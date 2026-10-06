# tiny_service — planted defects

Answer key for the offline end-to-end tests. **Never part of the assessed repo**: the
`tiny_service` pytest fixture copies the repo without this file, so evaluators can't read it.

Location forms: `path:line` (the line contains Marker), `injected:path:line` (written by the
fixture at test time, so no secret or vulnerable pin is ever committed), `absent` (the defect is
something missing). `tests/unit/test_tiny_service_fixture.py` checks every row.

## Defects

| ID | Check | Expected | Location | Marker | Defect |
|---|---|---|---|---|---|
| D01 | SEC-01 | fail | injected:app/settings_local.py:1 | AWS_ACCESS_KEY_ID | AWS access key in source (random per test run) |
| D02 | SEC-02 | fail | injected:requirements.txt:1 | PyYAML==5.3 | Dependency with a critical advisory (CVE-2020-14343) |
| D03 | SEC-05 | fail | app/api/users.py:34 | text(f"SELECT | SQL built with an f-string from the `name` query parameter |
| D04 | SEC-03 | fail | app/api/users.py:34 | text(f"SELECT | Same query, expected as a high-severity semgrep finding (confirm in M1.4) |
| D05 | SEC-04 | partial | app/api/users.py:42 | await request.json() | PATCH applies raw JSON fields without validation; POST uses a model |
| D06 | SEC-06 | fail | app/main.py:11 | allow_origins=["*"] | Wildcard CORS origins combined with `allow_credentials=True` |
| D07 | SEC-07 | fail | absent | | No Dependabot or Renovate configuration |
| D08 | AUTH-01 | fail | app/api/users.py:50 | @router.delete | No authentication on any route, including delete |
| D09 | AUTH-05 | fail | app/models.py:13 | password: Mapped[str] | Password stored as given; `create_user` assigns it unchanged (users.py:15) |
| D10 | DATA-01 | fail | app/db.py:15 | create_all | Schema created with `create_all`; no migrations |
| D11 | DATA-02 | fail | app/db.py:4 | DATABASE_URL = | Connection string with credentials hardcoded |
| D12 | DATA-05 | fail | app/models.py:13 | password: Mapped[str] | Sensitive field stored unprotected (same column as D09) |
| D13 | LOG-01 | fail | absent | | No logging framework; output goes through `print` |
| D14 | LOG-02 | fail | app/main.py:23 | print("tiny-service started") | `print` in production code (also users.py:18, services/notify.py:5) |
| D15 | LOG-04 | fail | app/api/users.py:18 | print(f"created user | Plaintext password written to output |
| D16 | LOG-03 | fail | absent | | No request correlation or trace context |
| D17 | TEST-03 | fail | .github/workflows/ci.yml:13 | run: ruff check | CI lints but never runs the tests |
| D18 | TEST-04 | fail | absent | | No coverage configuration |
| D19 | TEST-05 | fail | tests/test_users.py:15 | assert True | Tests assert nothing meaningful (also line 10: `assert response`) |
| D20 | CI-02 | partial | .github/workflows/ci.yml:13 | run: ruff check | Lint and build steps, no test step |
| D21 | CI-04 | fail | .github/workflows/ci.yml:2 | on: [push] | No `permissions:` at workflow or job level |
| D22 | CI-05 | fail | .github/workflows/ci.yml:20 | docker/login-action@v3 | Third-party action pinned to a tag, not a SHA |
| D23 | CI-06 | fail | .github/workflows/ci.yml:15 | deploy: | Deploy job without environment, approval or branch protection |
| D24 | CI-07 | fail | .github/workflows/ci.yml:24 | password: tinyservice | Registry password in plaintext in the workflow |
| D25 | CTR-01 | fail | Dockerfile:1 | FROM python:latest | No `USER`; the only stage runs as root |
| D26 | CTR-02 | fail | Dockerfile:1 | FROM python:latest | Base image `latest` |
| D27 | CTR-03 | fail | Dockerfile:1 | FROM python:latest | Single-stage build |
| D28 | CTR-04 | fail | absent | | No `.dockerignore` |
| D29 | CTR-05 | fail | Dockerfile:1 | FROM python:latest | No `HEALTHCHECK`; compose services have no healthcheck |
| D30 | CTR-07 | fail | Dockerfile:2 | MAINTAINER | Deprecated `MAINTAINER` (hadolint DL4000, error level) |
| D31 | PERF-01 | fail | app/api/users.py:59 | requests.get( | Blocking HTTP call inside an `async def` handler |
| D32 | PERF-02 | fail | app/api/users.py:26 | for user in users: | N+1: one orders query per user |
| D33 | PERF-04 | fail | app/api/users.py:24 | users = session.scalars(select(User)).all() | List endpoint returns every row, no pagination |
| D34 | PERF-05 | fail | app/api/users.py:59 | requests.get( | Outbound call without timeout or retry |
| D35 | PERF-06 | fail | docker-compose.yml:2 | api: | Compose services without resource limits |
| D36 | STR-04 | fail | app/services/billing.py:1 | from app.services.notify | Circular import with services/notify.py:1 |
| D37 | STR-06 | fail | pyproject.toml:5 | dependencies = [ | Manifest without a lockfile |
| D38 | DOC-01 | partial | README.md:5 | make run | README states purpose and a run command, no setup |
| D39 | DOC-06 | fail | README.md:5 | make run | README refers to `make run`; there is no Makefile |
| D40 | DOC-03 | fail | absent | | No architecture docs or ADRs |
| D41 | DOC-04 | fail | absent | | No docstrings on public functions |
| D42 | DOC-05 | fail | absent | | No CONTRIBUTING or CHANGELOG |

## Controls (expected not to be defects)

| ID | Check | Expected | Location | Marker | Why |
|---|---|---|---|---|---|
| C01 | LOG-05 | pass | app/main.py:26 | @app.get("/health") | Health endpoint present |
| C02 | DOC-02 | pass | app/main.py:7 | app = FastAPI( | FastAPI generates OpenAPI docs |
| C03 | TEST-01 | pass | tests/test_users.py:8 | def test_health | A test suite exists |
| C04 | CI-01 | pass | .github/workflows/ci.yml:1 | name: ci | A CI pipeline exists |
| C05 | CTR-06 | not_applicable | absent | | No IaC or Kubernetes manifests |
