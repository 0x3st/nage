# Nage Product Scope and User Stories

**Scope version:** 2.0.0

**Status:** Product baseline for Nage 2.0.0

**Target release:** Nage 2.0.0

## 1. Product definition

Nage is a one-shot, natural-language assistant that helps a user understand and operate their computer from the terminal. The user describes an outcome in ordinary language; Nage selects suitable internal tools, gathers only the context it needs, performs supported actions under a local safety policy, and reports the result.

```bash
nage find what is using my disk space
nage could you organize these screenshots by month
nage explain why this test failed
npm test 2>&1 | nage identify the likely root cause
```

Nage should feel like a Siri-style assistant embedded in the terminal, but it is not a chat application, a full-screen interface, or a repository-first coding agent.

The core product loop is:

```text
natural-language request
        -> bounded context assembly
        -> intent and tool selection
        -> local validation and risk policy
        -> approval when required
        -> tool execution
        -> concise, honest result
```

Every invocation has one request and one result. Nage may perform several internal model/tool steps during that invocation, but the user does not enter a persistent conversation UI.

## 2. Product contract

The following decisions define Nage's interface and identity.

### 2.1 Natural language has no magic words

Everything after `nage` is an ordinary natural-language request. Words such as `please`, `do`, `run`, or `help me` have no special parsing behavior and do not enable an action mode.

These requests differ only by their ordinary meaning:

```bash
nage find the largest files here
nage please find the largest files here
nage how can I remove old cache files
nage remove old cache files
nage would removing old cache files be safe
```

Nage must infer whether the user is asking for an explanation, an inspection, a hypothetical assessment, or a state-changing action. When intent is ambiguous and the consequence matters, Nage must clarify rather than guess.

### 2.2 Plugins are internal tools, not user commands

A user does not select a plugin, mention a tool name, or learn plugin-specific syntax. Interfaces such as these are not part of the product:

```text
nage /git status
nage files find-large
nage use system-plugin disk
```

Instead, a natural request such as `nage what changed in this repository` may cause the model to call an internal `git.status` tool. Plugin names and schemas are implementation details.

### 2.3 The model proposes; the core decides

The model may propose an answer, tool call, continuation, or memory update. Application code owns:

- Schema validation
- Tool availability
- Path and argument validation
- Permission and risk classification
- User confirmation
- Execution
- Persistent storage
- Success and failure determination

A model-generated statement or tool call is never proof that an operation happened and never grants itself permission.

### 2.4 Sessionless interface, bounded internal state

Nage has no user-visible conversation sessions. It may retain compact, scoped state where continuity is necessary, such as an incomplete task waiting for a recipient or path. It must not replay an ever-growing transcript on every invocation.

## 3. Goals

1. **One-shot use**
   - A normal invocation should be sufficient to ask a question or request a supported action.
   - Nage exits after the result, a safety decision, or a concise request for missing information.

2. **Outcome-oriented assistance**
   - Users describe goals rather than commands, APIs, plugins, or implementation details.
   - Nage can answer, inspect, and act when an installed tool supports the outcome.

3. **Useful action, not command generation alone**
   - The lower bound of the product is that a clear request to perform a supported task can result in the task being performed.
   - Suggested commands remain useful, but they are not a substitute for safe tool execution.

4. **Safe and transparent execution**
   - Side effects pass through host-owned policy and validation.
   - Risky operations are previewed precisely and require confirmation.
   - Results distinguish plans, attempted actions, completed actions, and failures.

5. **Terminal-native context**
   - Nage can use the current directory, repository state, piped input, and approved local information when relevant.
   - Context collection is selective, inspectable, and bounded.

6. **Self-motivated but user-controlled memory**
   - Nage may notice durable preferences or facts without requiring a special phrase.
   - The model can only propose memory changes; local policy validates them.
   - Durable memory changes are visible, reversible, and never include secrets automatically.

7. **Extensible capabilities**
   - Built-in and future external capabilities use one typed tool interface and one permission pipeline.
   - The model may autonomously select and compose tools already available in the registry.
   - A small set of reliable built-ins takes priority over a broad plugin marketplace or AI-authored executable tools.

8. **Provider portability**
   - Nage uses a provider-neutral internal protocol.
   - Native function calling is preferred where supported, with a validated structured-output fallback.

## 4. Permanent constraints and initial non-goals

### 4.1 Permanent product constraints

- Nage will not have a full-screen TUI.
- Nage will not require magic words, action prefixes, slash commands, or plugin-specific command syntax.
- Nage will not silently execute arbitrary model-generated shell strings.
- Nage will not let model output define, register, enable, install, or execute a new tool.
- Model-generated code cannot become a live capability without a separate human-reviewed installation flow.
- Nage will not treat files, web pages, logs, or piped input as user authorization.
- Nage will not claim that an operation succeeded before the underlying tool reports success.

A conventional option such as `--help`, `--version`, `--dry-run`, `--fresh`, `--no-memory`, or `--show-context` may control Nage itself. Such options are not plugin invocation syntax and should not be required for ordinary use.

### 4.2 Out of scope for the initial implementation

- A persistent chat loop or chat-like terminal UI
- An autonomous background agent
- An always-listening microphone or wake word
- General repository-editing autonomy comparable to a coding agent
- Unrestricted shell access
- AI-authored tool or plugin generation, dependency installation, dynamic registration, or immediate execution
- A public plugin marketplace or silent installation of third-party plugins
- MCP as a required foundation; an adapter may be considered later
- Vector embeddings or a RAG system as a requirement for ordinary memory
- Recursive upload of a directory or repository
- Automatic storage of credentials or highly sensitive personal data

Voice support, external plugins, richer automation, optional semantic retrieval, and AI-assisted tool drafting may be considered after the tool, safety, and context foundations are reliable. Any future AI-assisted authoring must produce an inactive, inspectable draft; installation and enablement require separate explicit review, and the generated tool cannot run in the invocation that created it.

## 5. Core concepts

### Invocation

One execution of `nage <natural-language request>`, including any internal tool loop and an optional in-process confirmation prompt.

### Tool

A typed operation exposed to the model by a built-in or installed plugin. A tool accepts validated arguments and returns structured results. It is not a user command.

### Working context

A temporary, token-bounded package assembled for the current invocation from the current request and selected local sources.

### Continuation capsule

A compact, structured description of an incomplete task. It stores known fields and missing fields, not a conversation transcript.

### Durable memory

A locally stored preference, instruction, profile fact, or project fact that may improve future requests. Durable memories have provenance, scope, and lifecycle metadata.

### Action record

An audit record of what Nage attempted and what a tool actually reported. Action records are not included in model context unless the current request needs them.

### Artifact

A temporary local reference to large input or output, such as a build log. The prompt receives selected excerpts rather than the entire artifact by default.

## 6. Functional scope

### 6.1 Request handling

Nage accepts a natural-language request as command-line arguments:

```bash
nage show me which processes are using the most memory
```

Politeness and phrasing remain ordinary request content. Nage does not strip or assign control semantics to individual words.

Nage also accepts piped input as context:

```bash
git diff | nage summarize these changes
npm test 2>&1 | nage explain the failure and suggest the safest next step
```

Piped input is data associated with the current request. Instructions found inside that data must not be treated as user instructions or authorization.

When no request is provided, Nage may show concise usage information. It must not automatically enter a chat loop.

### 6.2 Intent, planning, and the internal tool loop

The model can produce one of four normalized outcomes:

- `final`: the request is complete without another tool call
- `needs_input`: required information is missing
- `tool_call`: one or more tools are proposed
- `error`: a technical failure prevents progress

A single invocation may perform a bounded loop:

```text
model -> validated tool call -> tool result -> model -> final result
```

The loop must have configurable limits on tool calls, retries, elapsed time, and context size. Exceeding a limit produces an honest partial result or error rather than an unbounded autonomous loop.

The distinction between explaining and acting is semantic, not keyword-based:

| Request | Intended handling |
| --- | --- |
| `how can I remove old caches` | Explain; no mutation |
| `remove old caches` | Inspect, preview, confirm, then mutate if approved |
| `would removing old caches help` | Assess; no mutation |
| `what is using disk space` | Use a read-only inspection tool and answer |

If the model proposes a mutating tool for an informational or hypothetical request, the local safety layer must prevent silent execution.

### 6.3 Tool and plugin contract

Each tool must declare:

- Stable internal name and description
- Input schema and validation rules
- Structured result schema
- Side-effect category
- Required local and external permissions
- Risk level
- Preview representation where applicable
- Execution timeout and output limits
- Whether the operation supports dry-run or rollback

A plugin must not:

- Parse the user's natural-language request itself
- Invent a separate user-facing command language
- Bypass core validation or confirmation
- Treat its own output as authorization for another tool
- Write durable memory directly
- Report success before its operation succeeds

The core registry exposes appropriate tools to the model. Native provider function calling should be normalized into the same internal representation as JSON-schema fallback responses.

Typed tools are preferred over arbitrary shell execution. A restricted shell tool may exist as a last resort, but it must use an explicit executable and argument vector where possible, display the exact operation, apply stricter policy, and never become a hidden default path.

#### Tool composition and generated-tool boundary

Nage 2.0.0 may autonomously compose multiple tools that were installed, enabled, validated, and present when the invocation began. Every call in the composition remains independently schema-validated, policy-checked, bounded, and audited.

The available registry is immutable for an invocation. Model output cannot provide a new schema or implementation, install a dependency, write executable plugin code and load it, or convert a script into a callable tool. Unknown tool names and model-supplied tool definitions are rejected.

If a user asks for source code for a possible tool, Nage may explain or produce an ordinary, inactive code draft under the normal file-writing policy. The draft is untrusted content, not an installed capability, and cannot be imported or executed automatically.

A future AI-assisted authoring feature, if approved in a later scope, must separate drafting, inspection, testing, installation, enablement, and execution. It must show source, manifest, permissions, dependencies, provenance, and hashes; use an isolated validation environment; require explicit human approval; and make the tool available no earlier than a later invocation. In-process `exec()` or restricted Python globals are not a security sandbox.

### 6.4 Permission and confirmation policy

Risk is defined by application-owned tool metadata and validated arguments, not by the model's confidence or wording.

| Operation | Default behavior |
| --- | --- |
| Answer from provided context | Return the answer |
| Read-only system or Git inspection | Run within allowed scope |
| Read ordinary files in the current scope | Run when relevant and policy permits |
| Read sensitive or unclear paths | Ask or refuse |
| Create or modify files | Preview exact scope and ask |
| Move or rename files | Preview exact source and destination and ask |
| Delete files | Always preview exact targets and ask |
| Run privileged operations or change system settings | Always ask; refuse unsupported risks |
| Install or remove software | Always preview and ask |
| Create, install, enable, or execute an AI-generated tool/plugin | Out of scope for 2.0.0; refuse |
| Send messages, publish content, or transmit data to another destination | Identify destination and payload scope, then ask |
| Make purchases or irreversible account changes | Out of scope initially |
| Ambiguous or unsupported operation | Ask for missing information or explain the limitation |

Confirmation is bound to the exact normalized plan. If tool arguments change, previous approval is invalid. Approval is not carried across continuation capsules or future invocations.

A declined confirmation performs no mutating operation.

### 6.5 Continuity without sessions

Nage does not send full history with every request. Continuity is provided by small continuation capsules and targeted retrieval.

A capsule may contain:

```json
{
  "task_id": "email-7d2",
  "intent": "mail.send",
  "known": {"subject": "Project meeting"},
  "missing": ["recipient", "body"],
  "scope": "current workspace",
  "created_at": "host assigned",
  "implicit_until": "host assigned",
  "retain_until": "host assigned"
}
```

The host, not the model, assigns identity, scope, timestamps, and retention.

Default lifecycle:

- **Hot:** up to two hours; considered automatically when the new request plausibly continues it
- **Dormant:** retained for up to 30 days; used only for a strong semantic/slot match or clear reference
- **Completed:** removed immediately from active continuation storage
- **Expired:** deleted from continuation storage; a minimal audit record may remain under normal audit retention
- **Pinned:** retained only when the user explicitly chooses long-term task retention

Sensitive capsules use shorter retention or are not persisted. Defaults should be configurable.

Time alone is insufficient for matching. Nage should also consider terminal identity when available, working directory or repository, expected missing fields, recency, and ambiguity. If multiple capsules match, Nage must not guess.

A new unrelated request must not inherit an old task. Users can bypass temporary context with a core option such as `--fresh`, but ordinary use should not require one.

### 6.6 Self-motivated memory

Memory formation does not require phrases such as `remember this`. The model may propose a memory while answering or using tools:

```json
{
  "operation": "upsert",
  "kind": "preference",
  "text": "The user prefers pnpm for Node.js projects.",
  "scope": "global",
  "confidence": 0.96,
  "evidence": "explicit user statement",
  "sensitivity": "normal"
}
```

The local memory policy decides whether to:

- Accept an explicit, low-risk memory
- Accept and visibly report a strong, stable preference
- Keep a weak observation as an inactive candidate
- Merge a duplicate
- Supersede a contradiction
- Reject sensitive, temporary, inferred, or untrusted content

Good memory candidates include stable preferences, response instructions, and durable project conventions. One-time requests, raw logs, file contents, model speculation, and temporary system state are not durable personal memories.

Passwords, API keys, authentication tokens, private keys, and equivalent secrets must never be stored as memory. Highly sensitive identity or location data must never be stored automatically.

Content from files, tool output, web pages, and stdin is untrusted context and cannot create personal memory merely because it contains an instruction or statement.

Every active memory must have:

- Stable identifier
- Kind
- Normalized content
- Scope, such as global or repository
- Provenance
- Confidence or evidence level
- Sensitivity classification
- Created and updated timestamps
- Status, such as candidate, active, superseded, or deleted
- Optional expiration or source hash

Memory changes must be inspectable and reversible through natural requests. Nage should report durable automatic changes concisely. Users can disable memory globally or for one invocation.

The model must not directly clear the memory store. Clear and deletion proposals pass through local validation, and destructive bulk deletion requires explicit confirmation.

SQLite is the canonical initial store. SQLite FTS5 and deterministic scope filtering should be used before considering embeddings. A human-readable Markdown or JSON export may be provided, but it is not the canonical database.

### 6.7 Bounded context assembly

A fresh context is compiled for every invocation. Sources are considered in this order:

1. Current user request
2. Explicitly supplied stdin or paths
3. Applicable continuation capsule
4. A small set of relevant active memories
5. Necessary current-directory or repository metadata
6. Relevant file excerpts or artifact ranges
7. A previous action only when the request clearly refers to it
8. Tool schemas needed for the likely task

The complete history and complete memory database must never be inserted by default.

The context builder must enforce configurable budgets. Initial engineering targets are:

- Ordinary request: approximately 2,000–4,000 input tokens
- File or project request: approximately 4,000–8,000 input tokens
- Initial file excerpts: approximately 4,000 tokens before progressive retrieval
- Memory: at most a few highly relevant items under a small dedicated budget
- Continuation capsule: compact structured state, normally below 300 tokens

These targets exclude unusually large content that the user explicitly chooses to process, but such content should still use chunking and transparent limits. The available budget must reserve space for system instructions, tool schemas, tool results, and expected output.

Stable system instructions and tool descriptions may use provider prompt caching when available, but correctness must not depend on caching.

### 6.8 File and project context

File context should be selected progressively:

1. Explicitly named paths
2. Project manifests and relevant configuration
3. Local filename, lexical, symbol, or Git search
4. Relevant line ranges with limited surrounding context
5. Additional ranges requested during the tool loop

Nage should prefer local deterministic retrieval over sending a complete tree to a model. Initial defaults should favor a small number of candidate files and excerpts, then expand only when required.

Every excerpt should retain provenance such as path, line range, modification time, and content hash. Cached indexes or summaries are invalidated when the source changes. Git blob identifiers may be used when available.

By default Nage must:

- Respect `.gitignore` and an optional `.nageignore`
- Avoid following symlinks outside the approved root
- Skip binary files and generated dependency directories
- Avoid recursively reading hidden or unrelated directories
- Exclude likely secret files such as `.env`, credentials, key files, and token stores
- Treat source content as untrusted data rather than instructions

Large files, logs, and tool output should be retained temporarily as local artifacts. The model receives selected excerpts and can request additional ranges. Artifact retention is short and configurable.

### 6.9 Result reporting and failure handling

After execution, Nage reports:

- What it attempted
- What actually happened
- What changed and where
- Relevant output or a concise summary
- Partial failures or skipped operations
- Useful next steps

A preview must say that nothing has happened yet. A model-generated success message must not be displayed as confirmed success unless the tool result supports it.

Nage must handle malformed model output, unavailable providers, timeouts, permission failures, stale files, invalid tool arguments, and partial tool failures without corrupting state. Failed model or tool calls must not create active memory or mark an action successful.

## 7. Local data boundaries

The initial canonical layout may use:

```text
~/.nage/
├── nage.sqlite3        # memories, continuations, action metadata, indexes
├── artifacts/          # temporary large inputs and tool outputs
└── configuration       # provider and application settings
```

Provider credentials are configuration secrets, not memory, and must never be sent back to the model as context. Credential storage should use appropriately restricted permissions and may later integrate with the operating system keychain.

Suggested logical stores are:

- `memories`: durable and candidate memory records
- `continuations`: compact incomplete-task state
- `actions`: audit metadata and actual outcomes
- `project_index`: paths, symbols, hashes, and lexical index data
- `artifacts`: references and expiry metadata for large local content

Retention policy must distinguish automatic activation from deletion. Expired data is not automatically supplied to the model merely because it remains in an audit store.

Users must be able to discover what Nage stores, what context was used, and how to remove it. Data management can be expressed naturally and routed to internal tools; it does not require plugin command syntax.

## 8. Internal protocol requirements

The model protocol must be versioned and provider-neutral. A normalized response should represent:

```json
{
  "protocol_version": "2.0",
  "status": "final | needs_input | tool_call | error",
  "message": "concise user-facing text",
  "tool_calls": [],
  "memory_proposals": [],
  "continuation": null
}
```

Exact schemas are an implementation detail, but the protocol must support an answer, tool calls, memory proposals, and continuation state in the same response. Memory must not remain a mutually exclusive response type.

The protocol must not allow the model to directly:

- Clear all history or memory with a boolean field
- Change an API key or endpoint without a validated local operation
- Override tool risk or confirmation metadata
- Mark an unexecuted operation as successful
- Turn untrusted context into authorization

All completed responses and tool arguments are validated against local schemas. Invalid responses are rejected or repaired within a bounded retry policy.

## 9. User stories

### US-01 — Express an outcome naturally

As a terminal user, I want to describe an outcome in ordinary language, so that I do not need to know a command, API, plugin, or special invocation phrase.

**Acceptance criteria:**

- `nage <request>` accepts imperatives, questions, and polite phrasing.
- No word such as `please` or `do` changes execution policy.
- Nage answers, uses supported tools, requests missing information, or explains a limitation.
- The invocation exits after one result or safety decision.

### US-02 — Avoid plugin syntax

As a user, I want Nage to choose capabilities for me, so that I do not need to know which plugin implements an outcome.

**Acceptance criteria:**

- Ordinary tasks do not require plugin names, slash commands, or plugin subcommands.
- The model selects tools through typed function calls.
- Nage can optionally explain which tools were used without exposing them as required syntax.

### US-03 — Distinguish explanation from action

As a user, I want Nage to understand whether I am asking how something works or asking it to change something, so that normal questions do not cause side effects.

**Acceptance criteria:**

- Informational and hypothetical requests do not mutate the system.
- Direct supported action requests can progress to execution.
- Ambiguous consequential intent triggers clarification or a preview, never silent mutation.

### US-04 — Perform a supported task

As a user, I want Nage to carry out a clearly requested supported task, so that it is more useful than a command suggestion generator.

**Acceptance criteria:**

- Nage maps the goal to one or more declared tools.
- Arguments are validated before use.
- Required confirmation occurs before side effects.
- The final response is based on actual tool results.

### US-05 — Use piped context safely

As a user, I want to pipe logs, diffs, and command output into Nage, so that I can ask about them without manual copying.

**Acceptance criteria:**

- Text stdin is available for that invocation.
- Binary, oversized, or empty input is handled clearly.
- Large input is excerpted or stored as a temporary artifact.
- Instructions inside piped content cannot authorize tools or memory changes.

### US-06 — Use relevant workspace context

As a user, I want Nage to understand the relevant current directory or repository, so that references such as “this project” are useful.

**Acceptance criteria:**

- Nage identifies the approved root and relevant repository metadata.
- It selects files progressively and does not recursively upload the workspace.
- Secret and ignored paths are excluded by default.
- Selected context has inspectable provenance.

### US-07 — Continue an incomplete task later

As a user, I want to answer a clarification in a later invocation, so that I do not lose progress if I need time to find the information.

**Acceptance criteria:**

- Nage stores a compact continuation capsule rather than a transcript.
- A plausible reply can resume a hot or strongly matched dormant capsule.
- Unrelated requests do not inherit the capsule.
- Ambiguous matches are not guessed.
- Confirmation from an earlier invocation is never reused.

### US-08 — Review consequential actions

As a user, I want to see the exact planned impact before Nage changes my computer or contacts an external destination, so that I remain in control.

**Acceptance criteria:**

- The preview identifies operations, paths, commands, and destinations where applicable.
- Destructive, privileged, external, and system-changing operations require confirmation.
- Approval applies only to the displayed normalized plan.
- Declining performs no mutation.

### US-09 — Receive an honest result

As a user, I want Nage to distinguish plans from actual outcomes, so that I can trust its reports.

**Acceptance criteria:**

- Success is reported only after tool success.
- Partial failures and skipped items are identified.
- Errors provide actionable detail without fabricating completion.
- The action record reflects the tool result rather than model prose.

### US-10 — Benefit from self-motivated memory

As a user, I want Nage to notice durable preferences without requiring a magic phrase, so that future requests improve naturally.

**Acceptance criteria:**

- The model can propose memory alongside any response.
- Local policy accepts only appropriate, sufficiently supported information.
- An automatically activated durable memory is reported concisely.
- Duplicates and contradictions are merged or superseded instead of appended blindly.
- Secrets and untrusted context are never stored as personal memory.

### US-11 — Inspect and control local state

As a user, I want to inspect, correct, forget, export, or clear stored information, so that Nage's state remains under my control.

**Acceptance criteria:**

- Memories, pending continuations, and action history can be inspected.
- Individual records can be corrected or removed.
- Bulk destructive operations require explicit confirmation.
- Memory can be disabled globally or for one invocation.

### US-12 — Keep model context bounded

As a user, I want Nage to remain fast and affordable over time, so that old interactions do not continuously increase cost or reduce answer quality.

**Acceptance criteria:**

- Complete history and memory are not sent by default.
- Every invocation has a context budget and manifest.
- Only relevant memories, excerpts, and state are selected.
- Large content uses progressive retrieval.
- Users can inspect a summary of what context was selected.

### US-13 — Recover from a failed command or tool

As a user, I want Nage to diagnose a failure and offer a safe next step, so that I can recover without understanding every low-level detail.

**Acceptance criteria:**

- Failure output can be supplied through stdin, an artifact, or an approved shell integration.
- Nage identifies likely causes with uncertainty where appropriate.
- Any repair action follows the normal validation and confirmation policy.

### US-14 — Add a capability without changing the core

As a contributor, I want to add a tool through a documented plugin interface, so that Nage can grow while preserving consistent safety behavior.

**Acceptance criteria:**

- The plugin declares typed inputs, outputs, side effects, permissions, risk, and limits.
- The plugin uses the shared validation, preview, execution, and reporting pipeline.
- It does not introduce required user-facing command syntax.
- Untrusted plugins are not enabled silently.
- Model-generated source is treated as an inactive, untrusted draft rather than an installed plugin.

### US-15 — Compose existing tools without inventing live code

As a user, I want Nage to combine available capabilities when one tool is insufficient, so that it can complete useful multi-step work without generating and executing arbitrary code.

**Acceptance criteria:**

- The model may select multiple tools from the invocation's fixed registry.
- Every step is independently validated, authorized, bounded, and recorded.
- Unknown or model-defined tools are rejected.
- Missing capability produces an explanation or safe alternative, not self-installation.
- Code suggested for a future tool is never loaded or executed automatically.

## 10. Initial implementation boundary

The first implementation of this scope should prove the complete product loop with a deliberately small capability set.

### Required foundation

1. Versioned internal response and tool-call protocol
2. Local schema validation and bounded retry handling
3. Tool registry with host-owned risk and permission metadata
4. Context builder that replaces full-history and full-memory injection
5. SQLite memory, continuation, and action stores
6. Continuation lifecycle and scoped matching
7. Preview and confirmation pipeline bound to exact tool arguments
8. Piped-input handling and temporary artifacts
9. Context and state inspection diagnostics
10. Tests for safety, context selection, storage migration, malformed responses, and tool failures

### Initial built-in capabilities

- **System:** disk, memory, processes, platform, and environment inspection
- **Files:** scoped search and reading plus a small set of previewed, confirmed mutations
- **Git:** status, diff, log, branch, and repository metadata, initially read-oriented
- **Memory:** search, propose, inspect, correct, forget, clear, and export
- **Context:** inspect and clear pending working context

A restricted shell capability may be introduced only after the typed-tool policy is established. It should not be the default implementation for operations that have typed tools.

### Deferred until after the foundation

- Third-party plugin discovery and installation
- AI-assisted inactive tool drafting and review workflow
- Dynamic tool generation, registration, dependency installation, or execution
- Broad shell automation
- Voice input and output
- Background or scheduled autonomous work
- Embedding-based memory or project retrieval
- Large-scale document knowledge bases
- MCP integration
- Autonomous repository modification

## 11. Evaluation and release criteria

Evaluation must include representative small, medium, and large workspaces and should measure:

- Task completion accuracy
- Relevant-context recall
- Irrelevant-context rate
- Input tokens and estimated provider cost
- End-to-end latency
- Number of model/tool rounds
- Wrong continuation attachment rate
- Stale file-context rate
- Unintended mutation rate
- Sensitive-context inclusion rate
- Accuracy of reported action outcomes

The test set should include:

- Direct and polite requests with identical intent
- Explanatory, hypothetical, negated, and mutating variants of the same topic
- Tool-like words quoted inside logs or files
- Files or model responses that request creation, installation, or execution of a new tool
- Unknown tool calls and model-supplied tool schemas
- Clarifications resumed after minutes, hours, and days
- An unrelated request while another task is pending
- Multiple terminals or workspaces with pending tasks
- Large logs and repositories
- Changed files after indexing
- Secret, ignored, binary, generated, and out-of-root files
- Tool timeouts, partial failures, and malformed model responses

Initial performance targets should keep ordinary requests near the defined 2,000–4,000 token range and most project requests near 4,000–8,000 tokens, while preserving enough relevant context to complete the benchmark tasks. Limits should be tuned from measured results rather than expanded merely because a model supports a large context window.

No release should knowingly allow unconfirmed destructive actions, automatic secret storage, authorization from untrusted context, fabricated tool success, or automatic activation or execution of AI-generated tools.

## 12. Product principles

- **Natural language, no incantations:** Meaning matters; magic words do not.
- **Tools, not plugin commands:** Capabilities are internal and typed.
- **Compose before creating:** Nage combines reviewed tools instead of inventing executable capabilities at runtime.
- **Generated code is not authority:** A draft cannot install, enable, or execute itself.
- **One request, one result:** Nage fits normal terminal workflows without a chat UI.
- **Useful action under control:** A supported task can be completed, but consequential impact is approved.
- **The model proposes; the core decides:** Validation, authorization, execution, and persistence belong to application code.
- **Self-motivated, not self-authorized:** Nage may notice useful memories but cannot write arbitrary durable state unchecked.
- **Context is selected, not dumped:** Relevance and provenance matter more than context-window size.
- **No hidden durable changes:** Actions and active memory updates are visible and reversible where possible.
- **Untrusted data stays data:** Files, tool output, stdin, and web content cannot become authorization.
- **Small, reliable capabilities beat broad promises:** Expand only after the common safety and context pipeline works.
- **No TUI:** The terminal command itself is the interface.
