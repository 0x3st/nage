# ADR 0001: Nage 2.0 Foundation Decisions

**Status:** Accepted for implementation

**Target:** Nage 2.0.0

## Context

Nage 2.0 replaces the prompt-only assistant with a bounded, tool-using architecture. The first implementation work needs stable decisions for compatibility, validation, providers, local dependencies, exit behavior, and release handling.

## Decisions

### Python support

Keep the current Python 3.8–3.13 support target for initial development. New syntax and direct dependencies must work across that range. If the range cannot be maintained, changing it requires a separate documented decision before the first alpha.

Python 3.14 may be used for local development experiments but is not part of the initial release matrix.

### Runtime schema validation

Use Pydantic 2 as a declared direct dependency when protocol models are introduced. Do not rely on the OpenAI package's transitive Pydantic dependency. The universal lock and CI matrix must verify a compatible resolution for every supported Python version.

### Optional system and file dependencies

- Prefer the standard library for system inspection.
- Add `psutil` directly only when implementing portable process or memory tools.
- Prefer recoverable deletion; evaluate `Send2Trash` before adding the first delete tool.
- Use `pathspec` when implementing `.gitignore` and `.nageignore` matching rather than maintaining a partial custom parser.
- Add each dependency only in the phase that uses it and verify its platform wheels and Python requirements first.

Local resolver/import verification passed on Python 3.8–3.13 for these candidate ranges:

- `pydantic>=2,<3` (`2.10.6` on Python 3.8; `2.11.7` on Python 3.9+ in the verification environment)
- `pathspec>=0.12,<1`
- `psutil>=5.9,<8`
- `send2trash>=1.8,<2`

This verifies Python compatibility, not every target-platform wheel; CI and packaging tests remain required when each dependency is introduced.

### Provider baseline

The first alpha supports the OpenAI Chat Completions-compatible API through a provider adapter:

1. Native function/tool calling where the endpoint supports it.
2. Locally validated structured JSON fallback where native tools are unavailable.
3. No Responses API requirement.
4. No regex-based partial JSON streaming.
5. Final-answer streaming may return only after planning and tool behavior are safely separated.

Provider capabilities are explicit configuration or detected failures; Nage must not silently assume every OpenAI-compatible endpoint implements every OpenAI feature.

### Tool registry

The enabled tool registry is snapshotted at invocation start and remains immutable for that invocation. The model may compose registered tools but cannot define schemas, register tools, install dependencies, import generated source, or execute a newly generated tool.

AI-assisted tool authoring is deferred beyond 2.0.0. A future feature may create inactive drafts only and requires a separate scope and isolation review.

### Initial exit-code categories

| Code | Meaning |
| ---: | --- |
| 0 | Completed successfully or answered |
| 2 | CLI usage or configuration error |
| 3 | More user input is required |
| 4 | User declined or cancelled |
| 5 | Provider or protocol failure |
| 6 | Tool execution failed or partially failed |
| 7 | Local policy denied the operation |

Values are provisional until beta but provide the implementation baseline.

### Version handling

Keep package metadata at `1.2.1` until a testable `2.0.0a1` artifact exists. The scope and plan filenames use the target release number, but planning documents do not bump the package.

Do not schedule a separate `1.2.2` release now. A metadata-only patch may still be cut if correcting the published license metadata becomes urgent before the first 2.0 prerelease.

### License

GPL-3.0-only is the canonical license identifier. `LICENSE.txt`, README text, package metadata, classifiers, source distributions, wheels, and installers must agree before release.

## Consequences

- Foundation code must avoid Python syntax newer than 3.8 until the support decision changes.
- Protocol and tool tests cannot depend on paid network calls.
- Generated tool code is treated as ordinary untrusted content.
- New dependencies are introduced deliberately rather than all at once.
- The v1 local formats need fixtures and a reversible migration path.
