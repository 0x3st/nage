import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from ..memory import significant_tokens
from ..protocol import AssistantDecision, DecisionStatus, Sensitivity
from ..storage import ContinuationRecord, ContinuationStore
from .context import (
    BuiltContext,
    ContextSegment,
    ContextSource,
    RequestEnvelope,
    TrustLevel,
)


class ContinuationMatchKind(str, Enum):
    NONE = "none"
    MATCH = "match"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class ContinuationMatch:
    kind: ContinuationMatchKind
    record: Optional[ContinuationRecord] = None
    candidates: Tuple[Tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ContinuationDecision:
    message: str
    task_id: Optional[str] = None


@dataclass(frozen=True)
class ContinuationInvocation:
    envelope: RequestEnvelope
    attached_task_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.attached_task_id is not None and not re.fullmatch(
            r"task-[0-9a-f]{12}", self.attached_task_id
        ):
            raise ValueError("attached continuation task ID is invalid")

    @classmethod
    def from_built_context(
        cls,
        envelope: RequestEnvelope,
        context: BuiltContext,
    ) -> "ContinuationInvocation":
        if context.manifest.request_id != envelope.request_id:
            raise ValueError("continuation context belongs to another request")
        task_ids = tuple(
            segment.source_reference
            for segment in context.selected_segments
            if segment.source_type == ContextSource.CONTINUATION
        )
        if len(task_ids) > 1:
            raise ValueError("only one continuation can be attached")
        return cls(
            envelope=envelope,
            attached_task_id=task_ids[0] if task_ids else None,
        )


class ContinuationService:
    def __init__(self, store: ContinuationStore) -> None:
        self.store = store

    def match(self, envelope: RequestEnvelope) -> ContinuationMatch:
        if envelope.options.fresh:
            return ContinuationMatch(ContinuationMatchKind.NONE)
        try:
            self.store.cleanup()
        except Exception:
            # Opportunistic lifecycle cleanup must not block a request.
            pass
        candidates = self._candidates(envelope)
        if not candidates:
            return ContinuationMatch(ContinuationMatchKind.NONE)
        ranked = sorted(
            ((self._score(record, envelope), record) for record in candidates),
            key=lambda item: (-item[0], item[1].task_id),
        )
        top_score, top = ranked[0]
        threshold = 35 if top.status in {"hot", "pinned"} else 55
        if top_score < threshold:
            return ContinuationMatch(ContinuationMatchKind.NONE)
        if len(ranked) > 1:
            second_score, second = ranked[1]
            second_threshold = 35 if second.status in {"hot", "pinned"} else 55
            if second_score >= second_threshold and top_score - second_score <= 10:
                return ContinuationMatch(
                    ContinuationMatchKind.AMBIGUOUS,
                    candidates=tuple(
                        (record.task_id, record.label)
                        for score, record in ranked[:3]
                        if score >= min(threshold, second_threshold)
                    ),
                )
        return ContinuationMatch(ContinuationMatchKind.MATCH, record=top)

    @staticmethod
    def ambiguity_prompt(match: ContinuationMatch) -> Optional[str]:
        if match.kind != ContinuationMatchKind.AMBIGUOUS:
            return None
        choices = "; ".join(
            "{0} ({1})".format(task_id, label) for task_id, label in match.candidates
        )
        return "Multiple pending tasks match. Specify one task ID: {0}".format(choices)

    def context_segment(self, match: ContinuationMatch) -> Optional[ContextSegment]:
        if match.kind != ContinuationMatchKind.MATCH or match.record is None:
            return None
        record = match.record
        content = json.dumps(
            {
                "task_id": record.task_id,
                "label": record.label,
                "intent": record.intent,
                "known": dict(record.known),
                "missing": list(record.missing),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return ContextSegment(
            source_type=ContextSource.CONTINUATION,
            source_reference=record.task_id,
            trust_level=TrustLevel.POLICY_SELECTED,
            priority=90,
            content=content,
            reason_selected=(
                "Strong host-side continuation match for the current scope."
            ),
        )

    def handle_decision(
        self,
        decision: AssistantDecision,
        envelope: RequestEnvelope,
        attached_task_id: Optional[str] = None,
    ) -> Optional[ContinuationDecision]:
        if decision.status == DecisionStatus.NEEDS_INPUT:
            proposal = decision.continuation
            if proposal is None:
                return None
            if proposal.sensitivity == Sensitivity.SECRET:
                return ContinuationDecision(
                    "Continuation was not saved because it contains secret data."
                )
            sensitivity = (
                "sensitive"
                if proposal.sensitivity == Sensitivity.SENSITIVE
                else "normal"
            )
            if attached_task_id is not None:
                existing = self.store.get(attached_task_id)
            else:
                existing = None
            expected_scope = self._scope(envelope)
            if (
                existing is not None
                and (
                    existing.scope_type,
                    existing.scope_key,
                )
                != expected_scope
            ):
                existing = None
            if existing is not None:
                record = self.store.update(
                    existing.task_id,
                    label=proposal.label,
                    intent=proposal.intent,
                    known=proposal.known,
                    missing=proposal.missing,
                    sensitivity=sensitivity,
                )
                return ContinuationDecision(
                    "Updated continuation {0}: {1}".format(
                        record.task_id, record.label
                    ),
                    record.task_id,
                )
            scope_type, scope_key = self._scope(envelope)
            record = self.store.create(
                label=proposal.label,
                intent=proposal.intent,
                known=proposal.known,
                missing=proposal.missing,
                scope_type=scope_type,
                scope_key=scope_key,
                terminal_key=envelope.terminal_identity,
                sensitivity=sensitivity,
            )
            return ContinuationDecision(
                "Saved continuation {0}: {1}".format(record.task_id, record.label),
                record.task_id,
            )
        if decision.status == DecisionStatus.FINAL and attached_task_id is not None:
            if self.store.complete(attached_task_id):
                return ContinuationDecision(
                    "Completed continuation {0}.".format(attached_task_id),
                    attached_task_id,
                )
        return None

    def _candidates(self, envelope: RequestEnvelope) -> Tuple[ContinuationRecord, ...]:
        if envelope.repository_root is not None:
            repository = self.store.candidates(
                "repository", str(envelope.repository_root), limit=20
            )
            if repository:
                return repository
        return self.store.candidates(
            "workspace", str(envelope.working_directory), limit=20
        )

    @staticmethod
    def _scope(envelope: RequestEnvelope) -> Tuple[str, str]:
        if envelope.repository_root is not None:
            return "repository", str(envelope.repository_root)
        return "workspace", str(envelope.working_directory)

    @staticmethod
    def _score(record: ContinuationRecord, envelope: RequestEnvelope) -> int:
        request = envelope.request.casefold()
        if record.task_id.casefold() in request:
            return 1000
        score = 25 if record.status == "hot" else 20 if record.status == "pinned" else 0
        compatibility = 0
        if envelope.terminal_identity and record.terminal_key:
            score += 20 if envelope.terminal_identity == record.terminal_key else -15
        if record.scope_type == "repository":
            score += 20
        else:
            score += 10
        query_tokens = significant_tokens(envelope.request)
        candidate_tokens = significant_tokens(
            "{0} {1} {2}".format(
                record.label,
                record.intent.replace(".", " "),
                " ".join(record.missing),
            )
        )
        if query_tokens and candidate_tokens:
            overlap = len(query_tokens.intersection(candidate_tokens))
            if overlap >= 2 or (
                len(query_tokens) <= 2 and overlap == len(query_tokens)
            ):
                lexical_score = int(40 * overlap / max(len(query_tokens), 1))
                score += lexical_score
                compatibility += lexical_score
        if "recipient" in record.missing and re.search(
            r"[^\s@]+@[^\s@]+\.[^\s@]+", envelope.request
        ):
            score += 25
            compatibility += 25
        if "path" in record.missing and (
            "/" in envelope.request or "\\" in envelope.request
        ):
            score += 20
            compatibility += 20
        return score if compatibility > 0 else -1000
