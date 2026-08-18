# Nage 2.0.0 Implementation Plan

**Target version:** Nage 2.0.0

**Status:** Active implementation baseline; `2.0.0a1` alpha prepared

**Source of truth:** [SCOPE-v2.0.0.md](SCOPE-v2.0.0.md)

**Current stable version:** Nage 1.2.1
**Current prerelease:** Nage 2.0.0a1

## 1. Purpose

This document turns the 2.0.0 product scope into an ordered engineering plan. It defines the target architecture, implementation phases, migration strategy, test requirements, release gates, and known risks.

The package version remained `1.2.1` during planning and changes only when a corresponding prerelease or release is ready to build and publish.

The intended release sequence is:

```text
1.2.1       current published release
2.0.0a1     first architecture preview
2.0.0a2     tool and context preview
2.0.0a3     memory and continuation preview
2.0.0b1     feature-complete beta
2.0.0rc1    release candidate
2.0.0       stable release
```

Additional alpha, beta, or release-candidate versions may be inserted when a gate is not yet satisfied.

## 2. Definition of done

Nage 2.0.0 is complete only when all of the following are true:

1. A user can express questions and supported actions through `nage <natural language>` without magic words or plugin syntax.
2. Plugins are exposed internally as typed tools and cannot bypass core policy.
3. The model can compose multiple reviewed tools already available when the invocation begins.
4. The invocation registry is immutable, and model-generated code, schemas, or dependencies cannot become live tools.
5. Read-only tools can gather useful system, file, and Git context.
6. At least one meaningful state-changing workflow can be planned, previewed, confirmed, executed, and reported safely.
7. Model output is validated through a versioned, provider-neutral protocol.
8. Complete history and complete memory are no longer sent on every request.
9. Context assembly is bounded, source-aware, and inspectable.
10. Memory is stored in SQLite, retrieved selectively, and controlled by a local policy.
11. Self-motivated memory proposals are visible, reversible, and unable to store secrets automatically.
12. Incomplete requests can continue across invocations through compact, expiring capsules without chat sessions.
13. Existing 1.x local data has a tested, idempotent, recoverable migration path.
14. Destructive, privileged, and external side effects cannot occur without confirmation of the exact plan.
15. Files, stdin, web content, and tool output cannot authorize actions, personal memory, or tool installation.
16. Provider, protocol, tool, migration, and partial-failure errors produce honest results and stable exit codes.
17. Unit, integration, CLI, security, migration, and context-evaluation suites pass on supported platforms.
18. Installers and the PyPI package report the same version and license.
19. User documentation explains the behavioral changes, local storage, privacy boundaries, generated-tool boundary, and migration from 1.x.

## 3. Current-state assessment

| Area | Nage 1.2.1 | Nage 2.0.0 target |
| --- | --- | --- |
| CLI | One-shot query through Click | Preserve one-shot interface; add core safety/context options |
| Model protocol | Prompt-generated JSON with mutually exclusive `type` values | Versioned response model with final, input, tool, memory, and continuation fields |
| Tool use | No tool runtime | Typed internal tool registry and bounded orchestration loop |
| Tool creation | Not supported | Compose a fixed set of reviewed tools; reject runtime-generated tools and schemas |
| Execution | Generated command copied automatically | Validated tools; preview and confirmation before side effects |
| Configuration | `SETT` JSON; model may request changes | Local validated configuration; model cannot directly mutate credentials or endpoints |
| Memory | Unstructured JSON string list | SQLite records with kind, scope, provenance, status, sensitivity, and lifecycle |
| History | Entire `HIST` list sent on each request | Separate audit records; targeted retrieval only |
| Continuity | Model interprets complete history | Small, scoped continuation capsules with hot/dormant lifecycle |
| Context | All memory/history concatenated into one prompt | Budgeted `ContextBuilder` with provenance and trust boundaries |
| File context | None | Progressive metadata, search, excerpt, and artifact retrieval |
| Streaming | Regex extracts a partially generated JSON string | Validated protocol first; safe rendering separated from planning |
| Error handling | Most JSON, network, and file errors escape | Typed errors, bounded retries, stable exit codes, no corrupt state |
| Tests | No repository test suite | Unit, integration, CLI, security, migration, and evaluation suites |
| Packaging | Versions duplicated and installer metadata hard-coded | One canonical package version consumed by Python and build workflows |

The 2.0 implementation should replace unsafe assumptions rather than layering tools onto the current `AIClient.request()` flow.

## 4. Target architecture

```text
CLI / stdin
    |
    v
RequestEnvelope
    |
    v
ContextBuilder ---- MemoryStore
    |               ContinuationStore
    |               ProjectIndex
    |               ArtifactStore
    v
Orchestrator <----> ProviderAdapter
    |
    +---- normalized AssistantDecision
    |
    v
ToolRegistry -> PolicyEngine -> Confirmation -> ToolExecutor
                                            |
                                            v
                                       ActionStore
    |
    v
ResultRenderer -> stdout/stderr and process exit code
```

### 4.1 Proposed package layout

The exact filenames may be adjusted during implementation, but responsibilities must remain separated.

```text
nage/
├── __init__.py
├── __main__.py
├── main.py                 # compatibility import or thin entry point
├── cli.py                  # Click parsing and process exit behavior
├── app.py                  # composition root
├── config.py               # validated application/provider configuration
├── paths.py                # injectable local paths
├── errors.py               # typed application errors
├── protocol/
│   ├── __init__.py
│   ├── models.py           # decisions, calls, results, proposals
│   └── validation.py       # parse and schema validation
├── providers/
│   ├── __init__.py
│   ├── base.py             # provider interface
│   └── openai_compatible.py
├── core/
│   ├── orchestrator.py     # bounded model/tool loop
│   ├── context.py          # ContextBuilder and context manifest
│   ├── policy.py           # host-owned permission decisions
│   ├── confirmation.py     # exact-plan approval
│   └── rendering.py        # terminal and optional JSON output
├── tools/
│   ├── __init__.py
│   ├── base.py             # ToolSpec, ToolContext, ToolResult
│   ├── registry.py
│   ├── system.py
│   ├── files.py
│   ├── git.py
│   ├── memory.py
│   └── context.py
├── storage/
│   ├── __init__.py
│   ├── database.py
│   ├── migrations.py
│   ├── memories.py
│   ├── continuations.py
│   ├── actions.py
│   └── artifacts.py
└── retrieval/
    ├── __init__.py
    ├── files.py
    └── ranking.py
```

The old `setting.py`, `parse.py`, and monolithic `ai_client.py` should remain only until their responsibilities have migrated and compatibility tests pass. They should not be expanded with new 2.0 behavior.

### 4.2 Dependency direction

Dependencies should flow inward:

```text
CLI and providers -> core interfaces -> tool/storage interfaces
```

The policy engine must not depend on model-provider classes. Tools must not import the CLI, renderer, or provider. Storage must not call a model. This keeps policy testable without network access and prevents plugins from controlling presentation or authorization.

## 5. Architecture decisions

### D-01 — Preserve the one-shot CLI

There is no chat loop or TUI. One process handles one request, any internal tool loop, an optional confirmation, and one final result.

### D-02 — Do not interpret magic words

The complete request is sent as ordinary language. `please`, `do`, and similar words do not toggle execution. The host protects against misclassification by gating side effects, not by searching for keywords.

### D-03 — Use typed function calling

Native provider tool calling is preferred. Providers without compatible native support use the same locally validated JSON schema. All provider responses normalize into shared protocol models before use.

A direct validation dependency should be declared rather than used transitively. Pydantic 2 is the preferred initial choice because it can define runtime models and generate tool/response JSON schemas. Python-version compatibility must be verified in the universal lock and CI matrix before merging.

### D-04 — Keep authority local

Tool risk, permission, confirmation, storage, and action status are host-owned. Protocol fields cannot override these decisions.

### D-05 — Use SQLite for canonical state

SQLite stores structured memories, continuations, action metadata, schema migrations, and retrieval metadata. FTS5 is used when available, with a deterministic fallback when unavailable. Markdown and JSON are export formats, not canonical stores.

### D-06 — Separate state classes

Durable memory, temporary continuation, action audit, project index, and large artifacts have independent schemas and retention. Audit history is not normal model context.

### D-07 — Use bounded context assembly

Every request receives a fresh context built under explicit budgets. Full `HIST`, full memory, full logs, and full repositories are forbidden as default prompt inputs.

### D-08 — Treat external content as untrusted data

Stdin, files, web pages, previous model text, and tool output receive an untrusted label in prompt construction. Only the current top-level user request can express user intent, and even it cannot bypass policy.

### D-09 — Defer embeddings

Initial memory and file retrieval use scope, exact matching, FTS5/BM25-style lexical ranking, manifests, symbols, and recency. Embeddings are not required for 2.0.0.

### D-10 — Prefer typed tools over shell execution

The initial release should implement common operations with Python APIs or narrowly scoped subprocess argument vectors. A general shell tool is not a prerequisite for 2.0.0 and must not be added before policy and confirmation are proven.

### D-11 — Compose existing tools; do not create live tools at runtime

The orchestrator may autonomously sequence tools from a registry snapshot created at the start of an invocation. Composition does not permit the model to define a schema or implementation, install a dependency, modify the registry, or turn generated code into an executable capability.

Nage 2.0.0 does not implement AI-authored tool installation or execution. If the user asks for possible tool source, it is handled as an ordinary inactive code draft under file-writing policy. It is never imported, registered, enabled, or executed automatically.

### D-12 — Preserve current Python support unless explicitly changed

The initial goal is to keep the current Python 3.8–3.13 matrix. Every new direct dependency and syntax choice must support that matrix. If maintaining Python 3.8 becomes infeasible, changing `requires-python` must be an explicit pre-alpha decision documented in release notes rather than an accidental dependency outcome.

### D-13 — Use one canonical package version

`pyproject.toml` should be the package-version source. Runtime version reporting should use `importlib.metadata.version("nage")`, with a development fallback when the package is not installed. Installer workflows must derive their version from package metadata instead of hard-coding `1.0`.

## 6. Protocol and runtime model

### 6.1 Normalized assistant decision

The internal response model should contain at least:

```json
{
  "protocol_version": "2.0",
  "status": "final",
  "message": "User-facing response",
  "tool_calls": [],
  "memory_proposals": [],
  "continuation": null
}
```

Allowed statuses are:

- `final`
- `needs_input`
- `tool_call`
- `error`

A response can include memory proposals while answering or calling tools. Memory is not a mutually exclusive response type.

### 6.2 Tool call

```json
{
  "id": "call_01",
  "name": "files.find_large",
  "arguments": {
    "root": ".",
    "minimum_size_mb": 100,
    "limit": 20
  }
}
```

Requirements:

- IDs are unique within an invocation.
- Calls may reference only the immutable registry snapshot created for that invocation.
- Unknown names, model-supplied tool definitions, and replacement schemas are rejected.
- Arguments validate against the host-registered tool schema.
- Tool results and model responses cannot modify the registry.
- Duplicate call IDs are never executed twice.
- Mutating calls execute sequentially in 2.0.0.
- Read-only calls may be parallelized later only after deterministic ordering and output limits are tested.

### 6.3 Tool result

```json
{
  "call_id": "call_01",
  "status": "success",
  "summary": "Found 20 files",
  "data": {},
  "warnings": [],
  "artifact_ids": []
}
```

The result status is produced by the executor, never copied from model prose.

### 6.4 Memory proposal

A proposal includes operation, normalized statement, kind, scope, confidence, evidence type, and sensitivity. The policy may reject it, keep it as a candidate, activate it, merge it, or supersede an existing memory.

The model cannot request secret storage or bulk deletion. A supersede proposal can reference only memories that were actually provided to the current model context.

### 6.5 Continuation proposal

A continuation proposal contains intent, known slots, missing slots, a short label, and sensitivity. The host adds task ID, terminal/workspace scope, lifecycle timestamps, and status.

### 6.6 Bounded loop

Initial defaults:

- Maximum model rounds: 4
- Maximum total tool calls: 8
- Maximum mutating tool calls: 1 plan at a time
- Maximum provider retries for transient failure: 2
- Maximum protocol-repair retry: 1
- Invocation deadline: configurable, with a conservative default

These values are configuration, not model inputs. Reaching a limit produces a partial result with a clear reason.

## 7. Storage design

### 7.1 Database operation

Use one SQLite database under `~/.nage/` with:

- Foreign keys enabled
- WAL mode where supported
- Busy timeout for concurrent terminal invocations
- Explicit transactions for migrations and multi-record state changes
- Incremental integer schema versions
- Parameterized SQL only
- Injectable database path for tests

No API key or provider secret belongs in this database.

### 7.2 Minimum schema

#### `schema_migrations`

- `version`
- `applied_at`

#### `memories`

- `id`
- `kind`
- `content`
- `normalized_content`
- `scope_type`
- `scope_key`
- `source_type`
- `source_reference`
- `confidence`
- `evidence_count`
- `sensitivity`
- `status`
- `created_at`
- `updated_at`
- `expires_at`
- `source_hash`

Add an FTS5 virtual table or equivalent fallback search path.

#### `continuations`

- `task_id`
- `label`
- `intent`
- `known_json`
- `missing_json`
- `scope_type`
- `scope_key`
- `terminal_key`
- `sensitivity`
- `status`
- `created_at`
- `updated_at`
- `implicit_until`
- `retain_until`

#### `requests`

- `request_id`
- `request_summary`
- `scope_key`
- `started_at`
- `finished_at`
- `status`
- `provider`
- `model`
- `input_tokens`
- `output_tokens`

Do not store full sensitive prompts by default.

#### `actions`

- `action_id`
- `request_id`
- `tool_name`
- `arguments_redacted_json`
- `plan_hash`
- `risk_level`
- `confirmation_status`
- `execution_status`
- `result_summary`
- `error_code`
- `started_at`
- `finished_at`

#### `artifacts`

- `artifact_id`
- `relative_path`
- `media_type`
- `size_bytes`
- `sha256`
- `sensitivity`
- `created_at`
- `expires_at`

#### `project_files`

- `scope_key`
- `relative_path`
- `size_bytes`
- `mtime_ns`
- `content_hash`
- `language`
- `index_status`
- `indexed_at`

A separate FTS table may index approved textual content or symbols. Raw file bodies do not need to be duplicated in the primary table.

### 7.3 Filesystem permissions

- Create the Nage data directory with user-only permissions where the platform permits.
- Create database, configuration, and artifact files without group/world access where supported.
- Never print credential values in diagnostics.
- Clean expired artifacts safely without following symlinks.

## 8. Migration from Nage 1.x

Migration must be automatic, idempotent, and recoverable.

### 8.1 Pre-migration

1. Detect legacy `SETT`, `MEMO`, and `HIST` files.
2. Validate that each is a regular file inside the expected data directory.
3. Create a timestamped backup before modifying or marking anything migrated.
4. Record a migration marker only after the database transaction commits.
5. Leave the backup available for rollback and document its location.

### 8.2 `MEMO` migration

- Parse the JSON list defensively.
- Ignore empty strings.
- Normalize whitespace.
- Deduplicate exact normalized entries.
- Import as `active` memories with `source_type = legacy_import` and normal sensitivity unless local detection requires review.
- Do not infer categories with high confidence from text alone; use `fact` or `uncategorized` when uncertain.
- Do not send imported memories to a model during migration.

### 8.3 `HIST` migration

Legacy history is not a reliable action log and must not become active prompt context.

- Preserve it in the backup.
- Optionally import entries into a clearly labeled legacy audit table or export artifact.
- Do not attach it to new requests automatically.
- Provide a documented way to delete the legacy copy.

### 8.4 `SETT` migration

- Preserve model and endpoint after validation.
- Preserve the existing API key only in a restricted local configuration path; never move it into memory or model context.
- Replace visible API-key input with `getpass` or another non-echoing mechanism.
- Reject malformed endpoint values and avoid sending the key to an endpoint change proposed only by the model.

### 8.5 Migration tests

Fixtures must cover:

- Missing files
- Empty files
- Valid current files
- Malformed JSON
- Duplicate and empty memories
- Interrupted migration
- Already migrated state
- Read-only data directory
- Concurrent first launch
- Rollback from backup

## 9. Context and retrieval design

### 9.1 Request envelope

The CLI creates a request envelope containing:

- Natural-language request
- Current working directory
- Repository root when detected
- TTY/terminal identity when available
- Stdin metadata and artifact reference
- Core options such as dry-run, fresh, and no-memory
- Request ID and start time

The current request is authoritative user input. Every other content source receives a lower trust label.

### 9.2 Context segment

Each context segment should record:

- Source type
- Source reference
- Trust level
- Priority
- Estimated tokens
- Truncation state
- Content hash where applicable
- Reason selected

The context manifest is stored for diagnostics without duplicating sensitive content unnecessarily.

### 9.3 Budgeting

Start with the scope targets:

- Ordinary requests: approximately 2,000–4,000 input tokens
- File/project requests: approximately 4,000–8,000 input tokens
- Memory: a few items and roughly 300 tokens
- Continuation: normally no more than 300 tokens
- Initial file excerpts: roughly 4,000 tokens

Budget calculation must reserve space for expected output, tool schemas, and later tool results. Use a provider tokenizer where available and a conservative local estimator otherwise. Record actual provider usage to calibrate estimates without recording full prompts.

### 9.4 File selection

Selection order:

1. Explicit user paths
2. Current workspace/repository metadata
3. Relevant manifests
4. Git changes when the request refers to current work
5. Filename and lexical search
6. Symbol/definition search
7. Ranked excerpts around matches
8. Additional ranges requested by a tool call

Initial operational limits should be configurable and conservative:

- Top 3–5 candidate files
- One or two excerpts per file
- Approximately 50–120 lines per excerpt
- No complete read of large files by default
- No recursive content upload

### 9.5 Ignore and sensitivity policy

- Respect `.gitignore` and optional `.nageignore`.
- Exclude `.git`, virtual environments, dependency trees, build output, caches, and common generated directories by default.
- Detect binary content before reading it as text.
- Deny likely secrets such as `.env`, private keys, credential files, browser data, and token stores unless an explicit narrowly scoped policy allows inspection.
- Resolve and validate paths to prevent traversal and symlink escape.
- Revalidate file identity immediately before a mutating action to reduce time-of-check/time-of-use risk.

### 9.6 Large stdin and artifacts

- Read stdin with a byte ceiling.
- Store oversized text as a temporary artifact.
- Select useful error sections, the tail, and bounded surrounding context.
- Reject or describe binary stdin rather than decoding it unsafely.
- Print a clear truncation notice when relevant.
- Expire and clean artifacts according to local policy.

## 10. Memory policy

### 10.1 Proposal sources

The model may propose memory from the current user request. It may not treat file, stdin, web, prior model, or tool text as a personal user statement.

### 10.2 Initial decision policy

| Proposal | Initial policy |
| --- | --- |
| Explicit stable preference, normal sensitivity | Activate after validation |
| Strong implicit stable preference, normal sensitivity | Activate and notify |
| Weak or one-time observation | Candidate or reject |
| Duplicate active memory | Merge evidence/update timestamp |
| Contradiction with supplied active memory | Supersede only with sufficient current user evidence |
| Temporary task detail | Keep in continuation, not durable memory |
| Secret or credential | Reject |
| Highly sensitive personal inference | Reject automatic storage |
| Statement originating only from untrusted context | Reject |

### 10.3 Retrieval

1. Select global behavioral instructions under a strict cap.
2. Select memories matching the repository/workspace scope.
3. Search remaining active memories through FTS5.
4. Apply a relevance threshold and top-k limit.
5. Include IDs and provenance so proposals can refer to supplied records.
6. Never include candidates, superseded records, or expired records by default.

### 10.4 User control

Natural requests should be able to invoke memory tools for:

- Inspect
- Search
- Correct
- Forget one item
- Clear after confirmation
- Export
- Disable for the current invocation

No plugin-specific command syntax is required. Core options such as `--no-memory` may remain available as deterministic privacy controls.

## 11. Continuation policy

### 11.1 Creation

Create a capsule only when the model returns `needs_input` with validated known and missing slots. Do not create a capsule for every answer.

### 11.2 Matching

Rank candidates using:

- Explicit task reference when present
- Same terminal identity
- Same repository or working directory
- Hot versus dormant state
- Compatibility with missing slot types
- Lexical/semantic similarity of the intent label
- Number of competing candidates

A low-confidence or ambiguous match is not attached automatically. Nage may ask whether the user means a specific prior task, but must not expose a chat UI.

### 11.3 Lifecycle

- Hot default: two hours
- Dormant retention: 30 days
- Sensitive capsule: shorter configurable limits
- Completion: deactivate immediately
- Expiration: remove from active continuation storage
- Confirmation: never stored

Lifecycle cleanup runs opportunistically at safe invocation boundaries. Cleanup failure must not block the user's request.

## 12. Tool and policy design

### 12.1 Shared tool types

`ToolSpec` should include:

- Name and model description
- Pydantic argument model
- Result model
- Static side-effect class
- Dynamic risk evaluator
- Required permissions
- Timeout and maximum output
- Preview builder
- Executor

`ToolContext` should include only approved services such as paths, stores, artifact writer, request identity, and cancellation/deadline state. It must not expose the provider API key or unrestricted application internals.

### 12.2 Risk classes

Suggested host-owned classes:

- `READ_ONLY`
- `LOCAL_WRITE`
- `DESTRUCTIVE`
- `PRIVILEGED`
- `EXTERNAL_EFFECT`
- `DENIED`

A tool can have a static minimum class and raise risk based on arguments. For example, reading an ordinary project file may be read-only, while reading a sensitive path is denied or confirmation-required.

### 12.3 Plan hash and confirmation

1. Normalize the tool name and validated arguments.
2. Include relevant current file hashes or metadata for race-sensitive operations.
3. Compute a plan hash.
4. Render the exact effect and risk.
5. Request confirmation.
6. Immediately before execution, revalidate policy and relevant filesystem state.
7. Execute only if the approved hash still matches.

Approval lives only in memory for the current invocation. There is no global “always allow” in the initial release.

### 12.4 Initial built-in tools

#### System

- Platform information
- Disk usage
- Memory usage
- Process listing/filtering

Use standard-library APIs where practical. If `psutil` is required for portable process and memory behavior, add it as a declared direct dependency and test wheels across the support matrix.

#### Files

Read-oriented:

- List directory
- Find by name/pattern
- Find large files
- Read approved range
- Inspect metadata
- Search text

Mutating:

- Create directory
- Copy
- Move/rename
- Move to trash where supported

Permanent deletion should either be deferred or require a separate, strictly gated tool. Consider a declared `Send2Trash` dependency to make recoverable deletion the default.

#### Git

- Repository root
- Status
- Diff/stat
- Log
- Branch information
- Show approved file/version

Invoke Git with an argument vector, fixed executable, repository working directory, timeout, and output ceiling. Initial Git tools are read-only.

#### Memory

- List/search
- Propose/upsert through policy
- Correct/supersede
- Forget
- Clear with confirmation
- Export

#### Context

- Inspect context manifest
- Inspect pending continuations
- Clear temporary context

### 12.5 Tool composition

The orchestrator can solve a request by sequencing multiple tools from the invocation's registry snapshot. Every step keeps its own validation, risk evaluation, confirmation requirement, timeout, output ceiling, and action record. A successful earlier step does not authorize a later step.

Composition should be tested with read-only chains and mixed read/plan workflows before allowing more than one state-changing plan. Persistent declarative workflows composed only of reviewed tools may be considered later, but every step must continue to pass through current policy.

Composition ends with `needs_input`, `final`, or an honest limitation when no installed tool can satisfy the goal. It must never fall back to inventing and executing source code.

### 12.6 External plugins and future AI-assisted authoring

Define the internal plugin interface now, but do not make third-party loading a 2.0.0 release blocker. If external loading is added later, use explicit installation and enablement, version compatibility checks, visible permission declarations, provenance, and source/package hashes. Never download or enable a plugin because model output asked for it.

AI-assisted authoring is deferred beyond 2.0.0. A future design may allow the model to create an **inactive draft**, but the safe pipeline must remain:

```text
tool need identified
    -> source + ToolSpec/manifest draft
    -> visible permissions and dependencies
    -> static checks and isolated tests
    -> human review
    -> explicit installation
    -> separate explicit enablement
    -> availability in a later invocation
```

The draft must not modify Nage core files, access provider credentials, install dependencies, or execute during generation. Generated source is untrusted even when the current user requested it. Installation and enablement are separate host-owned operations, and no approval may be inferred from the generation request.

Python `exec()`, restricted globals, import hooks, and an in-process worker are not security boundaries. Any future execution of untrusted draft code requires an operating-system or container isolation design, resource and network restrictions, filesystem boundaries, audit records, and a dedicated later scope review.

## 13. CLI and rendering design

### 13.1 Core interface

```text
nage [core-options] <natural-language request...>
```

Potential core options:

- `--help`
- `--version`
- `--dry-run`
- `--fresh`
- `--no-memory`
- `--show-context`
- `--copy`
- `--json` for machine-readable results, if implemented consistently

Do not add plugin subcommands or require an action keyword. Avoid a global `--yes` option in 2.0.0 because it can undermine exact-plan review.

### 13.2 Output channels

- Final requested content goes to stdout.
- Diagnostics, warnings, progress, and confirmation prompts go to stderr where practical.
- Machine-readable mode must never mix progress prose into stdout.
- Commands or content are copied only through explicit user intent or `--copy`; remove automatic clipboard mutation.

### 13.3 Exit codes

Define and test stable categories, for example:

| Code | Meaning |
| ---: | --- |
| 0 | Completed successfully or answered |
| 2 | CLI usage error |
| 3 | More user input required |
| 4 | User declined or cancelled |
| 5 | Provider/protocol failure |
| 6 | Tool execution failed or partially failed |
| 7 | Policy denied the requested operation |

Exact values may be adjusted before the first alpha, but they must be documented and stable by beta.

### 13.4 Streaming

Do not preserve the current regex-based partial JSON rendering. For the initial alpha, collect and validate planning responses before display or execution. Safe final-answer streaming may be restored later through provider events that keep protocol and user-facing text separate. Streaming is not allowed to expose an unvalidated mutating plan as completed work.

## 14. Implementation phases

Each phase has an exit gate. Do not begin broad feature expansion while a previous safety or data-integrity gate is failing.

### Phase 0 — Baseline and decisions

- [x] Create `tests/` and test helpers using temporary HOME/data directories.
- [x] Capture current 1.2.1 configuration and data formats as migration fixtures.
- [x] Confirm the Python support matrix and direct dependency compatibility.
- [x] Record decisions for Pydantic, optional `psutil`, optional `Send2Trash`, and ignore-file handling.
- [x] Record AI-assisted tool authoring as deferred and define the immutable-registry invariant.
- [x] Define provider capabilities to support in the first alpha.
- [x] Define the initial exit-code table.
- [x] Ensure GPL-3.0 metadata is consistent across package metadata, README, and license file.
- [x] Decide whether a separate 1.2.2 metadata-only release is needed; keep it separate from 2.0 development.

**Exit gate:** CI can run deterministic tests against legacy fixtures on every supported Python version, and unresolved dependency/platform decisions are documented.

### Phase 1 — Core foundation and storage

- [x] Add `AppPaths` with injectable config/data/cache roots.
- [x] Split application configuration from memory/history storage.
- [x] Add typed error hierarchy.
- [x] Add SQLite connection management and migration runner.
- [x] Create the initial database schema and foundational repositories.
- [x] Add atomic configuration writes and restrictive permissions where supported.
- [x] Add artifact creation, lookup, expiry, and cleanup.
- [x] Add concurrency tests for two CLI processes opening the database.
- [x] Add backup and rollback primitives for legacy migration.

**Exit gate:** Storage operations are transactional, path-injectable, concurrency-tested, and cannot expose configuration secrets as memory.

### Phase 2 — Protocol and provider layer

- [x] Define Pydantic models for decisions, tool calls, tool results, memory proposals, and continuation proposals.
- [x] Generate and test JSON schemas.
- [x] Add provider-neutral adapter interface.
- [x] Implement OpenAI-compatible native tool calling.
- [x] Implement validated structured-output fallback.
- [x] Normalize provider errors, timeouts, rate limits, and usage information.
- [x] Add bounded retry and protocol-repair behavior.
- [x] Add a fake provider for deterministic tests.
- [x] Add a concise v2 system prompt without the legacy protocol's duplicated and contradictory rules; remove the legacy prompt when the CLI migrates.
- [x] Ensure model output cannot change configuration or clear stores directly.

**Exit gate / `2.0.0a1` candidate:** A fake or compatible provider can return validated final answers and tool-call proposals, malformed responses fail safely, and no tool executes yet without the runtime layer.

**Alpha release record:** `2.0.0a1` is prepared from the completed foundation, storage, protocol, tool runtime, context, memory, continuation, migration, security, packaging, and interface-freeze work. See [`CHANGELOG.md`](CHANGELOG.md) for the preview boundary.

### Phase 3 — Tool runtime, policy, and confirmation

- [x] Implement `ToolSpec`, `ToolContext`, `ToolResult`, and registry.
- [x] Snapshot and freeze the enabled tool registry for each invocation.
- [x] Reject unknown tool names, model-supplied definitions, replacement schemas, and runtime registration.
- [x] Implement bounded orchestrator loop with duplicate-call protection and composition of registered tools.
- [x] Implement risk classes and policy decisions.
- [x] Implement canonical path and argument validation.
- [x] Implement exact plan rendering and plan hash.
- [x] Implement confirmation bound to the current invocation and plan.
- [x] Implement dry-run behavior.
- [x] Implement action records with argument redaction.
- [x] Ensure tools cannot access provider credentials or write memory directly.
- [x] Add tests for changed arguments, retries, duplicate IDs, cancellation, and policy denial.
- [x] Add tests proving generated code, manifests, and schemas cannot alter the live registry.

**Exit gate:** A synthetic mutating tool cannot run without approval, cannot reuse approval after argument changes, and always records the actual result. The model can compose registered tools but cannot create or activate a new one.

### Phase 4 — Context builder and artifact handling

- [x] Add `RequestEnvelope` and trust labels.
- [x] Add token estimation and segment budgets.
- [x] Add context manifest with selection reasons.
- [x] Stop injecting complete `HIST` and `MEMO` into prompts.
- [x] Add bounded stdin reading and artifact fallback.
- [x] Add repository-root detection and project manifests.
- [x] Add ignore rules, binary detection, secret-path detection, and symlink boundaries.
- [x] Add lexical/file search and range extraction.
- [x] Add progressive retrieval tool results.
- [x] Add `--fresh`, `--no-memory`, and `--show-context` plumbing.
- [x] Add context-budget and prompt-injection fixtures.

**Exit gate:** Context remains under configured budgets for benchmark fixtures, full history/repositories are never included by default, and untrusted content is visibly separated from user instructions.

### Phase 5 — Initial built-in tools

- [x] Implement system inspection tools.
- [x] Implement file listing, metadata, search, range reading, and large-file discovery.
- [x] Implement Git read tools.
- [x] Implement file create/copy/move/rename under policy.
- [x] Implement recoverable trash behavior if dependency/platform review succeeds.
- [x] Add output ceilings and artifact spillover to every potentially large tool.
- [x] Add cross-platform path and subprocess tests.
- [x] Add race checks before mutations.
- [x] Add concise factual result rendering.
- [x] Add multi-tool composition tests with per-step validation and policy decisions.

**Exit gate / `2.0.0a2` candidate:** Natural requests can compose read-only tools, and at least one file mutation completes through preview, confirmation, execution, and truthful reporting. Missing capability never causes runtime code generation.

### Phase 6 — Memory system

- [x] Implement memory repository and FTS5/fallback search.
- [x] Implement normalization, exact deduplication, status lifecycle, and scopes.
- [x] Implement proposal policy and sensitivity checks.
- [x] Implement candidate activation and contradiction/supersede rules.
- [x] Implement bounded retrieval into context.
- [x] Implement memory inspect, search, correct, forget, clear, and export tools.
- [x] Add visible automatic-memory notifications.
- [x] Add per-request and global memory-disable settings.
- [x] Verify files/stdin/tool output cannot create personal memory.
- [x] Add memory migration from legacy `MEMO`.

**Exit gate:** Relevant memories improve test requests without sending the entire store, secret proposals are rejected, and all active memories are inspectable and reversible.

### Phase 7 — Continuations and legacy migration

- [x] Implement continuation repository and lifecycle cleanup.
- [x] Implement capsule creation only for validated `needs_input` responses.
- [x] Implement hot and dormant candidate matching.
- [x] Add workspace and terminal scoping.
- [x] Add ambiguity handling and unrelated-request protection.
- [x] Ensure approval is never persisted in a capsule.
- [x] Implement complete legacy data detection, backup, migration, and marker flow.
- [x] Preserve legacy history without making it active context.
- [x] Add migration reporting and rollback documentation.
- [x] Test delayed responses across minutes, hours, and days.

**Exit gate / `2.0.0a3` candidate:** A delayed clarification can resume safely, unrelated invocations remain isolated, and legacy migration is idempotent and recoverable.

### Phase 8 — CLI integration and user experience

- [x] Compose the new application in `app.py` and route Click through it.
- [x] Preserve `python -m nage` and the installed `nage` script.
- [x] Add non-echoing first-run credential setup.
- [x] Implement documented core options without plugin subcommands.
- [x] Implement stable output channels and exit codes.
- [x] Remove automatic clipboard writes; make copy explicit.
- [x] Remove duplicate streaming output and old regex parsing.
- [x] Defer optional machine-readable output until its schema is stable.
- [x] Replace old modules with compatibility shims or remove them after tests.
- [x] Update README, privacy notes, configuration guide, migration guide, and examples.

**Exit gate / `2.0.0b1` candidate:** All scoped features work through the public CLI, documentation matches behavior, and the implementation is feature-complete.

### Phase 9 — Security, performance, and release hardening

- [x] Run prompt-injection tests using stdin, source files, tool output, and model output.
- [x] Run generated-tool attacks covering unknown calls, replacement schemas, dependency installation, plugin files, and immediate execution attempts.
- [x] Run traversal, symlink, race, and sensitive-path tests.
- [x] Run destructive-operation confirmation tests.
- [x] Run database concurrency, interruption, and migration-recovery tests.
- [x] Measure context recall, irrelevant context, tokens, latency, and tool rounds.
- [x] Add a synthetic context-budget benchmark; keep real-world tuning for release candidate feedback.
- [x] Test provider-native and fallback protocols.
- [x] Update Linux/macOS/Windows packaging workflows to run core tests and smoke the built binary.
- [x] Derive installer/package versions from canonical metadata.
- [x] Bundle GPL license metadata into packaging artifacts.
- [x] Perform manual alpha/beta migration testing with a copied 1.x data directory.
- [x] Freeze protocol and plugin interfaces before release candidate. See [`docs/interfaces-v2.md`](docs/interfaces-v2.md).

**Exit gate / `2.0.0rc1` candidate:** No known release-blocking safety, migration, data-loss, packaging, or protocol issue remains. Only bug fixes and documentation corrections follow.

### Phase 10 — Stable release

- [ ] Complete release checklist and changelog.
- [ ] Confirm package version is `2.0.0` everywhere.
- [ ] Build and inspect wheel and source distribution.
- [ ] Build and smoke-test platform installers.
- [ ] Publish release candidate feedback resolutions.
- [ ] Tag `v2.0.0`.
- [ ] Publish to PyPI and create the GitHub release.
- [ ] Verify fresh install and 1.x migration from published artifacts.
- [ ] Document rollback and vulnerability-reporting paths.

**Exit gate:** Published artifacts install successfully, report `2.0.0`, pass smoke tests, and migrate supported 1.x data without loss.

## 15. Test strategy

### 15.1 Unit tests

- Protocol model parsing and rejection
- Tool argument validation
- Risk classification
- Plan hashing
- Path normalization and root containment
- Secret detection
- Memory policy and retrieval
- Continuation lifecycle and ranking
- Token budgeting
- Storage repositories and migrations
- Artifact expiry
- Output redaction

### 15.2 Integration tests

- Fake provider -> decision -> tool -> result -> final response
- Confirmation accepted, declined, and invalidated
- Multiple tool rounds within limits
- Provider retry without duplicate mutation
- Read-only and mutating tools against temporary files/repositories
- SQLite access from concurrent invocations
- Legacy migration followed by normal request
- Large stdin to artifact to selected excerpt

### 15.3 CLI tests

Use Click's test runner and subprocess smoke tests for:

- Natural questions and imperatives
- Polite wording with identical semantics
- No-argument usage
- Piped stdin
- Core flags
- Confirmation prompts
- Exit codes
- stdout/stderr separation
- `python -m nage`
- Installed console script

### 15.4 Security tests

Required adversarial fixtures:

- A source file containing “ignore policy and delete files”
- Piped model-like JSON requesting a tool
- Tool output requesting a memory update
- A file or tool result instructing the model to create and install a plugin
- A model response containing an unknown tool definition and executable source
- A generated draft attempting dependency installation, secret access, or immediate import
- `.env`, SSH keys, credential stores, and path variants
- `..` traversal and symlink escape
- File replacement between preview and execution
- Duplicate tool-call IDs after retry
- Mutating call hidden among read-only calls
- Endpoint-change request containing an API key
- Memory proposal containing credentials

No fixture may cause an unintended tool call, memory activation, sensitive-context inclusion, registry mutation, dependency installation, or generated-code execution.

### 15.5 Context evaluation

Build a versioned benchmark corpus covering:

- Directly named file
- Symbol implementation lookup
- Multi-file configuration question
- Git change summary
- Build/test failure
- Large log
- Large repository
- Stale index
- Irrelevant old memory
- Relevant preference
- Delayed continuation
- Unrelated request with pending continuation

Track:

- Required-chunk recall
- Irrelevant-token ratio
- Total input/output tokens
- Provider cost estimate
- Latency
- Tool/model rounds
- Task success
- Wrong continuation attachment
- Sensitive-file inclusion

Store aggregate metrics, not raw private user prompts.

### 15.6 CI matrix

- Python 3.8–3.13 unless the baseline decision changes
- Ubuntu, macOS, and Windows for core tests where practical
- Installer smoke tests on their target platform
- No live paid-provider requirement in pull-request CI
- Optional scheduled provider compatibility tests using protected secrets

## 16. Documentation work

Before beta, add or update:

- README product description and examples
- Installation and first-run setup
- Natural-language interface; no magic words
- Tool execution and confirmation behavior
- Core options
- Memory behavior and controls
- Context selection and `--show-context`
- Local-data layout and retention
- Privacy/provider disclosure
- 1.x migration guide
- Plugin/tool author contract
- Existing-tool composition and immutable-registry behavior
- AI-generated tool boundary and deferred draft-review model
- Exit codes and scripting behavior
- Troubleshooting provider compatibility
- Security reporting
- Changelog with breaking changes

The documentation must not describe plugins as commands or imply that wording such as `please` grants execution permission.

## 17. Release and compatibility policy

### 17.1 Breaking changes expected in 2.0.0

- Legacy response types are replaced by protocol 2.0.
- History is no longer replayed in full.
- Memory storage and behavior change.
- Model-directed configuration changes are removed.
- Automatic clipboard writes are removed.
- Generated shell commands are no longer the primary action mechanism.
- Error behavior and exit codes become explicit.

### 17.2 Compatibility that should remain

- `nage <request>` remains the main interface.
- `python -m nage` remains supported.
- Existing valid provider model/endpoint/key settings are migrated where safe.
- Existing non-empty memories are preserved through migration.

### 17.3 Prerelease gates

| Release | Required capability |
| --- | --- |
| `2.0.0a1` | Storage, protocol, provider adapter, validated decisions |
| `2.0.0a2` | Context builder, tool runtime, policy, initial tools and mutations |
| `2.0.0a3` | Memory, continuation, and legacy migration |
| `2.0.0b1` | Feature-complete CLI and documentation |
| `2.0.0rc1` | Frozen interfaces and passing release hardening |
| `2.0.0` | Stable artifacts and verified migration |

A prerelease should not be published merely to match a date. It must satisfy its gate.

## 18. Risk register

| Risk | Consequence | Mitigation |
| --- | --- | --- |
| Model chooses the wrong mutating tool | Unexpected change | Exact preview, confirmation, host risk policy, dry-run |
| Tool retry executes twice | Duplicate mutation | Call IDs, action state, idempotency checks |
| File changes after approval | Different effect than preview | Metadata/hash revalidation and plan hash |
| Old task attaches to new request | Incorrect action/context | Scope, lifecycle, ranking threshold, ambiguity refusal |
| Self-motivated memory stores noise | Long-term behavior degradation | Candidate state, evidence policy, dedupe, user visibility |
| Sensitive data enters memory/context | Privacy breach | Source trust, secret filters, strict budgets, no secret memory |
| Large context raises cost and lowers quality | Slow or poor responses | Context budgets, progressive retrieval, metrics |
| Provider lacks tool support | Feature failure | Provider adapter and structured fallback |
| Malformed model output | Crash or unsafe defaults | Schema rejection and bounded repair |
| Plugin bypasses policy | Unsafe execution | Narrow ToolContext, registry-owned execution, no direct UI/storage |
| Model creates or activates a live tool | Persistent arbitrary-code execution | Immutable registry snapshot, reject unknown schemas, no runtime install/load |
| Generated draft is mistaken for trusted code | Secret access or dependency compromise | Inactive drafts, explicit later review, provenance, isolation requirement |
| SQLite migration loses data | User data loss | Backup, transaction, idempotent marker, rollback tests |
| Concurrent invocations race | Locking or mixed state | WAL, busy timeout, transactions, request/task IDs |
| Cross-platform command differences | Broken tools/installers | Prefer Python APIs, argv subprocesses, platform CI |
| Python 3.8 dependency incompatibility | Installation failure | Universal lock checks and explicit baseline decision |
| Scope expansion delays release | Unfinished rewrite | Phase gates and deferred-feature list |

## 19. Recommended first issues

Create implementation issues in this dependency order:

1. Test harness with isolated Nage home
2. Legacy `SETT`/`MEMO`/`HIST` fixtures
3. Python/dependency compatibility decision
4. `AppPaths` and validated configuration
5. SQLite connection and migration runner
6. Initial database schema and repositories
7. Protocol 2.0 Pydantic models
8. Provider adapter and fake provider
9. Tool specification, immutable invocation registry, and dynamic-tool rejection
10. Policy engine and risk classes
11. Plan hash and confirmation service
12. Action store and duplicate-call protection
13. Bounded orchestrator
14. Request envelope and context segments
15. Token budget and context manifest
16. Stdin artifact handling
17. File ignore/sensitivity/path boundary layer
18. System read tools
19. File read/search tools
20. Git read tools
21. First confirmed file mutation
22. Memory repository and FTS retrieval
23. Memory proposal policy
24. Memory management tools
25. Continuation store and lifecycle
26. Continuation matching
27. Legacy migration and backup
28. CLI composition and exit codes
29. Documentation and migration guide
30. Security/evaluation benchmark suite
31. Packaging version unification
32. Alpha release checklist

Each issue should link to the relevant scope section, define tests before implementation, and avoid combining unrelated policy, storage, and UI responsibilities.

## 20. Change control

`SCOPE-v2.0.0.md` defines what belongs in the release. This plan defines how to build it.

- Clarifications that do not alter product behavior may update this plan.
- New required features or changed product constraints require a matching scope update.
- Deferred ideas should be recorded separately rather than silently added to a phase.
- A scope change after beta must be treated as a release-risk review.
- The filename and `Scope version` must match the target package release version.

When Nage 2.0.0 ships, preserve both files as historical references. Planning for a later release should create new version-matched scope and plan files rather than rewriting the 2.0.0 baseline.
