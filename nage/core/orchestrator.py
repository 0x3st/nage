import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import List, Optional, Tuple

from pydantic import BaseModel

from ..errors import ContextError, StorageError, ToolError
from ..protocol import (
    AssistantDecision,
    DecisionStatus,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    Usage,
)
from ..providers import (
    BaseProvider,
    MessageRole,
    ProviderMessage,
    ProviderRequest,
    ProviderToolDefinition,
)
from ..storage.actions import ActionRecord, ActionStore
from ..storage.context_manifests import ContextManifestStore
from ..storage.requests import RequestStore
from ..tools import (
    RiskLevel,
    ToolContext,
    ToolOutcome,
    ToolRegistrySnapshot,
    ToolSpec,
)
from .confirmation import ActionPlan, ConfirmationService
from .context import ContextManifest, estimate_tool_schema_tokens
from .continuation import ContinuationInvocation, ContinuationService
from .memory import MemoryInvocation, MemoryProposalPolicy
from .policy import PolicyDecisionKind, PolicyEngine


class OrchestrationStatus(str, Enum):
    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"
    MODEL_ERROR = "model_error"
    CANCELLED = "cancelled"
    POLICY_DENIED = "policy_denied"
    LIMIT_REACHED = "limit_reached"


@dataclass(frozen=True)
class OrchestratorLimits:
    max_model_rounds: int = 4
    max_tool_calls: int = 8
    max_mutating_calls: int = 1

    def __post_init__(self) -> None:
        if self.max_model_rounds <= 0:
            raise ValueError("max_model_rounds must be positive")
        if self.max_tool_calls <= 0:
            raise ValueError("max_tool_calls must be positive")
        if self.max_mutating_calls < 0:
            raise ValueError("max_mutating_calls cannot be negative")


@dataclass(frozen=True)
class OrchestrationResult:
    status: OrchestrationStatus
    decision: AssistantDecision
    tool_results: Tuple[ToolResult, ...]
    model_rounds: int
    usage: Usage
    memory_notices: Tuple[str, ...] = ()
    continuation_notices: Tuple[str, ...] = ()


@dataclass(frozen=True)
class _CallResult:
    result: ToolResult
    stop_status: Optional[OrchestrationStatus] = None
    stop_message: Optional[str] = None
    mutating_plan: bool = False


class Orchestrator:
    def __init__(
        self,
        provider: BaseProvider,
        registry: ToolRegistrySnapshot,
        policy: PolicyEngine,
        confirmation: ConfirmationService,
        action_store: Optional[ActionStore] = None,
        request_store: Optional[RequestStore] = None,
        context_manifest_store: Optional[ContextManifestStore] = None,
        memory_policy: Optional[MemoryProposalPolicy] = None,
        continuation_service: Optional[ContinuationService] = None,
        limits: Optional[OrchestratorLimits] = None,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.policy = policy
        self.confirmation = confirmation
        if context_manifest_store is not None and request_store is None:
            raise ValueError("A context manifest store requires a request store.")
        self.action_store = action_store
        self.request_store = request_store
        self.context_manifest_store = context_manifest_store
        self.memory_policy = memory_policy
        self.continuation_service = continuation_service
        self.limits = limits or OrchestratorLimits()

    def run(
        self,
        request: ProviderRequest,
        context: ToolContext,
        context_manifest: Optional[ContextManifest] = None,
        memory_invocation: Optional[MemoryInvocation] = None,
        continuation_invocation: Optional[ContinuationInvocation] = None,
    ) -> OrchestrationResult:
        provider_tools = tuple(
            ProviderToolDefinition(
                name=spec.name,
                description=spec.description,
                parameters=spec.argument_model.model_json_schema(),
            )
            for spec in self.registry.specs()
        )
        current_request = replace(request, tools=provider_tools)
        if context_manifest is not None:
            schema_tokens = estimate_tool_schema_tokens(provider_tools)
            if schema_tokens > context_manifest.reserved_tool_tokens:
                raise ContextError(
                    "Tool schemas exceed the context budget reserved for them."
                )
        all_results: List[ToolResult] = []
        total_tool_calls = 0
        mutating_calls = 0
        input_tokens = 0
        output_tokens = 0
        input_usage_seen = False
        output_usage_seen = False
        request_record_started = False
        memory_notices: List[str] = []
        continuation_notices: List[str] = []
        seen_memory_proposals = set()

        if (
            memory_invocation is not None
            and memory_invocation.request_id != context.request_id
        ):
            raise ContextError("Memory invocation belongs to a different request.")

        if (
            continuation_invocation is not None
            and continuation_invocation.envelope.request_id != context.request_id
        ):
            raise ContextError(
                "Continuation invocation belongs to a different request."
            )

        if self.request_store is not None:
            self.request_store.begin(
                request_id=context.request_id,
                request_summary=None,
                scope_key=str(context.working_directory),
                provider=type(self.provider).__name__,
                model=request.model,
            )
            request_record_started = True

        try:
            if context_manifest is not None:
                self._store_context_manifest(context, context_manifest)
            for model_round in range(1, self.limits.max_model_rounds + 1):
                provider_result = self.provider.complete(current_request)
                usage = provider_result.usage
                if usage.input_tokens is not None:
                    input_tokens += usage.input_tokens
                    input_usage_seen = True
                if usage.output_tokens is not None:
                    output_tokens += usage.output_tokens
                    output_usage_seen = True

                decision = provider_result.decision
                self._process_memory_proposals(
                    decision=decision,
                    invocation=memory_invocation,
                    seen=seen_memory_proposals,
                    notices=memory_notices,
                )
                self._process_continuation(
                    decision=decision,
                    invocation=continuation_invocation,
                    notices=continuation_notices,
                )
                if decision.status != DecisionStatus.TOOL_CALL:
                    status = self._status_for_decision(decision)
                    return self._complete(
                        status=status,
                        decision=decision,
                        tool_results=all_results,
                        model_rounds=model_round,
                        input_tokens=input_tokens if input_usage_seen else None,
                        output_tokens=output_tokens if output_usage_seen else None,
                        request_id=context.request_id
                        if request_record_started
                        else None,
                        memory_notices=memory_notices,
                        continuation_notices=continuation_notices,
                    )

                if (
                    total_tool_calls + len(decision.tool_calls)
                    > self.limits.max_tool_calls
                ):
                    return self._local_stop(
                        status=OrchestrationStatus.LIMIT_REACHED,
                        message="Tool-call limit reached before execution.",
                        tool_results=all_results,
                        model_rounds=model_round,
                        input_tokens=input_tokens if input_usage_seen else None,
                        output_tokens=output_tokens if output_usage_seen else None,
                        request_id=context.request_id
                        if request_record_started
                        else None,
                        memory_notices=memory_notices,
                        continuation_notices=continuation_notices,
                    )

                assistant_message = provider_result.assistant_message
                if not assistant_message.tool_calls:
                    assistant_message = replace(
                        assistant_message,
                        tool_calls=tuple(decision.tool_calls),
                    )
                messages = current_request.messages + (assistant_message,)

                for call in decision.tool_calls:
                    total_tool_calls += 1
                    call_result = self._execute_call(
                        call,
                        context,
                        allow_mutation=(
                            mutating_calls < self.limits.max_mutating_calls
                        ),
                    )
                    all_results.append(call_result.result)
                    if call_result.mutating_plan:
                        mutating_calls += 1
                    if call_result.stop_status is not None:
                        return self._local_stop(
                            status=call_result.stop_status,
                            message=call_result.stop_message
                            or call_result.result.summary,
                            tool_results=all_results,
                            model_rounds=model_round,
                            input_tokens=input_tokens if input_usage_seen else None,
                            output_tokens=output_tokens if output_usage_seen else None,
                            request_id=context.request_id
                            if request_record_started
                            else None,
                            memory_notices=memory_notices,
                            continuation_notices=continuation_notices,
                        )
                    messages += (
                        ProviderMessage(
                            role=MessageRole.TOOL,
                            content=call_result.result.model_dump_json(),
                            tool_call_id=call.id,
                        ),
                    )

                current_request = replace(current_request, messages=messages)

            return self._local_stop(
                status=OrchestrationStatus.LIMIT_REACHED,
                message="Model-round limit reached.",
                tool_results=all_results,
                model_rounds=self.limits.max_model_rounds,
                input_tokens=input_tokens if input_usage_seen else None,
                output_tokens=output_tokens if output_usage_seen else None,
                request_id=context.request_id if request_record_started else None,
                memory_notices=memory_notices,
                continuation_notices=continuation_notices,
            )
        except Exception:
            if request_record_started and self.request_store is not None:
                try:
                    self.request_store.finish(
                        context.request_id,
                        status="failed",
                        usage=Usage(
                            input_tokens=input_tokens if input_usage_seen else None,
                            output_tokens=output_tokens if output_usage_seen else None,
                        ),
                    )
                except Exception:
                    # Preserve the original failure. Storage recovery and
                    # diagnostics can inspect the unfinished request record.
                    pass
            raise

    def _process_memory_proposals(
        self,
        decision: AssistantDecision,
        invocation: Optional[MemoryInvocation],
        seen: set,
        notices: List[str],
    ) -> None:
        proposals = []
        for proposal in decision.memory_proposals:
            fingerprint = proposal.model_dump_json()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            proposals.append(proposal)
        if not proposals:
            return
        if self.memory_policy is None or invocation is None:
            return
        try:
            outcomes = self.memory_policy.process(proposals, invocation)
        except StorageError:
            notices.append("Memory update failed in local storage.")
            return
        notices.extend(outcome.message for outcome in outcomes)

    def _process_continuation(
        self,
        decision: AssistantDecision,
        invocation: Optional[ContinuationInvocation],
        notices: List[str],
    ) -> None:
        if self.continuation_service is None or invocation is None:
            return
        try:
            outcome = self.continuation_service.handle_decision(
                decision,
                invocation.envelope,
                attached_task_id=invocation.attached_task_id,
            )
        except StorageError:
            notices.append("Continuation update failed in local storage.")
            return
        if outcome is not None:
            notices.append(outcome.message)

    def _store_context_manifest(
        self,
        context: ToolContext,
        manifest: ContextManifest,
    ) -> None:
        if manifest.request_id != context.request_id:
            raise ContextError("Context manifest belongs to a different request.")
        if self.context_manifest_store is None:
            return
        mapping = manifest.to_mapping()
        entries = mapping["entries"]
        if not isinstance(entries, list):
            raise ValueError("Context manifest entries are invalid.")
        self.context_manifest_store.save(
            request_id=manifest.request_id,
            budget_tokens=manifest.budget_tokens,
            estimated_prompt_tokens=manifest.estimated_prompt_tokens,
            reserved_output_tokens=manifest.reserved_output_tokens,
            reserved_tool_tokens=manifest.reserved_tool_tokens,
            entries=entries,
        )

    def _execute_call(
        self,
        call: ToolCall,
        context: ToolContext,
        allow_mutation: bool,
    ) -> _CallResult:
        try:
            spec = self.registry.resolve(call.name)
        except ToolError as exc:
            return _CallResult(
                result=ToolResult(
                    call_id=call.id,
                    status=ToolResultStatus.DENIED,
                    summary=str(exc),
                ),
                stop_status=OrchestrationStatus.POLICY_DENIED,
                stop_message=str(exc),
            )

        try:
            arguments = spec.validate_arguments(call.arguments)
        except ToolError as exc:
            return _CallResult(
                result=ToolResult(
                    call_id=call.id,
                    status=ToolResultStatus.FAILURE,
                    summary=str(exc),
                )
            )

        planning_context = self._bounded_tool_context(context, spec)
        try:
            decision = self.policy.evaluate(spec, arguments, planning_context)
        except ToolError as exc:
            return self._deny_validated_call(
                call=call,
                context=context,
                spec=spec,
                arguments=arguments,
                reason=str(exc),
                error_code="policy_evaluation_failed",
            )

        if decision.kind == PolicyDecisionKind.DENY:
            return self._deny_validated_call(
                call=call,
                context=context,
                spec=spec,
                arguments=arguments,
                reason=decision.reason,
                error_code="policy_denied",
            )

        try:
            state = spec.relevant_state(arguments, planning_context)
            plan = ActionPlan.build(
                call_id=call.id,
                spec=spec,
                arguments=arguments,
                risk=decision.risk,
                context=planning_context,
                relevant_state=state,
            )
        except ToolError as exc:
            return self._deny_validated_call(
                call=call,
                context=context,
                spec=spec,
                arguments=arguments,
                reason=str(exc),
                error_code="plan_validation_failed",
            )
        mutating = decision.risk != RiskLevel.READ_ONLY

        if mutating and not allow_mutation:
            action = self._begin_action(
                context,
                spec,
                arguments,
                plan,
                confirmation_status="not_requested",
            )
            summary = "Mutating-tool limit reached; this action was not executed."
            self._finish_action(
                action,
                execution_status="denied",
                summary=summary,
                error_code="mutating_tool_limit",
            )
            return _CallResult(
                result=ToolResult(
                    call_id=call.id,
                    status=ToolResultStatus.DENIED,
                    summary=summary,
                ),
                stop_status=OrchestrationStatus.LIMIT_REACHED,
                stop_message=summary,
                mutating_plan=False,
            )

        if context.dry_run and mutating:
            action = self._begin_action(
                context,
                spec,
                arguments,
                plan,
                confirmation_status="dry_run",
            )
            summary = "Dry run only: {0}".format(plan.preview.summary)
            self._finish_action(
                action,
                execution_status="dry_run",
                summary=summary,
            )
            return _CallResult(
                result=ToolResult(
                    call_id=call.id,
                    status=ToolResultStatus.SUCCESS,
                    summary=summary,
                    data={"dry_run": True, "plan_hash": plan.plan_hash},
                ),
                mutating_plan=True,
            )

        confirmation_status = "not_required"
        if decision.kind == PolicyDecisionKind.CONFIRM:
            confirmation_error = None
            try:
                approved = self.confirmation.confirm(plan)
            except ToolError as exc:
                approved = False
                confirmation_error = str(exc)
            if not approved:
                action = self._begin_action(
                    context,
                    spec,
                    arguments,
                    plan,
                    confirmation_status=(
                        "failed" if confirmation_error else "declined"
                    ),
                )
                summary = confirmation_error or "User declined the planned action."
                self._finish_action(
                    action,
                    execution_status="cancelled",
                    summary=summary,
                    error_code="confirmation_failed"
                    if confirmation_error
                    else "cancelled",
                )
                return _CallResult(
                    result=ToolResult(
                        call_id=call.id,
                        status=ToolResultStatus.CANCELLED,
                        summary=summary,
                    ),
                    stop_status=OrchestrationStatus.CANCELLED,
                    stop_message="Action cancelled; nothing was changed.",
                    mutating_plan=True,
                )
            confirmation_status = "approved"

        action = self._begin_action(
            context,
            spec,
            arguments,
            plan,
            confirmation_status=confirmation_status,
        )

        validation_context = self._bounded_tool_context(context, spec)
        try:
            current_state = spec.relevant_state(arguments, validation_context)
            plan_matches = plan.matches(
                spec=spec,
                arguments=arguments,
                risk=decision.risk,
                relevant_state=current_state,
            )
        except ToolError as exc:
            summary = str(exc)
            self._finish_action(
                action,
                execution_status="failed",
                summary=summary,
                error_code="state_revalidation_failed",
            )
            return _CallResult(
                result=ToolResult(
                    call_id=call.id,
                    status=ToolResultStatus.FAILURE,
                    summary=summary,
                ),
                stop_status=OrchestrationStatus.POLICY_DENIED,
                stop_message=summary,
                mutating_plan=mutating,
            )
        if not plan_matches:
            summary = "Action state changed after preview; execution was stopped."
            self._finish_action(
                action,
                execution_status="failed",
                summary=summary,
                error_code="plan_changed",
            )
            return _CallResult(
                result=ToolResult(
                    call_id=call.id,
                    status=ToolResultStatus.FAILURE,
                    summary=summary,
                ),
                stop_status=OrchestrationStatus.POLICY_DENIED,
                stop_message=summary,
                mutating_plan=mutating,
            )

        execution_context = self._bounded_tool_context(context, spec)
        try:
            outcome = spec.execute(arguments, execution_context)
            if not isinstance(outcome, ToolOutcome):
                raise ToolError("Tool returned an invalid outcome.")
            result = ToolResult(
                call_id=call.id,
                status=ToolResultStatus.SUCCESS,
                summary=outcome.summary,
                data=dict(outcome.data),
                warnings=list(outcome.warnings),
                artifact_ids=list(outcome.artifact_ids),
            )
            encoded_result = result.model_dump_json().encode("utf-8")
            if len(encoded_result) > spec.max_output_bytes:
                raise ToolError("Tool result exceeds its configured output limit.")
        except ToolError as exc:
            self._finish_action(
                action,
                execution_status="failed",
                summary=str(exc),
                error_code=exc.code,
            )
            return _CallResult(
                result=ToolResult(
                    call_id=call.id,
                    status=ToolResultStatus.FAILURE,
                    summary=str(exc),
                ),
                mutating_plan=mutating,
            )
        except Exception as exc:
            summary = "Tool failed unexpectedly: {0}".format(type(exc).__name__)
            self._finish_action(
                action,
                execution_status="failed",
                summary=summary,
                error_code="unexpected_tool_error",
            )
            return _CallResult(
                result=ToolResult(
                    call_id=call.id,
                    status=ToolResultStatus.FAILURE,
                    summary=summary,
                ),
                mutating_plan=mutating,
            )

        self._finish_action(
            action,
            execution_status="success",
            summary=result.summary,
        )
        return _CallResult(result=result, mutating_plan=mutating)

    @staticmethod
    def _bounded_tool_context(
        context: ToolContext,
        spec: ToolSpec,
    ) -> ToolContext:
        deadline = time.monotonic() + spec.timeout_seconds
        if context.deadline_monotonic is not None:
            deadline = min(deadline, context.deadline_monotonic)
        return replace(context, deadline_monotonic=deadline)

    def _deny_validated_call(
        self,
        call: ToolCall,
        context: ToolContext,
        spec: ToolSpec,
        arguments: BaseModel,
        reason: str,
        error_code: str,
    ) -> _CallResult:
        plan = ActionPlan.denied(
            call_id=call.id,
            spec=spec,
            arguments=arguments,
            reason=reason,
        )
        action = self._begin_action(
            context,
            spec,
            arguments,
            plan,
            confirmation_status="denied",
        )
        self._finish_action(
            action,
            execution_status="denied",
            summary=reason,
            error_code=error_code,
        )
        return _CallResult(
            result=ToolResult(
                call_id=call.id,
                status=ToolResultStatus.DENIED,
                summary=reason,
            ),
            stop_status=OrchestrationStatus.POLICY_DENIED,
            stop_message=reason,
            mutating_plan=False,
        )

    def _begin_action(
        self,
        context: ToolContext,
        spec: ToolSpec,
        arguments: BaseModel,
        plan: ActionPlan,
        confirmation_status: str,
    ) -> Optional[ActionRecord]:
        if self.action_store is None:
            return None
        return self.action_store.begin(
            request_id=context.request_id if self.request_store is not None else None,
            tool_name=spec.name,
            arguments=arguments.model_dump(mode="json"),
            plan_hash=plan.plan_hash,
            risk=plan.risk,
            confirmation_status=confirmation_status,
        )

    def _finish_action(
        self,
        action: Optional[ActionRecord],
        execution_status: str,
        summary: str,
        error_code: Optional[str] = None,
    ) -> None:
        if action is None or self.action_store is None:
            return
        self.action_store.finish(
            action.action_id,
            execution_status=execution_status,
            result_summary=summary,
            error_code=error_code,
        )

    def _complete(
        self,
        status: OrchestrationStatus,
        decision: AssistantDecision,
        tool_results: List[ToolResult],
        model_rounds: int,
        input_tokens: Optional[int],
        output_tokens: Optional[int],
        request_id: Optional[str],
        memory_notices: List[str],
        continuation_notices: List[str],
    ) -> OrchestrationResult:
        usage = Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=(
                None
                if input_tokens is None and output_tokens is None
                else (input_tokens or 0) + (output_tokens or 0)
            ),
        )
        if request_id is not None and self.request_store is not None:
            self.request_store.finish(
                request_id,
                status=status.value,
                usage=usage,
            )
        return OrchestrationResult(
            status=status,
            decision=decision,
            tool_results=tuple(tool_results),
            model_rounds=model_rounds,
            usage=usage,
            memory_notices=tuple(memory_notices),
            continuation_notices=tuple(continuation_notices),
        )

    def _local_stop(
        self,
        status: OrchestrationStatus,
        message: str,
        tool_results: List[ToolResult],
        model_rounds: int,
        input_tokens: Optional[int],
        output_tokens: Optional[int],
        request_id: Optional[str],
        memory_notices: List[str],
        continuation_notices: List[str],
    ) -> OrchestrationResult:
        return self._complete(
            status=status,
            decision=AssistantDecision(
                status=DecisionStatus.FINAL,
                message=message,
            ),
            tool_results=tool_results,
            model_rounds=model_rounds,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_id=request_id,
            memory_notices=memory_notices,
            continuation_notices=continuation_notices,
        )

    @staticmethod
    def _status_for_decision(
        decision: AssistantDecision,
    ) -> OrchestrationStatus:
        if decision.status == DecisionStatus.NEEDS_INPUT:
            return OrchestrationStatus.NEEDS_INPUT
        if decision.status == DecisionStatus.ERROR:
            return OrchestrationStatus.MODEL_ERROR
        return OrchestrationStatus.COMPLETED
