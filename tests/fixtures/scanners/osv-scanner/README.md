# osv-scanner fixture

- Tool version: 2.6.0
- Captured: 2026-10-06 by `scripts/capture_scanner_fixtures.py`
- Input: materialized `tests/fixtures/repos/tiny_service` (with injected D01/D02)
- Command (`<repo>`, `<work>`, `<tools>` are placeholders):

  ```
  osv-scanner scan source --recursive --no-resolve --allow-no-lockfiles --config <work>/osv-scanner.toml --format json --verbosity error <repo>
  ```

Output is redacted and the input root is replaced by `__ROOT__`.
