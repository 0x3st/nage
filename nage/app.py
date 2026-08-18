import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Optional, Tuple

from .config import AppConfig, ConfigStore
from .core import (
    ConfirmationService,
    ContextBuilder,
    ContinuationInvocation,
    ContinuationMatchKind,
    ContinuationService,
    MemoryContextService,
    MemoryInvocation,
    MemoryProposalPolicy,
    OrchestrationResult,
    OrchestrationStatus,
    Orchestrator,
    PolicyEngine,
    ProjectContextCollector,
    RequestEnvelope,
    RequestOptions,
    TokenEstimator,
    estimate_tool_schema_tokens,
)
from .core.context import ContextManifest
from .core.stdin import StdinReader
from .errors import StorageError
from .paths import AppPaths
from .protocol import AssistantDecision, DecisionStatus, Usage, decision_json_schema
from .providers import (
    BaseProvider,
    OpenAICompatibleProvider,
    ProviderRequest,
    ProviderToolDefinition,
    RetryingProvider,
)
from .storage import (
    ActionStore,
    ArtifactStore,
    ContextManifestStore,
    ContinuationStore,
    Database,
    LegacyMigrationCoordinator,
    MemoryStore,
    RequestStore,
)
from .tools import ToolContext
from .tools.builtin import BUILTIN_PERMISSIONS, build_builtin_registry


@dataclass(frozen=True)
class AppRunResult:
    orchestration: OrchestrationResult
    context_manifest: Optional[ContextManifest]


class NageApp:
    def __init__(
        self,
        paths: Optional[AppPaths] = None,
        provider: Optional[BaseProvider] = None,
        confirmation: Optional[ConfirmationService] = None,
        invocation_timeout_seconds: float = 120.0,
    ) -> None:
        if invocation_timeout_seconds <= 0:
            raise ValueError("invocation timeout must be positive")
        self.paths = paths or AppPaths.default()
        self.injected_provider = provider
        self.confirmation = confirmation
        self.invocation_timeout_seconds = invocation_timeout_seconds
        self.database = Database(self.paths.database_file)
        self.config_store = ConfigStore(self.paths)
        self.memories: Optional[MemoryStore] = None
        self.continuations: Optional[ContinuationStore] = None
        self.artifacts: Optional[ArtifactStore] = None
        self._prepared = False

    def prepare(self) -> AppConfig:
        self.paths.ensure()
        self.database.initialize()
        self.memories = MemoryStore(self.database)
        self.continuations = ContinuationStore(self.database)
        self.artifacts = ArtifactStore(self.database, self.paths)
        migration = LegacyMigrationCoordinator(
            self.paths,
            self.database,
            self.config_store,
            self.memories,
        )
        migration.migrate()
        self._prepared = True
        return self.config_store.load()

    def execute(
        self,
        request_text: str,
        config: AppConfig,
        working_directory: Path,
        options: Optional[RequestOptions] = None,
        stdin_stream: Optional[BinaryIO] = None,
        terminal_identity: Optional[str] = None,
    ) -> AppRunResult:
        if not self._prepared:
            raise RuntimeError("NageApp.prepare() must run before execute().")
        if (
            self.memories is None
            or self.continuations is None
            or self.artifacts is None
        ):
            raise RuntimeError("Nage application services are unavailable.")
        options = options or RequestOptions()
        stdin_context = None
        if stdin_stream is not None:
            stdin_context = StdinReader(self.artifacts).read(stdin_stream)
        envelope = RequestEnvelope.create(
            request=request_text,
            working_directory=working_directory,
            terminal_identity=terminal_identity,
            stdin=stdin_context,
            options=options,
        )

        memory_context = MemoryContextService(
            self.memories,
            globally_enabled=config.memory_enabled,
        ).select(envelope)
        continuation_service = ContinuationService(self.continuations)
        continuation_match = continuation_service.match(envelope)
        if continuation_match.kind == ContinuationMatchKind.AMBIGUOUS:
            message = continuation_service.ambiguity_prompt(continuation_match) or (
                "Multiple pending tasks match this request."
            )
            return AppRunResult(
                orchestration=OrchestrationResult(
                    status=OrchestrationStatus.NEEDS_INPUT,
                    decision=AssistantDecision(
                        status=DecisionStatus.FINAL,
                        message=message,
                    ),
                    tool_results=(),
                    model_rounds=0,
                    usage=Usage(),
                ),
                context_manifest=None,
            )

        candidates = list(memory_context.segments)
        continuation_segment = continuation_service.context_segment(continuation_match)
        if continuation_segment is not None:
            candidates.append(continuation_segment)
        explicit_paths = self._explicit_paths(envelope)
        include_manifests = self._needs_project_manifest(envelope.request)
        candidates.extend(
            ProjectContextCollector().collect(
                envelope,
                explicit_paths=explicit_paths,
                include_manifests=include_manifests,
            )
        )

        registry = build_builtin_registry(memory_store=self.memories).snapshot()
        provider_tools = tuple(
            ProviderToolDefinition(
                name=spec.name,
                description=spec.description,
                parameters=spec.argument_model.model_json_schema(),
            )
            for spec in registry.specs()
        )
        context_builder = ContextBuilder(estimator=TokenEstimator())
        built_context = context_builder.build(
            envelope,
            candidates,
            tool_schema_tokens=estimate_tool_schema_tokens(provider_tools),
        )
        memory_invocation = MemoryInvocation.from_built_context(
            envelope,
            built_context,
            enabled=config.memory_enabled,
        )
        continuation_invocation = ContinuationInvocation.from_built_context(
            envelope,
            built_context,
        )

        provider = self.injected_provider or RetryingProvider(
            OpenAICompatibleProvider(
                api_key=config.api_key,
                base_url=config.endpoint,
            )
        )
        confirmation = self.confirmation or ConfirmationService(lambda plan: False)
        orchestrator = Orchestrator(
            provider=provider,
            registry=registry,
            policy=PolicyEngine(BUILTIN_PERMISSIONS),
            confirmation=confirmation,
            action_store=ActionStore(self.database),
            request_store=RequestStore(self.database),
            context_manifest_store=ContextManifestStore(self.database),
            memory_policy=MemoryProposalPolicy(
                self.memories,
                globally_enabled=config.memory_enabled,
            ),
            continuation_service=continuation_service,
        )
        request = ProviderRequest(
            model=config.model,
            messages=built_context.messages,
            tools=provider_tools,
            response_schema=decision_json_schema(),
        )
        deadline = time.monotonic() + self.invocation_timeout_seconds
        orchestration = orchestrator.run(
            request,
            ToolContext(
                request_id=envelope.request_id,
                working_directory=envelope.working_directory,
                dry_run=options.dry_run,
                deadline_monotonic=deadline,
                artifact_writer=self.artifacts,
                protected_paths=(
                    self.paths.root,
                    self.paths.config_dir,
                    self.paths.cache_dir,
                ),
            ),
            context_manifest=built_context.manifest,
            memory_invocation=memory_invocation,
            continuation_invocation=continuation_invocation,
        )
        try:
            self.artifacts.cleanup_expired()
        except StorageError:
            pass
        return AppRunResult(
            orchestration=orchestration,
            context_manifest=built_context.manifest,
        )

    @staticmethod
    def _explicit_paths(envelope: RequestEnvelope) -> Tuple[str, ...]:
        try:
            tokens = shlex.split(envelope.request)
        except ValueError:
            return ()
        selected = []
        for token in tokens:
            candidate = token.strip("'\"`:,;()[]{}")
            if not candidate or candidate.startswith("-"):
                continue
            path = Path(candidate)
            if not path.is_absolute():
                path = envelope.working_directory / path
            try:
                resolved = path.resolve(strict=True)
                root = envelope.repository_root or envelope.working_directory
                resolved.relative_to(root)
                selected.append(candidate)
            except (OSError, ValueError):
                continue
            if len(selected) >= 5:
                break
        return tuple(selected)

    @staticmethod
    def _needs_project_manifest(request: str) -> bool:
        lowered = request.casefold()
        terms = (
            "project",
            "repository",
            "repo",
            "code",
            "build",
            "test",
            "dependency",
            "package",
        )
        return any(term in lowered for term in terms)
