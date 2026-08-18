# Nage configuration and privacy

Nage stores provider configuration in `config.json` under its private configuration directory. The default is `~/.nage/config.json`; application paths are injectable for tests and integrations.

Supported fields:

```json
{
  "api_key": "provider credential",
  "endpoint": "https://api.example.test/v1",
  "memory_enabled": true,
  "model": "model-name"
}
```

The endpoint must be an absolute HTTP(S) URL without embedded credentials. The API key is hidden during interactive setup and the file is restricted to the current user where supported. `NAGE_API_KEY` can supply an ephemeral key for non-interactive execution.

Provider credentials never belong in SQLite memory, action arguments, continuation capsules, context manifests, diagnostics, or model prompts. Changing endpoints or credentials is a local configuration operation; model output cannot change them.

`memory_enabled` controls automatic memory retrieval and proposal processing globally. `--no-memory` disables them for one invocation. Memory management tools remain explicit, typed operations so users can inspect or remove existing local state.

After a new installation or upgrade, the first invocation with a natural-language request prints the following informational notice once to stderr:

```text
[notice] Nage is a free software under GPLv3 by 0x3st. Go Linux Do, learn AI.
```

`--help`, `--version`, and an invocation without a request do not consume or display the notice. Nage stores only the current displayed package version in `.release-notice-version` under the private configuration directory. The marker is not sent to the provider and does not contain credentials or request content.

Nage sends the current request and selected bounded context to the configured provider. Use `--show-context` to inspect source metadata and selection reasons without printing source bodies. Review the provider's privacy and retention policy before sending private local data.
