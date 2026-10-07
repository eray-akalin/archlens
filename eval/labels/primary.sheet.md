# Label sheet: `primary` @ `f27b47215078`

Label only checks whose truth is clear on the unmutated repo — about 15, spread over
at least 6 metrics. Set `label:` to pass, partial, fail or not_applicable, and `path:`
to the file that proves it (empty for an absence). Leave `label:` empty to skip a check.
Then run `archlens eval label-sheet primary --read`.

Current findings: run `01M4B8VKK81AX8B06Y1PKBTFV8` on `a684aefbf2cb`.

## Authentication & authorization (`auth`)

### AUTH-01 — Authentication enforced on API entrypoints

llm, critical.
Current: **pass** (verified, scored) — Authentication is enforced via an OAuth2 dependency and current_user dependency used on protected routes (get_current_user in deps and routes accept CurrentUser or use Depends(get_current_active_superuser)).
Evidence: `backend/app/api/deps.py:16-33`, `backend/app/api/deps.py:30-36`, `backend/app/api/routes/items.py:13-16`, `backend/app/api/routes/users.py:32-36`

- label: pass
- path: backend/app/api/routes/items.py

### AUTH-02 — Unauthenticated routes are explicit and intentional

llm, high.
Current: **pass** (verified, scored) — Unauthenticated endpoints are explicit and intentional (login/access-token and users/signup are public routes for obtaining tokens and registering users).
Evidence: `backend/app/api/routes/login.py:23-26`, `backend/app/api/routes/users.py:146-151`, `backend/app/api/routes/login.py:53-57`

- label:
- path:

### AUTH-03 — Authorization protects privileged operations

llm, high.
Current: **pass** (verified, scored) — Privileged operations require role checks (get_current_active_superuser) and ownership checks are enforced on item/user operations (superuser dependency and owner_id checks).
Evidence: `backend/app/api/deps.py:51-57`, `backend/app/api/routes/users.py:32-36`, `backend/app/api/routes/items.py:53-57`, `backend/app/api/routes/items.py:86-91`

- label:
- path:

### AUTH-04 — Tokens and sessions validated with vetted libraries

llm, high.
Current: **pass** (verified, scored) — JWT tokens are created and validated with the pyjwt library using a secret and an algorithm constant (create_access_token sets exp; get_current_user calls jwt.decode with SECRET_KEY and algorithms).
Evidence: `backend/app/core/security.py:19-26`, `backend/app/api/deps.py:30-34`, `backend/app/core/security.py:22-25`

- label:
- path:

### AUTH-05 — Stored passwords hashed with a slow KDF

llm, high.
Current: **pass** (verified, scored) — Passwords are hashed using pwdlib with Argon2 and Bcrypt backends; get_password_hash and verify_password use that library.
Evidence: `backend/app/core/security.py:11-16`, `backend/app/core/security.py:29-36`, `backend/app/api/routes/users.py:103-111`

- label: pass
- path: backend/app/core/security.py

### AUTH-06 — Session cookies set HttpOnly, Secure, SameSite

llm, medium.
Current: **not_applicable** (unverified) — The service issues and validates bearer JWTs (OAuth2PasswordBearer + Token responses) and does not set session cookies, so cookie flags do not apply.
Evidence: `backend/app/api/deps.py:16-18`, `backend/app/api/routes/login.py:23-26`, `backend/app/api/routes/login.py:36-41`

- label:
- path:

## CI/CD (`cicd`)

### CI-01 — A CI pipeline exists

deterministic, high.
Current: **pass** (verified, scored) — 15 CI workflows (github_actions): .github/workflows/add-to-project.yml, .github/workflows/bump-pre-commit-hooks.yml, .github/workflows/create-draft-release.yml.
Evidence: ast:ci `CI workflows` → 15, `.github/workflows/add-to-project.yml:1-1`, `.github/workflows/bump-pre-commit-hooks.yml:1-1`, `.github/workflows/create-draft-release.yml:1-1`, `.github/workflows/deploy-docker-compose.yml:1-1`

- label:
- path:

### CI-02 — The pipeline runs build, lint and tests

deterministic, medium.
Current: **pass** (verified, scored) — CI runs build, lint, test steps.
Evidence: ast:ci `CI stages ['build', 'lint', 'test']` → 3, `.github/workflows/deploy-docker-compose.yml:36-36`, `.github/workflows/pre-commit.yml:60-60`, `.github/workflows/playwright.yml:80-80`

- label:
- path:

### CI-03 — Workflows are free of actionlint errors

deterministic, low.
Current: **pass** (verified, scored) — actionlint reported 0 errors (allowed: 0).
Evidence: actionlint `workflow errors` → 0

- label:
- path:

### CI-04 — Workflow tokens use least-privilege permissions

deterministic, medium.
Current: **pass** (verified, scored) — All 15 GitHub workflows restrict token permissions.
Evidence: ast:ci `workflows without restricted `permissions`` → 0, `.github/workflows/add-to-project.yml:1-1`, `.github/workflows/bump-pre-commit-hooks.yml:1-1`, `.github/workflows/create-draft-release.yml:1-1`, `.github/workflows/deploy-docker-compose.yml:1-1`

- label: pass
- path: .github/workflows/test-backend.yml

### CI-05 — Third-party actions are pinned to a commit SHA

deterministic, medium.
Current: **pass** (verified, scored) — All 23 third-party action references are pinned to a commit SHA.
Evidence: ast:ci `third-party actions not pinned to a commit SHA` → 0, `.github/workflows/bump-pre-commit-hooks.yml:30-30`, `.github/workflows/bump-pre-commit-hooks.yml:39-39`, `.github/workflows/create-draft-release.yml:31-31`, `.github/workflows/deploy.yml:26-26`

- label:
- path:

### CI-06 — Deployments are gated

llm, medium.
Current: **pass** (verified, scored) — All deploy jobs are gated: deploy.yml runs only on pushes to master or workflow_dispatch, deploy-docker-compose.yml runs only on workflow_dispatch, and create-draft-release runs only for merged PRs with the 'release' label.
Evidence: `.github/workflows/deploy.yml:3-9`, `.github/workflows/deploy.yml:14-18`, `.github/workflows/deploy-docker-compose.yml:3-5`, `.github/workflows/create-draft-release.yml:3-12`

- label:
- path:

### CI-07 — Cloud and registry credentials come from OIDC or a secret store

llm, high.
Current: **pass** (verified, scored) — All cloud/registry/deploy credentials are supplied from GitHub secrets or built-in tokens/vars (secrets.DATABASE_URL, SECRET_KEY, FIRST_SUPERUSER_PASSWORD, FASTAPI_CLOUD_TOKEN, FASTAPI_CLOUD_APP_ID, and github.token), not as literals.
Evidence: `.github/workflows/deploy.yml:44-49`, `.github/workflows/deploy.yml:50-54`, `.github/workflows/deploy-docker-compose.yml:19-29`, `.github/workflows/create-draft-release.yml:43-46`

- label:
- path:

## Containers & deployment (`container`)

### CTR-01 — Container runs as non-root

deterministic, high.
Current: **fail** (verified, scored) — Missing a non-root final USER in 2 of 2 Dockerfiles: backend/Dockerfile, frontend/Dockerfile.playwright.
Evidence: ast:docker `Dockerfiles without a non-root final USER` → 2, `backend/Dockerfile:19-19`, `frontend/Dockerfile.playwright:1-1`

- label: fail
- path: backend/Dockerfile

### CTR-02 — Base images pinned

deterministic, medium.
Current: **pass** (verified, scored) — Every base image in 2 Dockerfiles has a tag or digest.
Evidence: ast:docker `unpinned base images` → 0, `backend/Dockerfile:19-19`, `frontend/Dockerfile.playwright:1-1`

- label: pass
- path: backend/Dockerfile

### CTR-03 — Multi-stage build

deterministic, low.
Current: **partial** (verified, scored) — Missing a multi-stage build in 1 of 2 Dockerfiles: frontend/Dockerfile.playwright.
Evidence: ast:docker `Dockerfiles without a multi-stage build` → 1, `frontend/Dockerfile.playwright:1-1`

- label:
- path:

### CTR-04 — .dockerignore present

deterministic, low.
Current: **pass** (verified, scored) — Found .dockerignore, backend/.dockerignore, frontend/.dockerignore.
Evidence: files `any of ['**/.dockerignore']` → 3, `.dockerignore:1-1`, `backend/.dockerignore:1-1`, `frontend/.dockerignore:1-1`

- label:
- path:

### CTR-05 — Health check or probes defined

deterministic, medium.
Current: **pass** (verified, scored) — Health checks defined in compose.yml.
Evidence: ast:docker `HEALTHCHECK instructions, compose healthchecks, k8s probes` → 2, `compose.yml:20-20`, `compose.yml:49-49`

- label:
- path:

### CTR-06 — IaC and manifests free of high-severity misconfigurations

deterministic, high.
Does not apply to this repo's profile.
Current: no finding.

- label:
- path:

### CTR-07 — Dockerfile free of hadolint errors

deterministic, low.
Current: **pass** (verified, scored) — hadolint reported 0 errors (allowed: 0).
Evidence: hadolint `error-level findings` → 0

- label:
- path:

## Data management (`data`)

### DATA-01 — Schema changes managed by migrations

deterministic, high.
Current: **pass** (verified, scored) — Found backend/app/alembic/versions/.keep, backend/app/alembic/versions/1a31ce608336_add_cascade_delete_relationships.py, backend/app/alembic/versions/9c0a54914c78_add_max_length_for_string_varchar_.py.
Evidence: files `any of ['**/alembic/versions/**', '**/migrations/*.py', '**/migrations/*.sql', …` → 6, `backend/app/alembic/versions/1a31ce608336_add_cascade_delete_relationships.py:1-1`, `backend/app/alembic/versions/9c0a54914c78_add_max_length_for_string_varchar_.py:1-1`, `backend/app/alembic/versions/d98dd8ec85a3_edit_replace_id_integers_in_all_models_.py:1-1`, `backend/app/alembic/versions/e2412789c190_initialize_models.py:1-1`

- label: pass
- path: backend/app/alembic/versions/

### DATA-02 — Connection settings come from configuration or a secret store

llm, high.
Current: **pass** (verified, scored) — The database URL is provided via a pydantic BaseSettings Settings object (DATABASE_URL: PostgresDsn) that reads env_file (../.env) and settings = Settings() is instantiated, so connection settings come from configuration/environment.
Evidence: `backend/app/core/config.py:15-21`, `backend/app/core/config.py:29-41`, `backend/app/core/config.py:88-91`

- label: pass
- path: backend/app/core/config.py

### DATA-03 — Data access isolated in a dedicated layer

llm, medium.
Current: **pass** (verified, scored) — Database queries and writes live in a dedicated crud module (e.g. create_user in app/crud.py) and API route handlers call that layer (routes/users.py calls crud.create_user), so data access is isolated in a dedicated layer.
Evidence: `backend/app/crud.py:10-17`, `backend/app/api/routes/users.py:61-69`

- label:
- path:

### DATA-04 — Multi-step writes use transactions

llm, medium.
Current: **pass** (verified, scored) — A multi-step delete (delete items then delete user) is executed and committed once (single session.commit()), so the multi-step write runs as a single commit (atomic unit of work).
Evidence: `backend/app/api/routes/users.py:228-231`

- label:
- path:

### DATA-05 — Sensitive fields protected

llm, low.
Current: **pass** (verified, scored) — Sensitive passwords are stored as a hashed_password field on the User model and the crud layer sets hashed_password before commit; the public API model (UserPublic) does not expose hashed_password.
Evidence: `backend/app/models.py:51-55`, `backend/app/crud.py:10-13`, `backend/app/models.py:59-62`

- label:
- path:

## Documentation (`documentation`)

### DOC-01 — README covers purpose, setup and how to run

llm, medium.
Current: **partial** (verified, scored) — The top-level README clearly states the project's purpose and links to setup/run docs, but it does not include concrete prerequisites or install/run commands itself (it points to backend/frontend/development docs instead).
Evidence: `README.md:6-15`, `README.md:58-66`, `README.md:76-80`

- label:
- path:

### DOC-02 — API documented

deterministic, medium.
Current: **pass** (verified, scored) — API docs are generated by fastapi.
Evidence: files `OpenAPI/Swagger files or doc-generating frameworks` → 2, `backend/pyproject.toml:7-7`

- label:
- path:

### DOC-03 — Architecture docs or ADRs

deterministic, low.
Current: **fail** (verified, scored) — No architecture documents or ADRs found.
Evidence: files `any of ['docs/**/adr*/**', 'docs/**/architecture*', 'ARCHITECTURE*', 'docs/**/d…` → 0

- label: fail
- path:

### DOC-04 — Docstring or comment coverage of public functions

deterministic, low.
Current: **fail** (verified, scored) — 24 of 93 public functions (26%) have docstrings or doc comments.
Evidence: ast:metrics `documented 24 / public 93 functions` → 24

- label:
- path:

### DOC-05 — Contributing guide or changelog

deterministic, low.
Current: **pass** (verified, scored) — Found CONTRIBUTING.md, release-notes.md.
Evidence: files `any of ['**/CONTRIBUTING*', '**/CHANGELOG*', '**/HISTORY*', '**/release-notes*'…` → 2, `CONTRIBUTING.md:1-1`, `release-notes.md:1-1`

- label:
- path:

### DOC-06 — README commands refer to things that exist

llm, low.
Current: **pass** (verified, scored) — A command referenced in the top-level README points to backend documentation and that backend README contains the referenced commands (e.g. 'uv run fastapi dev').
Evidence: `README.md:62-66`, `backend/README.md:18-24`

- label:
- path:

## Logging & observability (`logging`)

### LOG-01 — A logging framework is used

deterministic, medium.
Current: **pass** (verified, scored) — Imported: logging, logging.config.
Evidence: ast:imports `imports of logging, structlog, loguru, winston, pino, bunyan, ...` → 3, `backend/app/alembic/env.py:2-2`, `backend/app/initial_data.py:1-1`, `backend/app/utils.py:1-1`

- label: pass
- path: backend/app/utils.py

### LOG-02 — No print/console output in production code

deterministic, low.
Current: **fail** (verified, scored) — 5 print/console calls vs 3 log calls in production code (62% of output calls).
Evidence: ast:logging `print/console calls in production code` → 5, `frontend/src/hooks/useCopyToClipboard.ts:13-13`, `frontend/src/hooks/useCopyToClipboard.ts:25-25`, `scripts/add_latest_release_date.py:35-35`, `scripts/add_latest_release_date.py:29-29`

- label:
- path:

### LOG-03 — Request correlation or trace context propagated

llm, medium.
Current: **partial** (verified, scored) — The backend initializes Sentry tracing (so trace context/instrumentation exists) but logging calls in the code do not include any trace/correlation ID or propagate an explicit request ID in logs.
Evidence: `backend/app/main.py:18-19`, `backend/app/utils.py:15-17`

- label:
- path:

### LOG-04 — Sensitive data not logged

llm, high.
Current: **pass** (verified, scored) — Representative log calls do not write passwords, tokens, or personal data — e.g. simple informational messages are logged (no secrets in the logged string examples).
Evidence: `backend/app/utils.py:55-57`, `backend/app/initial_data.py:16-19`

- label:
- path:

### LOG-05 — Telemetry SDK or health endpoint present

deterministic, medium.
Current: **pass** (verified, scored) — Observability present: route /utils/health-check/.
Evidence: ast:routes `telemetry imports or health/metrics routes` → 1, `backend/app/api/routes/utils.py:29-29`

- label:
- path:

## Performance & resilience (`performance`)

### PERF-01 — Non-blocking IO in request paths

llm, medium.
Current: **pass** (verified, scored) — Most API handlers are synchronous (run in threadpool) and the only async handler (health_check) is trivial and returns immediately, so request paths do not use blocking calls in async handlers.
Evidence: `backend/app/api/routes/items.py:13-16`, `backend/app/api/routes/utils.py:29-31`

- label:
- path:

### PERF-02 — No N+1 query patterns

llm, medium.
Current: **pass** (verified, scored) — List endpoints load rows with a single query using offset/limit (no per-row queries); related data is not lazily queried per item in the shown list handlers.
Evidence: `backend/app/api/routes/items.py:21-27`, `backend/app/api/routes/items.py:36-45`

- label:
- path:

### PERF-03 — Caching strategy for expensive reads

llm, low.
Current: **fail** (verified, scored) — I found no caching (in-memory, Redis, memoization, or HTTP caching headers) for expensive or repeated reads in the backend codebase.
Evidence: absence_probe `regex:(?i)\bcache|redis|memcache|lru_cache|@cached|Cache-Control|etag in **/*.{…` → 0

- label: fail
- path:

### PERF-04 — List endpoints paginated

llm, medium.
Current: **pass** (verified, scored) — Collection endpoints accept skip/limit parameters and the SQL queries explicitly use offset(...) and limit(...), bounding returned result sizes.
Evidence: `backend/app/api/routes/items.py:13-16`, `backend/app/api/routes/items.py:24-26`, `backend/app/api/routes/users.py:37-48`

- label: pass
- path: backend/app/api/routes/items.py

### PERF-05 — Outbound calls have timeouts and retries

llm, high.
Current: **partial** (rejected) — The codebase depends on httpx, and email/send utilities call out to external services, but I could not find explicit per-call timeouts or retry/backoff configuration in the inspected backend code; some outbound behavior is delegated to utilities like send_email.

- label:
- path:

### PERF-06 — Resource limits configured for deployments

deterministic, low.
Current: **fail** (verified, scored) — 14 of 14 workloads have no resource limits: proxy (compose.deploy.yml), db (compose.deploy.yml), adminer (compose.deploy.yml).
Evidence: ast:deploy `workloads without resource limits` → 14, `compose.deploy.yml:3-3`, `compose.deploy.yml:34-34`, `compose.deploy.yml:37-37`, `compose.deploy.yml:47-47`

- label: fail
- path: compose.yml

## Security (`security`)

### SEC-01 — No secrets committed to the repository

deterministic, critical.
Current: **pass** (verified, scored) — gitleaks found no secrets outside excluded paths.
Evidence: gitleaks `secrets outside ['**/test*/**', '**/*example*', '**/fixtures/**']` → 0

- label:
- path:

### SEC-02 — No dependencies with known critical or high vulnerabilities

deterministic, critical.
Current: **pass** (verified, scored) — No vulnerable dependencies at or above medium severity.
Evidence: osv-scanner `vulnerable dependencies with severity >= medium` → 0

- label:
- path:

### SEC-03 — No high-severity static-analysis findings

deterministic, high.
Current: **partial** (verified, scored) — 2 static-analysis findings at or above medium severity (worst: medium): missing-user at backend/Dockerfile:65 (medium), direct-use-of-jinja2 at backend/app/utils.py:30 (medium).
Evidence: `backend/Dockerfile:65-65`, `backend/app/utils.py:30-30`

- label:
- path:

### SEC-04 — Untrusted input is validated at system boundaries

llm, high.
Current: **pass** (verified, scored) — Handlers validate untrusted input using Pydantic/typed models: private.create_user accepts PrivateUserCreate, items.create_item accepts ItemCreate, and users.create_user accepts UserCreate, ensuring schema-based validation at boundaries.
Evidence: `backend/app/api/routes/private.py:16-24`, `backend/app/api/routes/items.py:61-64`, `backend/app/api/routes/users.py:54-58`

- label:
- path:

### SEC-05 — Database queries are parameterized

llm, critical.
Current: **pass** (verified, scored) — Database access uses SQLModel/ORM queries with bound parameters (no string-built SQL); e.g., get_user_by_email builds a select(User).where(User.email == email) and executes it via session.exec().
Evidence: `backend/app/crud.py:34-37`

- label: pass
- path: backend/app/crud.py

### SEC-06 — CORS and security headers are safely configured

llm, medium.
Current: **partial** (verified, scored) — CORS is configured restrictively (allow_origins set to settings.FRONTEND_HOST, not '*') but there is no middleware shown that sets common security headers (e.g., Helmet/Starlette security middleware) in the repo.
Evidence: `backend/app/main.py:27-33`

- label:
- path:

### SEC-07 — Automated dependency updates are configured

deterministic, low.
Current: **pass** (verified, scored) — Found .github/dependabot.yml.
Evidence: files `any of ['.github/dependabot.yml', '.github/dependabot.yaml', 'renovate.json', '…` → 1, `.github/dependabot.yml:1-1`

- label:
- path:

## Code structure (`structure`)

### STR-01 — Clear module and layer boundaries

llm, medium.
Current: **pass** (verified, scored) — API route handlers delegate business/data work to separate modules (e.g. login route calls app.crud.authenticate and other utilities) and DB/session is provided via dependency helpers, showing layered separation.
Evidence: `backend/app/api/routes/login.py:30-32`, `backend/app/api/deps.py:21-27`

- label:
- path:

### STR-02 — No oversized source files

deterministic, medium.
Current: **pass** (verified, scored) — No non-generated source file exceeds 1000 LOC (135 files measured).
Evidence: ast:metrics `files over 1000 LOC` → 0

- label:
- path:

### STR-03 — Function complexity under control

deterministic, medium.
Current: **pass** (verified, scored) — 0 of 580 functions (0.0%) exceed CCN 15.
Evidence: lizard `functions with CCN > 15` → 0

- label:
- path:

### STR-04 — No circular imports between internal modules

deterministic, medium.
Current: **pass** (verified, scored) — No import cycles among 131 internal modules.
Evidence: ast:imports `cycles between internal modules` → 0

- label:
- path:

### STR-05 — Configuration separated from code

llm, medium.
Current: **pass** (verified, scored) — Environment- and deployment-specific values are provided via a Settings object (pydantic-settings) and consumed through settings; secrets and DATABASE_URL are declared as settings rather than hard-coded.
Evidence: `backend/app/core/config.py:15-23`, `backend/app/core/config.py:30-33`, `backend/app/main.py:21-24`

- label:
- path:

### STR-06 — Dependency manifest with a lockfile

deterministic, low.
Current: **fail** (verified, scored) — 3 of 5 manifests have no lockfile and unpinned dependencies: backend/pyproject.toml, frontend/package.json, packages/react-email/package.json.
Evidence: ast:manifests `manifests without a lockfile` → 3, `backend/pyproject.toml:1-1`, `frontend/package.json:1-1`, `packages/react-email/package.json:1-1`

- label: pass
- path: uv.lock

## Testing (`testing`)

### TEST-01 — A test suite is present

deterministic, high.
Current: **pass** (verified, scored) — 11 test files with 119 tests (js, pytest).
Evidence: ast:tests `test files` → 11, `backend/tests/api/routes/test_items.py:10-10`, `backend/tests/api/routes/test_login.py:16-16`, `backend/tests/api/routes/test_private.py:8-8`, `backend/tests/api/routes/test_users.py:15-15`

- label: pass
- path: backend/tests/api/routes/test_items.py

### TEST-02 — Test code is proportionate to source code

deterministic, medium.
Current: **partial** (verified, scored) — Test-to-source LOC ratio is 0.24 (1836 test / 7625 source LOC); pass at 0.3, partial at 0.1.
Evidence: ast:metrics `test LOC 1836 / source LOC 7625` → 27

- label:
- path:

### TEST-03 — Tests are executed in CI

deterministic, high.
Current: **pass** (verified, scored) — 2 CI steps run tests: docker compose run --rm playwright bunx playwright test --fail-on-flaky-tests --trace=retain-on-failure --shard=${{ matrix.shardIndex }}/${{ matrix.shardTotal }}, uv run bash scripts/tests-start.sh "Coverage for ${{ github.sha }}".
Evidence: ast:ci `CI steps that run tests` → 2, `.github/workflows/playwright.yml:80-80`, `.github/workflows/test-backend.yml:33-33`

- label: pass
- path: .github/workflows/test-backend.yml

### TEST-04 — Test coverage is measured

deterministic, low.
Current: **pass** (verified, scored) — Coverage is configured: CI step in .github/workflows/test-backend.yml, backend/pyproject.toml.
Evidence: ast:ci `coverage config files, coverage settings, CI coverage flags` → 2, `backend/pyproject.toml:67-67`, `.github/workflows/test-backend.yml:43-43`

- label:
- path:

### TEST-05 — Tests contain meaningful assertions

llm, medium.
Current: **pass** (verified, scored) — Most sampled tests make meaningful assertions about specific outcomes (HTTP status and response body, or UI visibility/text); examples include response status/body checks in backend tests and UI expectations in frontend Playwright tests.
Evidence: `backend/tests/api/routes/test_items.py:14-23`, `frontend/tests/login.spec.ts:48-50`

- label:
- path:
