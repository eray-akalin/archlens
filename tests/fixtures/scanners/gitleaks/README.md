# gitleaks fixture

- Tool version: 8.30.1
- Captured: 2026-10-06 by `scripts/capture_scanner_fixtures.py`
- Input: materialized `tests/fixtures/repos/tiny_service` (with injected D01/D02)
- Command (`<repo>`, `<work>`, `<tools>` are placeholders):

  ```
  gitleaks dir --config <work>/gitleaks.toml --gitleaks-ignore-path <work>/gitleaks-empty --ignore-gitleaks-allow --redact --no-banner --exit-code 0 --log-level error --report-format json --report-path - <repo>
  ```

Output is redacted and the input root is replaced by `__ROOT__`.
