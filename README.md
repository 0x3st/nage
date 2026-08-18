# Nage

[![PyPI - Version](https://img.shields.io/pypi/v/nage)](https://pypi.org/project/nage/)
[![License](https://img.shields.io/github/license/0x3st/nage)](https://github.com/0x3st/nage/LICENSE.txt)

Nage is a one-shot natural-language assistant for the terminal. Run one request, receive one result, and return to the shell:

```bash
nage show me which processes are using the most memory
nage what is using disk space in this project
npm test 2>&1 | nage explain the failure and suggest the safest next step
```

Nage has no persistent chat UI, TUI, magic action words, slash commands, or plugin subcommands. Words such as `please` and `do` have their ordinary meaning and never grant extra permission.

> The repository is being migrated toward Nage 2.0. Package metadata remains at the latest published 1.x version until a testable 2.0 alpha is cut.

## Safety model

The model can answer and propose calls only to typed tools that were registered when the invocation began. Nage application code—not model prose—validates arguments, checks permissions, classifies risk, asks for confirmation, executes tools, records actual outcomes, and chooses the process exit code.

- Read-only inspection can run within the approved workspace.
- File writes, moves, corrections, and other side effects show an exact plan and default to **No**.
- Approval is bound to the validated arguments and relevant current state. A changed file or plan invalidates it.
- Permanent deletion is not provided; the initial delete behavior uses the system trash.
- General model-generated shell strings are not executed.
- The model cannot create, install, register, enable, or immediately execute a generated tool. Generated source is inactive text.
- Nage and Git internal state are protected from generic file-mutation tools.

## Installation

Use an isolated tool environment where practical:

```bash
uv tool install nage
# or
python -m pip install nage
```

## Provider setup

On the first request, Nage asks for the model, OpenAI-compatible endpoint, and API key. API-key input is hidden. For non-interactive use, provide `NAGE_API_KEY`; the environment value is used for that process and is not automatically written to configuration.

Provider credentials are stored only in the restricted local configuration file. They are never stored in SQLite memory, action records, context manifests, or prompts.

## Core options

```text
--dry-run       Preview mutations without executing them
--fresh         Do not attach a continuation capsule
--no-memory     Disable memory retrieval and proposals for this invocation
--show-context  Show selected-source metadata without source bodies
--copy          Explicitly copy the final output
--version       Show the package version
```

There is intentionally no global `--yes` option.

## Context and privacy

Each invocation gets a fresh, bounded context. Nage does **not** send complete history, memory, logs, or repositories by default. It selects small excerpts, manifests, scoped memories, and at most one strongly matched continuation capsule under explicit budgets.

The current top-level request is the only source of user intent. Files, stdin, previous model text, memories, continuation data, and tool results are lower-trust data and cannot authorize actions or memory changes. Likely secret paths, binary files, ignored trees, traversal, and symlink escape are blocked.

Prompts and selected local content are sent to the configured AI provider when needed to answer a request. Review that provider's privacy and retention terms. Context manifests and request records store metadata and hashes rather than full prompts.

Large stdin and tool output use expiring local artifacts. Artifact files and the SQLite database are created with user-only permissions where the platform supports them.

## Memory and continuity

Memory is local, structured, scoped, inspectable, and reversible. The model may propose a stable preference or fact, but local policy rejects secrets, highly sensitive data, temporary details, model inference, and statements originating only from files or tool output. Weak evidence remains an inactive candidate. Active retrieval is lexical/FTS-based and capped to a few items.

Continuation capsules contain only a compact intent, known fields, and missing fields. They do not contain a chat transcript or approval. Hot capsules become dormant and eventually expire; unrelated requests do not inherit them, and ambiguous matches require a task ID.

Natural-language requests can use typed memory tools to inspect, search, correct, forget, clear, restore, activate, or export memory. `--no-memory` is the deterministic per-request privacy control.

## Local data and 1.x migration

Nage 2.0 uses a private data directory (normally `~/.nage/`) containing:

- `config.json` — provider configuration and API key
- `nage.sqlite3` — memories, continuations, action/request metadata, and manifests
- `artifacts/` — expiring large local inputs and outputs
- `backups/legacy-v1-*` — verified migration backups

Legacy `SETT`, `MEMO`, and `HIST` files are backed up before migration. Valid non-sensitive memories are normalized and deduplicated. Secret-looking legacy memory stays only in the backup. Legacy history is preserved in the backup but never becomes active v2 prompt context. See [`docs/migration-v2.md`](docs/migration-v2.md) for migration and rollback details.

## Plugins and tools

Tools are internal typed capabilities, not user-facing commands. Every enabled tool uses the same validation, permission, confirmation, deadline, output-limit, and audit pipeline. Nage 2.0 composes only the immutable set of tools available at invocation start; AI-authored live plugins and runtime dependency installation are out of scope.

## Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | Completed successfully or answered |
| 2 | CLI usage or configuration error |
| 3 | More user input is required |
| 4 | User declined or cancelled |
| 5 | Provider or protocol failure |
| 6 | Tool execution failed or was partial |
| 7 | Local policy denied the operation |

Final requested content goes to stdout. Diagnostics, context manifests, memory/continuation notices, and confirmation prompts go to stderr.

## Development

Tests use temporary data roots and fake providers; pull-request tests do not require a paid network API.

```bash
uv run --extra test pytest -q
```

The supported development target is Python 3.8–3.13 until an explicit compatibility decision changes it.

## License

Nage is free software licensed under [`GPL-3.0-only`](LICENSE.txt).
