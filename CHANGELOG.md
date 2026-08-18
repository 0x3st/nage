# Changelog

## 2.0.0a1 — Alpha preview

Nage 2.0.0a1 is the first public architecture preview of the Nage 2 rewrite.
It is not a stable replacement for Nage 1.x; interfaces, behavior, and local
storage details may change before beta.

### Highlights

- Natural-language one-shot CLI without magic words, slash commands, or plugin subcommands.
- Versioned provider-neutral protocol 2.0 with strict response validation.
- Bounded provider/tool orchestration with fake-provider test support.
- Immutable per-invocation tool registry; generated tools and runtime dependency installation are not supported.
- Host-owned risk policy, exact previews, confirmation, dry-run behavior, and action records.
- Bounded, source-aware context assembly for stdin, files, repositories, and Git context.
- SQLite-backed memories, continuations, artifacts, manifests, and migration state.
- Safe migration and rollback support for legacy `SETT`, `MEMO`, and `HIST` data.
- Built-in system, file, Git, memory, and context capabilities.
- Python 3.8–3.13 test coverage plus Linux, Windows, and macOS packaging smoke tests.
- One-time `[notice]` message after a new installation or upgrade.

### Important limitations

- This is an alpha release. The v2 protocol and tool contract are frozen for the
  current release candidate path, but the broader product is not stable yet.
- Configure an OpenAI-compatible provider before making a request.
- Third-party plugin loading, unrestricted shell execution, AI-authored live
  tools, background work, and persistent chat are out of scope.
- Review the privacy and retention policy of the configured provider before
  sending local context.

### Installation

Stable 1.x remains available through the normal package name. Install this
preview explicitly:

```bash
python -m pip install --pre nage
# or
uv tool install nage==2.0.0a1
```

Existing 1.x local data is backed up before migration. See
[`docs/migration-v2.md`](docs/migration-v2.md) for rollback guidance.
