---
paths:
  - "src/archlens/ingest/**"
  - "src/archlens/tools/**"
  - "src/archlens/facts/**"
  - "src/archlens/api/**"
  - "src/archlens/worker/**"
---

# Security-sensitive code

Full spec: `docs/SECURITY.md`.

- Never execute anything from the target repo. Scanners: static modes only; no package manager
  resolution that runs install scripts.
- Clone hardening is mandatory: https only, `--depth 1`, no submodules, LFS smudge off, hooks path
  set to an empty dir, size/file-count/time limits from `IngestLimits`.
- Every path that reaches the filesystem goes through `archlens.tools.paths.resolve_in_snapshot()`,
  which resolves symlinks and rejects anything outside the snapshot root.
- Tool outputs are capped (lines and bytes) and line-numbered.
- Secret values are redacted with `archlens.security.redact` before they reach prompts, logs,
  stored facts, or reports. Store only fingerprints of secrets.
- The API accepts only `https://github.com/...` (and configured hosts) URLs; reject IP literals,
  credentials in URLs, and non-default ports.
