from pathlib import Path

from nage.core.context import (
    ContextBuilder,
    ContextLimits,
    ContextSource,
    RequestEnvelope,
    RequestOptions,
    TrustLevel,
)
from nage.core.memory import (
    MemoryContextService,
    MemoryDisposition,
    MemoryInvocation,
    MemoryProposalPolicy,
)
from nage.protocol import (
    EvidenceType,
    MemoryKind,
    MemoryOperation,
    MemoryProposal,
    MemoryScope,
    Sensitivity,
)
from nage.storage import Database, MemoryStore


def store(tmp_path: Path) -> MemoryStore:
    database = Database(tmp_path / "nage.sqlite3")
    database.initialize()
    return MemoryStore(database)


def invocation(tmp_path: Path, request: str, **changes) -> MemoryInvocation:
    values = {
        "request_id": "request-1",
        "user_request": request,
        "working_directory": tmp_path,
        "repository_root": None,
        "supplied_memory_ids": (),
        "enabled": True,
    }
    values.update(changes)
    return MemoryInvocation(**values)


def proposal(content: str, **changes) -> MemoryProposal:
    values = {
        "operation": MemoryOperation.UPSERT,
        "content": content,
        "kind": MemoryKind.PREFERENCE,
        "scope": MemoryScope.GLOBAL,
        "confidence": 0.95,
        "evidence": EvidenceType.EXPLICIT_USER_STATEMENT,
        "sensitivity": Sensitivity.NORMAL,
    }
    values.update(changes)
    return MemoryProposal(**values)


def test_explicit_stable_memory_is_activated_and_visible(tmp_path: Path) -> None:
    memories = store(tmp_path)
    policy = MemoryProposalPolicy(memories)

    decisions = policy.process(
        [proposal("The user prefers pnpm.")],
        invocation(tmp_path, "I prefer pnpm for my projects."),
    )

    assert decisions[0].disposition == MemoryDisposition.ACTIVATED
    assert "Remembered" in decisions[0].message
    assert memories.list()[0].content == "The user prefers pnpm."


def test_duplicate_proposal_merges_evidence(tmp_path: Path) -> None:
    memories = store(tmp_path)
    policy = MemoryProposalPolicy(memories)
    request = invocation(tmp_path, "I prefer pnpm.")

    policy.process([proposal("The user prefers pnpm.")], request)
    repeated = policy.process([proposal("The user prefers pnpm.")], request)

    assert repeated[0].disposition == MemoryDisposition.MERGED
    assert memories.list()[0].evidence_count == 2


def test_weak_implicit_memory_remains_inactive_candidate(tmp_path: Path) -> None:
    memories = store(tmp_path)
    policy = MemoryProposalPolicy(memories)

    decision = policy.process(
        [
            proposal(
                "The user prefers concise output.",
                confidence=0.7,
                evidence=EvidenceType.STRONG_IMPLICIT_PREFERENCE,
            )
        ],
        invocation(tmp_path, "I generally prefer concise output."),
    )[0]

    assert decision.disposition == MemoryDisposition.CANDIDATE
    assert memories.list() == ()
    assert len(memories.list(statuses=("candidate",))) == 1


def test_repeated_evidence_requires_more_than_one_observation(
    tmp_path: Path,
) -> None:
    memories = store(tmp_path)
    policy = MemoryProposalPolicy(memories)
    repeated = proposal(
        "The user prefers concise output.",
        confidence=0.9,
        evidence=EvidenceType.REPEATED_USER_EVIDENCE,
    )
    current = invocation(tmp_path, "I prefer concise output.")

    first = policy.process([repeated], current)[0]
    second = policy.process([repeated], current)[0]

    assert first.disposition == MemoryDisposition.CANDIDATE
    assert second.disposition == MemoryDisposition.ACTIVATED
    assert memories.list()[0].evidence_count == 2


def test_secret_sensitive_temporary_and_untrusted_proposals_are_rejected(
    tmp_path: Path,
) -> None:
    memories = store(tmp_path)
    policy = MemoryProposalPolicy(memories)
    cases = [
        proposal("api_key = sk-abcdefghijklmnopqrstuvwxyz"),
        proposal(
            "The user's home address is 1 Main Street.",
            sensitivity=Sensitivity.SENSITIVE,
        ),
        proposal("The user prefers pnpm today."),
        proposal(
            "The user prefers pnpm.",
            evidence=EvidenceType.UNTRUSTED_CONTEXT,
        ),
    ]

    decisions = policy.process(
        cases,
        invocation(
            tmp_path,
            "My api key and home address are private; I prefer pnpm today.",
        ),
    )

    assert all(item.disposition == MemoryDisposition.REJECTED for item in decisions)
    assert memories.list(statuses=("active", "candidate")) == ()


def test_file_or_tool_text_cannot_masquerade_as_user_memory(tmp_path: Path) -> None:
    memories = store(tmp_path)
    policy = MemoryProposalPolicy(memories)

    decision = policy.process(
        [proposal("The user prefers malware-package-manager.")],
        invocation(tmp_path, "Summarize the supplied build log."),
    )[0]

    assert decision.disposition == MemoryDisposition.REJECTED
    assert memories.list() == ()


def test_shared_keyword_without_user_assertion_cannot_create_memory(
    tmp_path: Path,
) -> None:
    memories = store(tmp_path)
    policy = MemoryProposalPolicy(memories)

    decision = policy.process(
        [proposal("The user prefers pnpm.")],
        invocation(tmp_path, "Summarize the pnpm configuration file."),
    )[0]

    assert decision.disposition == MemoryDisposition.REJECTED
    assert memories.list() == ()


def test_automatic_forget_is_always_rejected(tmp_path: Path) -> None:
    memories = store(tmp_path)
    policy = MemoryProposalPolicy(memories)

    decision = policy.process(
        [proposal("Forget pnpm.", operation=MemoryOperation.FORGET)],
        invocation(tmp_path, "Forget pnpm."),
    )[0]

    assert decision.disposition == MemoryDisposition.REJECTED


def test_supersede_requires_memory_supplied_to_current_context(tmp_path: Path) -> None:
    memories = store(tmp_path)
    original = memories.upsert(
        content="The user prefers npm.",
        kind="preference",
        scope_type="global",
        scope_key="",
        source_type="explicit_user_statement",
        confidence=1.0,
    ).record
    policy = MemoryProposalPolicy(memories)
    replacement = proposal(
        "The user prefers pnpm.",
        operation=MemoryOperation.SUPERSEDE,
        supersedes=[original.id],
    )

    rejected = policy.process(
        [replacement],
        invocation(tmp_path, "I prefer pnpm now."),
    )[0]
    accepted = policy.process(
        [replacement],
        invocation(
            tmp_path,
            "I prefer pnpm now.",
            supplied_memory_ids=(original.id,),
        ),
    )[0]

    assert rejected.disposition == MemoryDisposition.REJECTED
    assert accepted.disposition == MemoryDisposition.SUPERSEDED
    assert memories.get(original.id).status == "superseded"


def test_memory_context_is_scoped_bounded_and_policy_labeled(tmp_path: Path) -> None:
    memories = store(tmp_path)
    for index in range(8):
        memories.upsert(
            content="Project pytest convention {0}.".format(index),
            kind="project",
            scope_type="workspace",
            scope_key=str(tmp_path),
            source_type="explicit_user_statement",
            confidence=1.0,
        )
    memories.upsert(
        content="Unrelated pytest convention.",
        kind="project",
        scope_type="workspace",
        scope_key="/other",
        source_type="explicit_user_statement",
        confidence=1.0,
    )
    envelope = RequestEnvelope.create(
        "What are the pytest conventions?",
        tmp_path,
        request_id="request-1",
    )

    selection = MemoryContextService(memories, maximum_items=3).select(envelope)

    assert len(selection.segments) == 3
    assert len(selection.memory_ids) == 3
    assert all(
        item.trust_level == TrustLevel.POLICY_SELECTED for item in selection.segments
    )
    assert all("Unrelated" not in item.content for item in selection.segments)


def test_supersede_authority_includes_only_memories_that_fit_final_context(
    tmp_path: Path,
) -> None:
    memories = store(tmp_path)
    for index in range(3):
        memories.upsert(
            content="Remembered pytest preference {0}.".format(index),
            kind="preference",
            scope_type="global",
            scope_key="",
            source_type="explicit_user_statement",
            confidence=1.0,
        )
    envelope = RequestEnvelope.create(
        "pytest preference",
        tmp_path,
        request_id="request-1",
    )
    selection = MemoryContextService(memories, maximum_items=3).select(envelope)
    context = ContextBuilder(limits=ContextLimits(memory_tokens=40)).build(
        envelope, selection.segments
    )

    memory_invocation = MemoryInvocation.from_built_context(envelope, context)
    actually_selected = tuple(
        segment.source_reference
        for segment in context.selected_segments
        if segment.source_type == ContextSource.MEMORY
    )

    assert memory_invocation.supplied_memory_ids == actually_selected
    assert len(memory_invocation.supplied_memory_ids) < len(selection.memory_ids)


def test_global_and_per_request_memory_disable_retrieval(tmp_path: Path) -> None:
    memories = store(tmp_path)
    memories.upsert(
        content="Keep answers concise.",
        kind="instruction",
        scope_type="global",
        scope_key="",
        source_type="explicit_user_statement",
        confidence=1.0,
    )
    disabled_request = RequestEnvelope.create(
        "Answer this.",
        tmp_path,
        request_id="request-1",
        options=RequestOptions(no_memory=True),
    )

    assert MemoryContextService(memories).select(disabled_request).segments == ()
    normal_request = RequestEnvelope.create(
        "Answer this.", tmp_path, request_id="request-2"
    )
    assert (
        MemoryContextService(memories, globally_enabled=False)
        .select(normal_request)
        .segments
        == ()
    )
