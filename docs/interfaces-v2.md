# Nage 2.0 protocol and tool interfaces

**Status:** Frozen for the `2.0.0` release candidate

**Scope:** This document freezes the provider-neutral protocol and the host-owned
internal tool contract. It does not enable third-party plugin loading; external
plugin discovery, installation, and AI-authored executable tools remain outside
Nage 2.0.0.

## 1. Compatibility rule

The following interfaces are the v2 release-candidate contract:

- `nage.protocol` decision models and `parse_assistant_decision`
- `nage.providers.BaseProvider` and provider request/result value objects
- `nage.tools.ToolSpec`, `ToolContext`, `ToolPreview`, `ToolOutcome`, and
  `ToolRegistrySnapshot`
- host-owned risk, confirmation, execution, and result semantics

Only bug fixes, security fixes, documentation corrections, and implementation
changes that preserve these contracts may be made after this freeze. A change
to a field, validation rule, exported type, status value, tool-call meaning, or
registry invariant requires a release-risk review, an ADR, updated tests, and a
protocol/interface version decision before it is merged.

The package remains at the development baseline version until a corresponding
prerelease artifact is intentionally created. Freezing these interfaces does
not itself publish a release.

## 2. Normalized decision protocol

Provider adapters must normalize planning responses into
`nage.protocol.AssistantDecision`. The wire representation is a JSON object:

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

### 2.1 Root rules

- `protocol_version` is exactly `"2.0"`.
- `status` is one of `final`, `needs_input`, `tool_call`, or `error`.
- Unknown fields are rejected; model output cannot add authority fields.
- `message` is optional text with a maximum length of 32,768 characters.
- At most 8 tool calls and 8 memory proposals are accepted.
- `needs_input` requires a validated continuation proposal.
- A continuation is invalid for every other status.
- `tool_call` requires at least one tool call.
- Tool calls are invalid for every other status.
- Tool-call IDs are unique within a decision.
- An `error` decision cannot propose memory changes.

Responses are size-limited and parsed as UTF-8 JSON before Pydantic
validation. Invalid, oversized, non-object, or malformed responses are
provider/protocol failures; they are never treated as an empty or successful
decision.

### 2.2 Tool calls

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

- `id` matches `[A-Za-z0-9_.:-]{1,128}`.
- `name` is a lowercase dotted identifier such as `files.find_large`.
- `arguments` is an object validated against the host-registered Pydantic
  argument model.
- A model cannot supply or replace an argument schema.
- Unknown names, duplicate IDs, invalid arguments, and model-defined tools are
  rejected.

### 2.3 Host-produced tool results

The host records the result of an executed call using `ToolResult`:

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

Allowed result statuses are `success`, `failure`, `denied`, and `cancelled`.
The executor, not model prose, determines this status. A result is never
created to make an unexecuted plan appear successful.

### 2.4 Memory and continuation proposals

Memory proposals may request only `upsert`, `supersede`, or `forget`; local
policy decides whether the request is rejected, kept as a candidate, activated,
merged, or superseded. Secret proposals and untrusted-context claims are not
made safe by protocol fields.

Continuation proposals contain a bounded label, dotted intent, known fields,
and missing field names. The host assigns task identity, workspace/terminal
scope, timestamps, lifecycle, and confirmation state.

## 3. Provider adapter contract

A provider implements:

```python
class BaseProvider(ABC):
    def complete(self, request: ProviderRequest) -> ProviderResult:
        ...
```

`ProviderRequest` contains the selected model, ordered `ProviderMessage`
values, registered `ProviderToolDefinition` values, and the local response
schema where structured fallback is used. `ProviderResult` contains a
validated `AssistantDecision`, the normalized assistant message, and optional
usage information.

Provider adapters may use native function calling or validated structured JSON,
but both paths must produce the same normalized decision. They must not:

- execute tools;
- write memory, configuration, or action records;
- decide risk or confirmation;
- expose provider credentials to tools or model context;
- convert provider prose into an unvalidated successful action.

Provider retries and protocol repair remain bounded by host configuration.

## 4. Tool contract

A built-in or future explicitly installed tool is represented by an immutable
`ToolSpec` containing:

- stable dotted lowercase `name` and non-empty `description`;
- a Pydantic `argument_model`;
- host-owned minimum risk;
- an argument-validated executor;
- an exact preview builder;
- optional dynamic risk and current-state readers;
- declared permissions, timeout, output limit, dry-run support, and rollback
  capability.

Executors receive a narrow `ToolContext` containing request identity, the
resolved working directory, dry-run state, deadline, approved artifact writer,
and protected paths. The context does not contain the provider, API key,
configuration store, unrestricted application internals, or a registry mutator.

Executors return `ToolOutcome` values. They do not choose process exit codes,
write durable memory directly, or report success before the underlying action
has completed.

`ToolPreview` identifies the planned effect, details, affected paths, and
external destinations. Mutating, destructive, privileged, or external-effect
operations pass through host policy and exact-plan confirmation. Approval is
bound to the normalized arguments and relevant current state for the current
invocation only.

## 5. Registry and generated-code boundary

Application composition may register reviewed tools in `ToolRegistry`. At the
start of an invocation the application creates a `ToolRegistrySnapshot`; the
snapshot is immutable and is the only registry visible to the orchestrator.

During an invocation:

- a model may select and sequence tools from that snapshot;
- a model may not register a tool or modify its schema;
- generated source, manifests, and dependencies cannot become live capability;
- unknown tool names are rejected;
- a successful earlier call does not authorize a later call;
- every call is independently validated, policy-checked, bounded, and audited.

Nage 2.0 has no runtime plugin loader, dependency installer, general shell
fallback, or AI-authored live tool path. A requested future tool may be
represented as inactive text, but it is not imported, enabled, or executed in
the creating invocation.

## 6. Change checklist

Before changing a frozen interface, maintainers must:

1. identify whether the change is a bug fix or a contract change;
2. add or update rejection, compatibility, and security tests;
3. update this document and the relevant scope/plan decision;
4. review provider, tool, policy, migration, and packaging effects;
5. decide whether the protocol version or release target must change.

No interface change may be hidden in a provider adapter, generated schema, or
plugin implementation.
