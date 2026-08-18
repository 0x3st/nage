# Nage 1.x to 2.0 migration and rollback

Nage 2.0 detects the legacy `SETT`, `MEMO`, and `HIST` files in the Nage data directory. Before importing anything, it creates a private, timestamped backup under `backups/legacy-v1-*` with a hash manifest.

Migration behavior:

- Valid provider model, endpoint, and API key values from `SETT` move to the restricted v2 configuration file. Secrets never enter SQLite, memory, reports, or prompts.
- Non-empty, normalized, exact-deduplicated `MEMO` entries become active memories. Entries that look secret or highly sensitive remain only in the backup and are not imported.
- `HIST` remains in the verified backup only. It is never attached to a v2 request or imported as active context.
- A database marker is written only after the corresponding import completes. Repeated launches are idempotent.
- Malformed input stops the affected migration while preserving the backup and omitting the completion marker.

The migration report records counts, statuses, and the backup location, but not API keys, history text, or memory bodies.

## Restore legacy files

Use the migration coordinator's rollback operation with the reported backup path. It verifies hashes before atomically restoring the legacy files. By default, restoration should require explicit overwrite approval.

Restoring legacy files does not silently delete the v2 database or configuration. Keep the backup until the migrated installation has been manually verified. To abandon v2 state completely, first copy the data directory, restore the legacy files, and then remove v2-only files manually while Nage is not running.
