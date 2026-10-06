# semgrep fixture

- Tool version: 1.179.0
- Captured: 2026-10-06 by `scripts/capture_scanner_fixtures.py`
- Input: materialized `tests/fixtures/repos/tiny_service` (with injected D01/D02)
- Command (`<repo>`, `<work>`, `<tools>` are placeholders):

  ```
  semgrep scan --config <tools>/semgrep/p-default.yml --metrics off --no-git-ignore --disable-nosem --json --quiet --max-target-bytes 2000000 <repo>/.github/workflows/ci.yml <repo>/Dockerfile <repo>/app/__init__.py <repo>/app/api/__init__.py <repo>/app/api/users.py <repo>/app/db.py <repo>/app/main.py <repo>/app/models.py <repo>/app/schemas.py <repo>/app/services/__init__.py <repo>/app/services/billing.py <repo>/app/services/notify.py <repo>/app/settings_local.py <repo>/docker-compose.yml <repo>/tests/test_users.py
  ```

Output is redacted and the input root is replaced by `__ROOT__`.
