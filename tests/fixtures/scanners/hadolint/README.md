# hadolint fixture

- Tool version: 2.15.1
- Captured: 2026-10-06 by `scripts/capture_scanner_fixtures.py`
- Input: materialized `tests/fixtures/repos/tiny_service` (with injected D01/D02)
- Command (`<repo>`, `<work>`, `<tools>` are placeholders):

  ```
  hadolint --config <work>/hadolint.yaml --no-fail --no-color --disable-ignore-pragma -f json <repo>/Dockerfile
  ```

Output is redacted and the input root is replaced by `__ROOT__`.
