# Milestones

Work top to bottom. A task is done when every acceptance criterion (AC) is met **and** ruff,
pyright and pytest are green. Tick the box and add a line to the milestone's **Log**
(`YYYY-MM-DD — what was done; ambiguities resolved; spend if any`). Use `/next-milestone`.

**Credit expiry:** `____-__-__` (fill in from the portal — AZURE.md §0)
**Spend so far:** `$__` (update weekly from Cost Management)

Planned spend: development calls ~$10 · first real run ~$0.50 · first eval ~$4 · ablation ~$15 ·
APIM Developer ~2 weeks ~$24 · Redis (optional) ~$12 · buffer ~$15.

Tasks marked 💰 spend money or touch Azure: stop after the plan and ask before executing.

---

## M0 — Scaffold, contracts, storage (days 1–2)

- [x] **M0.1 Repo scaffold.** `pyproject.toml` (uv, Python 3.12), `src/archlens/`, ruff + pyright
  (strict on `src/`) + pytest config with markers `unit, integration, scanners, live` (default
  excludes `scanners`, `live`), `tests/conftest.py` live guard (`ARCHLENS_LIVE_TESTS=1` from
  `os.environ` only), `.gitignore` (incl. `.env`, `.archlens/`, `runs/`), Typer app with stub
  commands `assess, facts, eval, schema, prompts, serve, worker`.
  AC: `uv run archlens --help` lists the commands; `uv run pytest -m live` skips everything without
  the env var; all checks green.
- [ ] **M0.2 Contracts.** All models from DATA_MODEL.md in `src/archlens/models/`,
  `SCHEMA_VERSION`, `archlens schema export` → `schemas/*.json`, drift test.
  AC: round-trip (model → JSON → model) tests for every top-level model; drift test fails when a
  model changes without re-export.
- [ ] **M0.3 Settings and config.** `Settings` (env prefix `ARCHLENS_`), loaders for
  `config/models.yaml`, `config/pricing.yaml`, `config/tools.yaml`; `archlens.errors`.
  AC: invalid/missing config gives a typed error naming the field.
- [ ] **M0.4 CI.** `.github/workflows/ci.yml` (pinned SHAs, `permissions: contents: read`),
  `.github/dependabot.yml` (github-actions, pip, docker).
  AC: workflow green on GitHub.
- [ ] **M0.5 💰 Budget and model access.** Record credit expiry; deploy `infra/budget.bicep`
  ($100, alerts at $60/$85) **first**; run the quota-tier check; write and deploy `infra/ai.bicep`
  (ask first); put endpoint/key in `.env`.
  AC: budget visible in Cost Management; deployments for evaluator, verifier and embed roles exist;
  quota tier recorded in the log.
- [ ] **M0.6 Storage (local).** `storage/base.py` Protocols (artifact store, run-state store,
  cache store, checkpoint store) + `storage/local.py` (filesystem + SQLite under
  `ARCHLENS_DATA_DIR`) + a reusable contract test suite.
  AC: contract suite green against the local backend; the suite is parameterized so the Azure
  backend can reuse it in M4.2.

**Log**

- 2026-10-06 — M0.1: uv/hatchling project, ruff + pyright (strict on `src/`) + pytest config,
  Typer stubs (unimplemented commands exit 1 naming their milestone), live guard tested via
  `pytester` (incl. `.env` not enabling live). Resolved: current ruff formats Python blocks inside
  Markdown → `*.md` excluded so doc samples keep their alignment; unmarked tests get `unit`
  automatically. Spend: $0.

---

## M1 — Ingest, facts, profile, index (week 1)

- [ ] **M1.1 Fixture repo.** `tests/fixtures/repos/tiny_service/`: small FastAPI + SQLAlchemy app
  with Dockerfile, GitHub workflow and tests, plus `DEFECTS.md` listing each planted defect and the
  check it should trigger (aim for ≥ 1 per metric). Secrets are **not** committed: a pytest fixture
  copies the repo to a temp dir and injects a generated secret (keeps this repo clean for
  gitleaks/push protection).
  AC: `DEFECTS.md` maps every defect to a check ID and file/line.
- [ ] **M1.2 Ingest.** Hardened clone (SECURITY.md §3, incl. requested-SHA checkout), local-path
  snapshots, filters, `IngestLimits`, `RepoSnapshot`.
  AC: tests for each limit (total/file count fail; oversized file listed but not read),
  binary/vendored/generated detection, symlink listing, and that the git command lines contain every
  hardening flag; a requested SHA ends up as `commit_sha`.
- [ ] **M1.3 Jail and redaction.** `resolve_in_snapshot`, `redact`.
  AC: tests for every case in SECURITY.md §4 and §5.
- [ ] **M1.4 Scanner adapters.** gitleaks, osv-scanner, semgrep, hadolint, checkov, actionlint,
  lizard; severity mapping (DATA_MODEL §4); `config/tools.yaml` with pinned versions;
  `scripts/install_tools.sh` for local dev.
  AC: parser + severity-mapping test per tool from stored JSON fixtures; `pytest -m scanners` on
  tiny_service finds the planted defects; no input files → `status="skipped"`; a missing/failing
  binary → `status="error"`, not a crash.
- [ ] **M1.5 Extractors.** imports + import graph, manifests/dependencies, CI workflows/steps
  (with `run_kind` classification and redacted `run`), Dockerfile, `deploy_config` (compose, k8s),
  routes (FastAPI, Flask, Django, Express, ASP.NET minimal APIs/controllers, Spring), tests,
  `file_metrics` (incl. public/documented function counts), log/print calls, doc files.
  AC: per-extractor tests; `archlens facts tests/fixtures/repos/tiny_service` writes `facts.jsonl`
  containing every fact kind DEFECTS.md relies on.
- [ ] **M1.6 Profile.** Detectors and all flags in RUBRICS.md §2, computed from snapshot + facts.
  AC: table-driven detector tests; tiny_service profile matches a checked-in expected profile.
- [ ] **M1.7 Index.** Chunker, FTS5, sqlite-vec, RRF search, embedding cache via the M0.6 cache
  store (embedding function injected; deterministic fake embeddings until M2.1).
  AC: known queries on tiny_service return the expected chunk in top 3; re-indexing unchanged files
  makes zero embedding calls.

**Log**

---

## M2 — Evaluate → verify → score → report, three metrics (weeks 1–2)

- [ ] **M2.1 LLM client.** `LLMClient`, `FakeLLM`, cassettes, retries, rate limiter, exact cache
  (boundary-normalized key), cost accounting, budget guard (LLM.md §1–2, §8–10).
  AC: unit tests for each component, incl. a cache hit across two runs with different boundaries;
  one `live` smoke test (structured output from the evaluator deployment) recorded as a cassette and
  replayed offline.
- [ ] **M2.2 Rubrics.** Loader + schema validation + rule registry + `RuleContext`;
  `security.yaml` (exists), `testing.yaml`, `cicd.yaml`; all deterministic rules these need.
  AC: loader rejects unknown keys/rules and missing absence probes (for `absence_allowed` and
  `na_allowed`); every rule has pass/fail/NA/unknown-on-error tests.
- [ ] **M2.3 Repo tools.** `list_dir`, `read_file`, `search_code`, `find_symbol`, `get_facts`;
  seen-lines ledger keyed by `session_id`; `untrusted.wrap`.
  AC: limit tests; ledger records exactly the lines shown (incl. prompt facts); boundary token
  can't be smuggled.
- [ ] **M2.4 Evaluator.** Prompts with front matter + lock test; session loop with tools and
  structured output; post-processing rules; self-consistency.
  AC: FakeLLM-scripted session test with tool calls; one test per post-processing rule (LLM.md §4),
  incl. evidence-less `pass` → `unknown`.
- [ ] **M2.5 Verifier.** Mechanical, entailment (batched), absence replay, skeptic; budget handling.
  AC: one test per row of the ARCHITECTURE §2.5 table; invented/unseen lines and hash mismatches
  rejected; absence probe hit rejects with evidence; skeptic refutation → `disputed`;
  `BudgetExceeded` → remaining findings `unverified`.
- [ ] **M2.6 Scorer.** RUBRICS.md §4 exactly.
  AC: tests for NA handling, zero applicable weight, coverage gate, critical override of the gate,
  caps, overall with < 5 metrics, rounding; hypothesis tests for order invariance and determinism.
- [ ] **M2.7 Report.** `AssessmentReport` builder, `report.md`, basic `report.html`, synthesizer
  with guards (LLM.md §7).
  AC: Markdown/HTML snapshot tests; synth guard tests (unknown finding IDs, unlisted numbers).
- [ ] **M2.8 Orchestrator + CLI.** Pipeline, checkpoints (M0.6 store), `--resume`, `archlens assess`.
  AC: offline e2e on tiny_service (cassettes) produces a valid `assessment.json` detecting the
  planted defects for security, testing and cicd; killing the run mid-evaluate and resuming skips
  completed stages and metrics.
- [ ] **M2.9 💰 First real run.** One public repo (user picks), all three metrics.
  AC: cost ≤ $0.50 and time ≤ 10 min recorded in the log; findings manually skimmed; top issues
  noted as follow-ups.

**Log**

---

## M3 — All metrics and the eval harness (week 2)

- [ ] **M3.1 Remaining rubrics.** structure, auth, data, logging, container, performance,
  documentation (catalogue in RUBRICS.md §3) and their rules.
  AC: every catalogue check implemented; DEFECTS.md fully covered by the offline e2e test.
- [ ] **M3.2 Eval set.** Pin `primary` (EVALUATION.md §1) to the current default-branch SHA; propose
  2–3 `cross` candidates (.NET or Node/TS; owner, license, LOC, Dockerfile/Actions/HTTP API present)
  → user picks → `eval/repos.yaml`; `archlens eval fetch`.
  AC: both repos cloned into the cache with ingest limits respected; `archlens facts` runs cleanly
  on both; `primary` profile has `has_http_api`, `has_database`, `has_dockerfile`, `has_ci` true.
- [ ] **M3.3 Eval runner and metrics.** `archlens.eval.runner` (variants file + constraint
  validation, suites, `--dry-run` cost projection, budget cap, results layout) and
  `archlens.eval.metrics` (EVALUATION.md §4).
  AC: constraint validation rejects each rule violation in EVALUATION.md §2.1; metric computations
  unit-tested on hand-built reports; `--dry-run` works with zero mutations.
- [ ] **M3.4 Mutations and injection variants.** Framework, the 13 mutations, the 4 injection
  mutations, patches for `primary`, and `eval/variants.yaml` with the default grouping.
  AC: each mutation tested (precondition, apply, reported locations); applying V1–V4, VI1, VI2 to
  `primary` and VX1 to `cross` succeeds; `eval --dry-run` lists every variant with its mutations.
- [ ] **M3.5 Labels and audit.** `eval label-sheet`, `eval audit`; the user labels ~15 checks on
  `primary`.
  AC: sheets generated for `primary`; parser reads filled sheets back; `eval/labels/primary.yaml`
  exists.
- [ ] **M3.6 💰 First eval (`full` suite).** ~11 runs, ~$4.
  AC: `summary.json` produced; top 3 failure modes and planned fixes written in the log.

**Log**

---

## M4 — Azure (week 3; must start with ≥ 10 days of credit left)

- [ ] **M4.1 Container image.** Multi-stage Dockerfile, pinned base digest, scanners at pinned
  versions with checksums, non-root, healthcheck; GHCR push in `deploy.yml`.
  AC: image passes ArchLens's own container checks (CTR-01…07).
- [ ] **M4.2 Storage (Azure).** Blob, Cosmos, Queue implementations of the M0.6 Protocols.
  AC: the M0.6 contract suite passes against Azure (marked `live`).
- [ ] **M4.3 API and worker.** FastAPI routes (ARCHITECTURE §6, without `/ask`), API-key auth, URL
  policy, quotas; `archlens worker --once`.
  AC: URL policy tests (SECURITY.md §7); API tests with the local storage backend; artifacts
  endpoint returns 404 for artifacts a run didn't produce.
- [ ] **M4.4 💰 Infra.** `azure.yaml`, `infra/main.bicep` + modules; `azd up` (ask).
  AC: POST a repo URL → job runs → report in Blob → GET returns it; App Insights shows one trace
  per run with stage spans and token metrics.
- [ ] **M4.5 💰 APIM (Consumption).** Import the Azure OpenAI API, managed-identity backend auth,
  `base.xml` policies; app switched to the APIM URL.
  AC: LLM traffic visible in APIM logs; rate-limit policy verified.
- [ ] **M4.6 Observability.** KQL queries in `infra/kql/` (cost per run, tokens per metric,
  verdict mix, verifier outcomes).
  AC: queries return data for the M4.4 run.
- [ ] **M4.7 Web UI.** Static Web App: submit URL, poll status, render the HTML report.
  AC: end-to-end from the browser.

**Log**

---

## M5 — Ablation, PR mode, gateway extras (weeks 3–4)

- [ ] **M5.1 💰 Ablation.** `baseline`, `facts`, `verifier`, `full` per EVALUATION.md §6
  (~41 runs, ~$15).
  AC: `table.md` per config; README Results table filled with the scope sentence from
  EVALUATION.md; injection criteria pass on `full`.
- [ ] **M5.2 PR mode + SARIF.** `findings.sarif`, metric result cache, `archlens assess --base
  <report>` diff, reusable workflow that comments the score delta and uploads SARIF.
  AC: on a test PR, only metrics with changed inputs are re-evaluated; SARIF shows in code scanning.
- [ ] **M5.3 💰 APIM Developer.** Switch `apimSku`, add `developer-extras.xml`
  (`llm-token-limit`, `llm-emit-token-metric`).
  AC: gateway token metrics visible in App Insights; token limit returns 429 when exceeded.
- [ ] **M5.4 💰 (optional) Report Q&A + semantic cache.** `POST /assessments/{id}/ask` route,
  `synth.qa.md`, `enableRedis=true`, `qa-semantic-cache.xml`.
  AC: answers cite finding IDs; a second similar question is served from cache (APIM trace).
- [ ] **M5.5 MCP server.** stdio server exposing `assess_repo`, `get_report`, `search_findings`.
  AC: usable from Claude Code as a local MCP server.

**Log**

---

## M6 — Wrap-up (week 4)

- [ ] **M6.1 HTML report polish** (scores, findings with code snippets, unverified section).
- [ ] **M6.2 README final** — results table, architecture diagram, cost per run, how to reproduce.
- [ ] **M6.3 Demo video** — submit URL → report → PR comment → App Insights trace.
- [ ] **M6.4 💰 Teardown** per AZURE.md §6; confirm $0 run-rate.

**Log**
