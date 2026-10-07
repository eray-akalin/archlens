# Rubrics

Ten metrics, one YAML file each in `rubrics/`. `rubrics/security.yaml` is the complete reference
example; `rubrics/_template.yaml` shows every field. The catalogue in §3 is the intended content
for all ten — implement the YAML from it, one metric at a time (`/add-check`).

## 1. Rubric YAML schema

```yaml
metric: security                 # slug; must equal the file name stem
title: Security
version: 1.0.0                   # semver, see §6
weight: 1.5                      # weight of this metric in the overall score
description: >                   # one paragraph; shown in the report
  ...
applies_when: {}                 # metric-level applicability; {} = always (see §2)
scope_globs: ["**/*"]            # files whose hashes key the metric result cache (PR mode)

checks:
  - id: SEC-01                   # <PREFIX>-<NN>, stable forever
    title: No secrets committed to the repository
    type: deterministic          # deterministic | llm
    severity: critical           # critical | high | medium | low | info
    weight: 5                    # optional; default from severity (§4)
    applies_when: {}             # optional check-level applicability
    rationale: >                 # why it matters, 1–2 sentences (report)
      ...
    remediation: >               # how to fix, 1–2 sentences (report)
      ...

    # deterministic only
    rule: secrets.none_found     # registered rule name (§5)
    params: {}                   # validated by the rule's params model

    # llm only
    guidance: |                  # explicit pass / partial / fail criteria + what counts as evidence
      ...
    fact_kinds: [route]          # facts given to the evaluator as context
    evidence_policy: positive_required   # or absence_allowed
    absence_probes:              # required when absence_allowed or na_allowed; patterns whose
                                 # presence would contradict an evidence-less fail/partial/NA claim
      - {kind: path_glob, pattern: ".github/dependabot.yml"}
      - {kind: regex, pattern: "(?i)traceparent|correlation[_-]?id", path_glob: "**/*.{py,ts,js,cs,java}"}
      - {kind: symbol, pattern: "*Middleware"}
    na_allowed: false            # may the model answer not_applicable? (default false → coerced to unknown);
                                 # an evidence-less NA is verified only by absence_probes finding nothing
    self_consistency: 2          # runs per check; default 2 for critical, 1 otherwise

    retired: false               # retired checks stay in the file, are skipped, never renumbered
```

`applies_when` grammar: `{all: [flag, ...], any: [flag, ...], none: [flag, ...]}`; each key
optional; all present clauses must hold. Flags come from `RepoProfile.flags`.

Globs everywhere (rule params, `absence_probes`, `scope_globs`) use `wcmatch.glob` semantics with
`GLOBSTAR | BRACE | DOTGLOB`, matched against repo-relative POSIX paths. Regexes use the `regex`
module with a per-file timeout (SECURITY.md §4).

## 2. Profile flags

| Flag | Detected when |
|---|---|
| `has_http_api` | route facts exist, or an HTTP framework is imported (fastapi, flask, django, express, nestjs, aspnetcore, spring-web, gin, ...) |
| `has_database` | ORM/driver imports or connection-string config (sqlalchemy, django.db, prisma, typeorm, EF Core, JDBC, psycopg, pymongo, ...) |
| `has_message_consumer` | consumer libraries (celery, kafka, pika/rabbit, azure-servicebus, sqs) |
| `has_outbound_http` | requests, httpx, aiohttp, axios, fetch, HttpClient, ... |
| `has_dockerfile` | any `Dockerfile` / `*.Dockerfile` / `Containerfile` |
| `has_compose` | `docker-compose*.yml` / `compose*.yml` |
| `has_k8s` | YAML with `apiVersion` + `kind` of Deployment/StatefulSet/..., or Helm charts |
| `has_iac` | Terraform, Bicep, ARM, CloudFormation, Pulumi files |
| `has_ci` | any CI config (GitHub Actions, GitLab CI, Azure Pipelines, Jenkinsfile, CircleCI) |
| `has_deploy_step` | a CI step classified `deploy` |
| `has_tests` | test files detected |
| `lang_python`, `lang_js_ts`, `lang_dotnet`, `lang_java`, `lang_go` | language with ≥ 10% of LOC |

## 3. Check catalogue

Type: **D** = deterministic, **L** = LLM. Severity: C/H/M/L.

### structure — Code structure (weight 1.0)
| ID | Check | Type | Sev | Rule / notes |
|---|---|---|---|---|
| STR-01 | Clear module/layer boundaries (e.g. API vs domain vs data access) | L | M | `fact_kinds: [import_edge, route]` |
| STR-02 | No oversized source files (>1000 LOC, non-generated) | D | M | `size.max_file_loc` |
| STR-03 | Function complexity under control (CCN>15 ratio) | D | M | `complexity.ccn_ratio` |
| STR-04 | No circular imports between internal modules | D | M | `imports.no_cycles`; NA outside Python/JS/TS |
| STR-05 | Configuration separated from code (env/config files, no magic constants for endpoints/credentials) | L | M | |
| STR-06 | Dependency manifest with a lockfile | D | L | `deps.lockfile_present` |

### auth — Authentication & authorization (weight 1.5, `applies_when: {any: [has_http_api]}`)
| ID | Check | Type | Sev | Rule / notes |
|---|---|---|---|---|
| AUTH-01 | Authentication enforced on API entrypoints (middleware, dependency, decorator, filter) | L | C | `fact_kinds: [route]`; self_consistency 2 |
| AUTH-02 | Unauthenticated routes are explicit and intentional (allowlist, health, login) | L | H | |
| AUTH-03 | Authorization (roles/scopes/ownership) protects privileged operations | L | H | |
| AUTH-04 | Tokens/sessions validated with vetted libraries (signature, expiry, audience/issuer) | L | H | `na_allowed: true` |
| AUTH-05 | Stored passwords hashed with a slow KDF (bcrypt, argon2, scrypt, PBKDF2) | L | H | `na_allowed: true` (no local passwords) |
| AUTH-06 | Session cookies set HttpOnly, Secure, SameSite | L | M | `na_allowed: true` |

### security — Security (weight 1.5) — see `rubrics/security.yaml`
| ID | Check | Type | Sev | Rule / notes |
|---|---|---|---|---|
| SEC-01 | No secrets committed | D | C | `secrets.none_found` |
| SEC-02 | No dependencies with known critical/high vulnerabilities | D | C | `vulns.max_severity` |
| SEC-03 | No high-severity static-analysis findings | D | H | `sast.max_severity` |
| SEC-04 | Untrusted input validated at boundaries | L | H | `applies_when: {any: [has_http_api, has_message_consumer]}`; `fact_kinds: [route, sast_finding]` |
| SEC-05 | Database queries parameterized (no string-built SQL) | L | C | `applies_when: {any: [has_database]}` |
| SEC-06 | CORS and security headers safely configured | L | M | `applies_when: {any: [has_http_api]}`; `absence_allowed`; `na_allowed: true` |
| SEC-07 | Automated dependency updates configured | D | L | `files.any_exists` |

### data — Data management (weight 1.0, `applies_when: {any: [has_database]}`)
| ID | Check | Type | Sev | Rule / notes |
|---|---|---|---|---|
| DATA-01 | Schema changes managed by migrations | D | H | `data.migrations_present` |
| DATA-02 | Connection settings come from config/secret store, not code | L | H | |
| DATA-03 | Data access isolated in a dedicated layer (repositories/DAO/ORM models) | L | M | |
| DATA-04 | Multi-step writes use transactions | L | M | `na_allowed: true` |
| DATA-05 | Sensitive fields protected (hashing/encryption/masking) | L | L | `na_allowed: true` |

### logging — Logging & observability (weight 1.0)
| ID | Check | Type | Sev | Rule / notes |
|---|---|---|---|---|
| LOG-01 | A logging framework is used | D | M | `imports.any_of` |
| LOG-02 | No print/console output in production code | D | L | `ast.debug_output_ratio` |
| LOG-03 | Request correlation / trace context propagated | L | M | `absence_allowed` |
| LOG-04 | Sensitive data not logged (tokens, passwords, PII) | L | H | `fact_kinds: [log_call, sast_finding]` |
| LOG-05 | Telemetry SDK or health endpoint present | D | M | `observability.present` |

### testing — Testing (weight 1.0)
| ID | Check | Type | Sev | Rule / notes |
|---|---|---|---|---|
| TEST-01 | Test suite present | D | H | `tests.present` |
| TEST-02 | Test-to-source LOC ratio | D | M | `tests.ratio` |
| TEST-03 | Tests executed in CI | D | H | `ci.runs_tests`; `applies_when: {any: [has_ci]}` (missing CI is penalized by CI-01) |
| TEST-04 | Coverage measured | D | L | `tests.coverage_configured` |
| TEST-05 | Tests contain meaningful assertions (sampled) | L | M | `fact_kinds: [test_file]`; `applies_when: {any: [has_tests]}` (no tests is penalized by TEST-01); evaluator samples ≤ 5 test files |

### cicd — CI/CD (weight 1.0)
| ID | Check | Type | Sev | Rule / notes |
|---|---|---|---|---|
| CI-01 | CI pipeline exists | D | H | `ci.present` |
| CI-02 | Pipeline runs build, lint and tests | D | M | `ci.stages`; `applies_when: {any: [has_ci]}` |
| CI-03 | Workflows free of actionlint errors | D | L | `ci.actionlint_clean`; `applies_when: {any: [has_ci]}`; NA for non-GitHub CI |
| CI-04 | Least-privilege token permissions | D | M | `ci.permissions_restricted`; `applies_when: {any: [has_ci]}`; NA for non-GitHub CI |
| CI-05 | Third-party actions pinned to a commit SHA | D | M | `ci.actions_pinned`; `applies_when: {any: [has_ci]}`; NA for non-GitHub CI |
| CI-06 | Deployments gated (environments, approvals, protected branches) | L | M | `applies_when: {any: [has_deploy_step]}` |
| CI-07 | Cloud auth via OIDC or secret store, no plaintext credentials in workflows | L | H | `applies_when: {any: [has_deploy_step]}` |

### container — Containers & deployment (weight 0.75, `applies_when: {any: [has_dockerfile, has_k8s, has_compose, has_iac]}`)
| ID | Check | Type | Sev | Rule / notes |
|---|---|---|---|---|
| CTR-01 | Container runs as non-root | D | H | `docker.non_root`; `applies_when: {any: [has_dockerfile]}` |
| CTR-02 | Base images pinned (no `latest`, tag or digest) | D | M | `docker.pinned_base`; `applies_when: {any: [has_dockerfile]}` |
| CTR-03 | Multi-stage build | D | L | `docker.multistage`; `applies_when: {any: [has_dockerfile]}` |
| CTR-04 | `.dockerignore` present | D | L | `files.any_exists`; `applies_when: {any: [has_dockerfile]}` |
| CTR-05 | Health check / probes defined | D | M | `docker.healthcheck` (Dockerfile, compose, or k8s probes); `applies_when: {any: [has_dockerfile, has_k8s, has_compose]}` |
| CTR-06 | IaC/manifests free of high-severity misconfigurations | D | H | `iac.max_severity`; `applies_when: {any: [has_iac, has_k8s]}` |
| CTR-07 | Dockerfile free of hadolint errors | D | L | `docker.hadolint_errors`; `applies_when: {any: [has_dockerfile]}` |

### performance — Performance & resilience (weight 0.75)
| ID | Check | Type | Sev | Rule / notes |
|---|---|---|---|---|
| PERF-01 | Non-blocking IO in request paths (no sync IO in async handlers) | L | M | `applies_when: {any: [has_http_api]}` |
| PERF-02 | No N+1 query patterns | L | M | `applies_when: {any: [has_database]}` |
| PERF-03 | Caching strategy for expensive reads | L | L | `absence_allowed`; `na_allowed: true` |
| PERF-04 | List endpoints paginated | L | M | `applies_when: {any: [has_http_api]}` |
| PERF-05 | Outbound calls have timeouts and retries | L | H | `applies_when: {any: [has_outbound_http]}`; `fact_kinds: [sast_finding]` |
| PERF-06 | Resource limits configured for deployments | D | L | `deploy.resource_limits`; `applies_when: {any: [has_k8s, has_compose]}` |

### documentation — Documentation (weight 0.75)
| ID | Check | Type | Sev | Rule / notes |
|---|---|---|---|---|
| DOC-01 | README covers purpose, setup and how to run | L | M | `absence_allowed` with probe `README*` |
| DOC-02 | API documented (OpenAPI/Swagger, generated or static) | D | M | `docs.api_spec`; `applies_when: {any: [has_http_api]}` |
| DOC-03 | Architecture docs or ADRs | D | L | `docs.architecture` |
| DOC-04 | Docstring/comment coverage of public functions | D | L | `docs.docstring_ratio` |
| DOC-05 | Contributing guide or changelog | D | L | `files.any_exists` |
| DOC-06 | Commands in README refer to things that exist (scripts, make targets, files) | L | L | |

## 4. Scoring (implemented in `src/archlens/score/scorer.py`)

Default check weights by severity: critical 5, high 3, medium 2, low 1, info 0.
Verdict values: pass 1.0, partial 0.5, fail 0.0.

All arithmetic uses `Decimal` (weights and values converted from their YAML/str form), and
findings are sorted by `check_id` before summing, so results are bit-for-bit reproducible.

For each metric (skip if the metric's own `applies_when` fails → `status="not_applicable"`):
1. **Applicable checks** A = non-retired checks whose `applies_when` holds, minus checks whose
   final result is a *verified* `not_applicable` (deterministic NA counts as verified; an LLM NA
   that is not verified becomes `unknown` and stays in A).
   If `Σ_{A} w = 0` (A empty or only `info` checks) → `status="not_applicable"`.
2. **Scored checks** S ⊆ A = checks with a `Finding` where `scored == True`
   (verified and verdict ∈ {pass, partial, fail}). With self-consistency, one finding per check.
3. `coverage = Σ_{S} w / Σ_{A} w`; `raw = 10 · Σ_{S} w·v / Σ_{S} w` (only when `Σ_{S} w > 0`).
4. **Critical findings override the coverage gate**: if S contains a `fail` on a critical check →
   `status="scored"`, `score = min(raw, 4.0)`; else if S contains a `partial` on a critical check →
   `status="scored"`, `score = min(raw, 6.0)`. Record those IDs in `capped_by`.
5. Otherwise, if `coverage < 0.6` → `status="insufficient_evidence"`, `score=None`;
   else `status="scored"`, `score=raw`.
6. Round with `quantize(Decimal("0.1"), ROUND_HALF_UP)`; store as float only at serialization.

Overall: weighted mean of `scored` metrics using metric `weight`, rounded the same way; `None` if
fewer than 5 metrics are `scored`. Grade bands (report only): A ≥ 8.5, B ≥ 7.0, C ≥ 5.5,
D ≥ 4.0, E < 4.0.

Details fixed by the implementation (`score/scorer.py`):
- `capped_by` lists the critical fails when the 4.0 cap applies, else the critical partials when
  the 6.0 cap applies; it is empty whenever no score is produced (e.g. every scored check has
  weight 0, so `raw` is undefined → `insufficient_evidence`).
- `counts` holds each applicable check's effective verdict: a scored finding's verdict, a verified
  `not_applicable` as such, everything else (missing, unknown, unverified, rejected, disputed) as
  `unknown`; all five keys are always present and sum to the applicable checks.
- The overall mean uses the rounded metric scores; metrics are reported sorted by name.
- Two findings for one check are a contract violation (`ValueError`), not a choice to make.

`not_applicable` from an LLM is accepted only when the check has `na_allowed: true`; otherwise it
is coerced to `unknown` with reason `"na_not_allowed"`. Deterministic rules may return
`not_applicable` freely (e.g. no Dockerfile).

Determinism guard: property tests (hypothesis) assert the scorer is invariant to finding order and
that equal inputs give equal outputs bit-for-bit.

## 5. Deterministic rule registry

Rules live in `src/archlens/rubric/rules/<family>.py`, registered with `@rule("<family>.<name>")`
and a Pydantic `Params` model. Signature: `(ctx: RuleContext, params: P) -> RuleOutcome`
(both defined in DATA_MODEL §5; `ctx` gives facts, profile, the full file list and a jailed
`read_text`). Pure: no IO except `ctx.read_text`.

Missing-data semantics, shared by all rules:
- the tool a rule depends on has `status="skipped"` (no input files) → `not_applicable`;
- `status="error"`/`"timeout"` → `unknown` with that reason — never `pass` on missing data;
  a tool with no run record at all (e.g. scanners disabled) → `unknown` (`tool_not_run`);
- the rule's subject is absent (e.g. no workflows for `ci.*`, no Dockerfile for `docker.*`) →
  `not_applicable` (applicability is also expressed in the YAML; the rule is a second guard).

Default params live in each rule's `Params` model; YAML may override them.

| Rule | Params (defaults) | Verdict logic |
|---|---|---|
| `secrets.none_found` | `exclude_globs: ["**/test*/**", "**/*example*"]` | fail if any `secret` fact outside excludes; else pass with `ScanEvidence(result_count=0)` |
| `vulns.max_severity` | `fail_at: high`, `partial_at: medium` | worst `vuln_dependency` severity |
| `sast.max_severity` | `fail_at: high`, `partial_at: medium`, `exclude_rule_globs: []` | worst `sast_finding` severity |
| `files.any_exists` | `globs: [...]` (required) | pass if any `ctx.files` path matches, else fail with `ScanEvidence` of the globs |
| `size.max_file_loc` | `threshold: 1000`, `partial_max_offenders: 2` | over `file_metrics` with `is_generated=False`: 0 offenders pass, ≤ N partial, else fail |
| `complexity.ccn_ratio` | `ccn: 15`, `pass_below: 0.03`, `partial_below: 0.08` | share of functions over CCN |
| `imports.no_cycles` | `scope: internal` | cycles in internal `import_edge` graph (Tarjan SCC) |
| `deps.lockfile_present` | – | every `manifest` has `has_lockfile` |
| `data.migrations_present` | `globs` (default below) | pass if any `ctx.files` path matches |
| `imports.any_of` | `modules` (default below) | pass if any `import_edge.to_module` matches (prefix match) |
| `ast.debug_output_ratio` | `pass_below: 0.05`, `partial_below: 0.2` | non-test `print_call` / (`print_call` + `log_call`); NA if both are 0 |
| `observability.present` | `modules` (default below), `route_patterns: ["/health*", "/ready*", "/live*", "/metrics"]` | import match or `route` path match |
| `tests.present` | `min_test_files: 1` | |
| `tests.ratio` | `pass_at: 0.3`, `partial_at: 0.1` | test LOC / non-test source LOC from `file_metrics` |
| `tests.coverage_configured` | – | coverage config file (`.coveragerc`, `.nycrc*`, `codecov.yml`, `[tool.coverage]`/`--cov` in pyproject/setup.cfg/tox.ini/pytest.ini via `read_text`, `jest`/`vitest` config or `package.json` with coverage, `coverlet` refs, `jacoco`/`kover`), or coverage flags/upload actions in a `ci_step`; nothing found and CI unreadable → unknown |
| `ci.present` | – | any `ci_workflow` fact |
| `ci.stages` | `required: [build, lint, test]` | all → pass, ≥ 1 → partial, 0 → fail; a step counts for its `run_kind` and every other kind its command matches |
| `ci.runs_tests` | – | a `ci_step` with `run_kind=test` (or whose command matches the test pattern, e.g. `pytest && docker push`) |
| `ci.actionlint_clean` | `max_errors: 0` | |
| `ci.permissions_restricted` | – | top-level or every-job `permissions` set and not `write-all`; a job-level `write-all` always fails |
| `ci.actions_pinned` | `allow_owners: [actions, github]` | third-party `uses:` pinned to 40-hex SHA |
| `docker.non_root` | – | final stage has `USER` that is not root/0 |
| `docker.pinned_base` | – | no base image without tag or with `latest` |
| `docker.multistage` | – | > 1 stage |
| `docker.healthcheck` | – | Dockerfile HEALTHCHECK, or `deploy_config` with `has_healthcheck`/`has_liveness`/`has_readiness` |
| `docker.hadolint_errors` | `max_errors: 0` | `hadolint_finding` with severity high |
| `iac.max_severity` | `fail_at: high`, `unknown_severity_fail_count: 5` | fail if any finding ≥ `fail_at`, or ≥ N findings with `severity=None` (checkov OSS often omits severity); partial if any finding; pass if none |
| `deploy.resource_limits` | – | every `deploy_config` has `has_limits`; some → partial |
| `docs.api_spec` | – | OpenAPI/Swagger file in `ctx.files`, or framework auto-docs detected (FastAPI, Swashbuckle, springdoc) |
| `docs.architecture` | `globs: ["docs/**/adr*/**", "docs/**/architecture*", "ARCHITECTURE*", "docs/**/decisions/**"]` | |
| `docs.docstring_ratio` | `pass_at: 0.6`, `partial_at: 0.3` | Σ `documented_functions` / Σ `public_functions` over non-test `file_metrics` |

Default lists (in the rules' `Params` models):

- `imports.any_of` for LOG-01: Python `logging, structlog, loguru`; JS/TS `winston, pino, bunyan,
  log4js`; .NET `Microsoft.Extensions.Logging, Serilog, NLog`; Java `org.slf4j, org.apache.logging.log4j,
  java.util.logging`; Go `log/slog, go.uber.org/zap, github.com/rs/zerolog, github.com/sirupsen/logrus`.
- `observability.present` modules: `opentelemetry, azure.monitor, applicationinsights,
  prometheus_client, @opentelemetry, prom-client, applicationinsights (npm), OpenTelemetry,
  Microsoft.ApplicationInsights, io.micrometer, io.prometheus, go.opentelemetry.io`.
- `data.migrations_present` globs: `**/alembic/versions/**`, `**/migrations/*.py`,
  `**/migrations/*.sql`, `**/db/migrate/**`, `**/db/migration/**`, `**/flyway/**`,
  `**/liquibase/**`, `**/Migrations/*.cs`, `**/prisma/migrations/**`.

## 6. Versioning

Metric `version` is semver: guidance wording only → patch; weight/severity/params/applies_when →
minor; add/remove/retire a check → major. `ConfigFingerprint.rubric_versions` records the version
used for every metric in every report, and the metric result cache key includes it.
