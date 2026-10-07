# Milestones

Work top to bottom. A task is done when every acceptance criterion (AC) is met **and** ruff,
pyright and pytest are green. Tick the box and add a line to the milestone's **Log**
(`YYYY-MM-DD — what was done; ambiguities resolved; spend if any`). Use `/next-milestone`.

**Credit expiry:** `2027-09-19` (Education hub, checked 2026-10-06: $99 of $100 available)
**Spend so far:** `$0.0004` (LLM calls recorded by ArchLens; update weekly from Cost Management)

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
- [x] **M0.2 Contracts.** All models from DATA_MODEL.md in `src/archlens/models/`,
  `SCHEMA_VERSION`, `archlens schema export` → `schemas/*.json`, drift test.
  AC: round-trip (model → JSON → model) tests for every top-level model; drift test fails when a
  model changes without re-export.
- [x] **M0.3 Settings and config.** `Settings` (env prefix `ARCHLENS_`), loaders for
  `config/models.yaml`, `config/pricing.yaml`, `config/tools.yaml`; `archlens.errors`.
  AC: invalid/missing config gives a typed error naming the field.
- [x] **M0.4 CI.** `.github/workflows/ci.yml` (pinned SHAs, `permissions: contents: read`),
  `.github/dependabot.yml` (github-actions, pip, docker).
  AC: workflow green on GitHub.
- [x] **M0.5 💰 Budget and model access.** Record credit expiry; deploy `infra/budget.bicep`
  ($100, alerts at $60/$85) **first**; run the quota-tier check; write and deploy `infra/ai.bicep`
  (ask first); put endpoint/key in `.env`.
  AC: budget visible in Cost Management; deployments for evaluator, verifier and embed roles exist;
  quota tier recorded in the log.
- [x] **M0.6 Storage (local).** `storage/base.py` Protocols (artifact store, run-state store,
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
- 2026-10-06 — M0.5 (in progress, nothing deployed): subscription `AzureForStudents_2018-01-01`,
  spending limit On; quota tier **Tier 1**. GlobalStandard quota in swedencentral and westeurope:
  gpt-5-mini 1000, gpt-4.1-mini 5000 (quota name `gpt4.1-mini`), text-embedding-3-small 1000
  (K TPM). gpt-4.1-mini is `Legacy`, retires 2027-04-14 (after the project window). Wrote
  `infra/budget.bicep` (timeGrain `Annually` so the one-off credit accumulates instead of
  resetting monthly) and `infra/ai.bicep` (region swedencentral; versions/capacity live in the
  Bicep param since `config/models.yaml` has no such fields); budget what-if: 1 Create, no errors.
- 2026-10-06 — M0.2: all DATA_MODEL contracts in `src/archlens/models/`, 13 top-level models
  exported to `schemas/` with `x-schema-version`; round-trip test per model (a sample is required
  for every registered model) and a drift test (verified by adding a field: test fails naming
  `assessment_report.json`). Resolved: timestamps are `AwareDatetime` (no naive times); LLM-facing
  models carry no length/count constraints (caps applied in post-processing, M2.4) and are tested
  for strict-mode shape; `CheckSpec` already enforces type-specific fields and the absence-probe
  requirement (registry checks stay in M2.2); `RuleContext` deferred to `rubric/context.py` (M2.2)
  since it needs the jail and redaction. Spend: $0.
- 2026-10-06 — M0.5 done: credit expires 2027-09-19. Budget `archlens-credit` deployed and read
  back ($100, Annually from 2026-10-01, alerts 60/85, to the Azure account email). swedencentral
  was blocked by policy `sys.regionrestriction` → account `archlens-ai-wcsizhzzhxrks` in
  **polandcentral** (AZURE.md §1 updated; Static Web Apps unavailable in every allowed region —
  M4.7 risk). Deployments gpt-5-mini (200), gpt-4.1-mini (100), text-embedding-3-small (100)
  Succeeded. Local auth is **Entra** (user's choice): role *Cognitive Services OpenAI User* via
  `developerPrincipalId`, verified with a token-free `GET /openai/v1/models`; `.env` holds only
  base URL + `ARCHLENS_LLM_AUTH=entra` (no key). Spend: $0.
- 2026-10-06 — Per user direction ("don't make rules too strict; give models room"): removed the
  `CheckSpec` rejection of fields belonging to the other check type (my addition, not in
  RUBRICS.md); only documented requirements remain.
- 2026-10-06 — M0.3: `Settings` (all `.env.example` vars; secrets as `SecretStr`; comma lists;
  empty = unset; `APPLICATIONINSIGHTS_CONNECTION_STRING` unprefixed), `load_config` for the three
  YAML files with `ARCHLENS_MODEL_<ROLE>` overrides, `archlens.errors`. `ConfigError.field` names
  the env var or YAML path. Resolved: config files reject unknown keys (catches typos; these are
  our files, not model output); added `ARCHLENS_CONFIG_DIR` (default `config`); a deployment
  without a price is a ConfigError because the budget guard would otherwise under-count. Spend: $0.
- 2026-10-06 — M0.4: public repo https://github.com/eray-akalin/archlens; `ci.yml` (read-only
  token, checkout v7.0.1 and setup-uv v10.2.0 pinned to SHAs, `uv sync --locked`, ruff, pyright,
  pytest) — first run green (37523670755). Dependabot for github-actions, uv (native ecosystem
  rather than pip) and docker (no Dockerfile until M4.1). Pre-publish scan of all commits: no
  subscription/tenant/object IDs, keys or `.env`. Spend: $0.
- 2026-10-06 — M0.6: async `ArtifactStore`, `RunStateStore`, `CacheStore`, `CheckpointStore`
  protocols + local backend (atomic file writes, SQLite cache with TTL checked against an
  injectable clock so Cosmos can match it). Contract suite parameterized via `BACKENDS` in
  `tests/unit/storage/conftest.py`. Resolved: identifiers are validated (artifact names come from
  API URLs — path-traversal boundary); per-metric checkpoints use `evaluate/<metric>` keys;
  `open_storage(azure)` is a ConfigError until M4.2. Spend: $0.

---

## M1 — Ingest, facts, profile, index (week 1)

- [x] **M1.1 Fixture repo.** `tests/fixtures/repos/tiny_service/`: small FastAPI + SQLAlchemy app
  with Dockerfile, GitHub workflow and tests, plus `DEFECTS.md` listing each planted defect and the
  check it should trigger (aim for ≥ 1 per metric). Secrets are **not** committed: a pytest fixture
  copies the repo to a temp dir and injects a generated secret (keeps this repo clean for
  gitleaks/push protection).
  AC: `DEFECTS.md` maps every defect to a check ID and file/line.
- [x] **M1.2 Ingest.** Hardened clone (SECURITY.md §3, incl. requested-SHA checkout), local-path
  snapshots, filters, `IngestLimits`, `RepoSnapshot`.
  AC: tests for each limit (total/file count fail; oversized file listed but not read),
  binary/vendored/generated detection, symlink listing, and that the git command lines contain every
  hardening flag; a requested SHA ends up as `commit_sha`.
- [x] **M1.3 Jail and redaction.** `resolve_in_snapshot`, `redact`.
  AC: tests for every case in SECURITY.md §4 and §5.
- [x] **M1.4 Scanner adapters.** gitleaks, osv-scanner, semgrep, hadolint, checkov, actionlint,
  lizard; severity mapping (DATA_MODEL §4); `config/tools.yaml` with pinned versions;
  `scripts/install_tools.sh` for local dev.
  AC: parser + severity-mapping test per tool from stored JSON fixtures; `pytest -m scanners` on
  tiny_service finds the planted defects; no input files → `status="skipped"`; a missing/failing
  binary → `status="error"`, not a crash.
- [x] **M1.5 Extractors.** imports + import graph, manifests/dependencies, CI workflows/steps
  (with `run_kind` classification and redacted `run`), Dockerfile, `deploy_config` (compose, k8s),
  routes (FastAPI, Flask, Django, Express, ASP.NET minimal APIs/controllers, Spring), tests,
  `file_metrics` (incl. public/documented function counts), log/print calls, doc files.
  AC: per-extractor tests; `archlens facts tests/fixtures/repos/tiny_service` writes `facts.jsonl`
  containing every fact kind DEFECTS.md relies on.
- [x] **M1.6 Profile.** Detectors and all flags in RUBRICS.md §2, computed from snapshot + facts.
  AC: table-driven detector tests; tiny_service profile matches a checked-in expected profile.
- [x] **M1.7 Index.** Chunker, FTS5, sqlite-vec, RRF search, embedding cache via the M0.6 cache
  store (embedding function injected; deterministic fake embeddings until M2.1).
  AC: known queries on tiny_service return the expected chunk in top 3; re-indexing unchanged files
  makes zero embedding calls.

**Log**

- 2026-10-06 — M1.1: `tiny_service` (FastAPI + SQLAlchemy, Dockerfile, compose, workflow,
  tests) with 42 defects covering all 10 metrics plus 5 controls; `DEFECTS.md` is checked row by
  row by `tests/unit/test_tiny_service_fixture.py` (marker at file:line, check IDs exist in the
  catalogue). Resolved: the `tiny_service` fixture copies the repo **without** `DEFECTS.md` (the
  answer key must never reach an evaluator) and injects both the secret (D01) and the vulnerable
  `PyYAML==5.3` pin (D02), so this public repo never triggers push protection or Dependabot alerts;
  defects that are an absence use location `absent`; D04 (SEC-03 via semgrep) to be confirmed in
  M1.4. Fixture excluded from ruff (with `force-exclude`), pyright and pytest collection.
  Incident: the Claude Code format hook ran `uv run` from inside the fixture, which created a
  `.venv` and `uv.lock` there (deps installed from PyPI, no fixture code run); removed, hook now
  pinned to `--project $CLAUDE_PROJECT_DIR --no-sync --force-exclude`, and a test fails if a
  lockfile or venv appears in the fixture. Scanners installed via Homebrew: gitleaks 8.30.1,
  osv-scanner 2.6.0, semgrep 1.179.0, hadolint 2.15.1, checkov 3.3.20, actionlint 1.7.12. Spend: $0.
- 2026-10-06 — M1.2: `ingest/{clone,snapshot,filters,limits}.py`. Clone follows SECURITY.md §3
  plus: `--` before the URL, ref validation (no option-like refs), env built from scratch
  (`GIT_CONFIG_NOSYSTEM`, `GIT_CONFIG_GLOBAL=/dev/null`, only PATH inherited), `core.fsmonitor=false`,
  protocol restrictions on fetch too, credentials in URLs rejected without echoing them. Every git
  call is recorded and checked in a test against a local `file://` origin (the `protocol` parameter
  exists only for that). Resolved: **schema 1.1.0** — `FileEntry.symlink_target` and `too_large`
  (+ `readable` property) since SECURITY.md requires recording link targets; local paths get a
  content-derived 40-hex `commit_sha` and git is never run in them (a repo's `.git/config` can make
  git execute programs); lockfiles count as `is_generated`; short SHAs are not supported as refs.
  Spend: $0.
- 2026-10-06 — M1.3: `tools/paths.resolve_in_snapshot` (component-wise containment, so
  `<root>-evil` is outside; NUL, absolute, UNC and drive paths rejected; symlink chains followed
  then checked; missing paths allowed) and `security/redact` (`Redactor` with gitleaks spans +
  fallback patterns). Resolved: a redaction keeps the original newline count so line numbers —
  and therefore citations — stay valid; `SecretSpan` columns are 1-based inclusive (gitleaks
  mapping confirmed in M1.4); the generic `password=`/`token=` rule needs ≥ 16 chars and entropy
  ≥ 3.5, so low-entropy demo values like compose's `POSTGRES_PASSWORD: app` stay readable as
  evidence; redaction is idempotent (hypothesis). Spend: $0.
- 2026-10-06 — M1.4: adapters for gitleaks, osv-scanner, semgrep, hadolint, checkov, actionlint
  (subprocess, scratch cwd, env built from scratch — no ARCHLENS_*/cloud vars reach a tool) and
  lizard (API); `facts/scanners/run_scanners` runs gitleaks first so its secret spans redact all
  other evidence; versions pinned in `config/tools.yaml` (all match the installed ones);
  `scripts/install_tools.sh` also pins semgrep `p/default` (1073 rules) as a local file;
  `scripts/capture_scanner_fixtures.py` regenerates `tests/fixtures/scanners/*` from the adapters'
  own commands (redacted, root → `__ROOT__`). Verified by experiment and recorded in docstrings:
  a repo `.semgrepignore` with `*` hides everything from a directory scan (→ explicit file
  targets); gitleaks columns are +1 on lines after the first (corrected); actionlint reports
  cwd-relative paths (→ runs with cwd=<repo>, explicit config). Repo-level suppressions disabled:
  `.gitleaks.toml`/`.gitleaksignore`/`gitleaks:allow`, `osv-scanner.toml`, `nosemgrep`,
  `# hadolint ignore`, actionlint config; not possible: `checkov:skip` (documented). Resolved:
  DEFECTS D04 moved to the SSRF finding (users.py:59, impact HIGH) — the SQL f-string only yields
  impact LOW under the DATA_MODEL mapping; secret facts carry span columns (DATA_MODEL table
  updated); `ARCHLENS_TOOLS_DIR` added. Deviation to review: the opt-in `pytest -m scanners`
  suite needs the OSV API (package names only) — CLAUDE.md rule 7 says tests never touch the
  network; the default suite and CI stay fully offline — **accepted by the user, ADR-014**.
  Spend: $0.
- 2026-10-07 — M1.5: ten extractors (`ast:imports|manifests|ci|docker|deploy|routes|tests|logging|
  metrics`, `fs:docs`), `facts/runner.collect_facts`, and a working `archlens facts <path|url>
  [--no-scanners]` → `<out>/<commit12>/facts.jsonl` + `tool_runs.json`. On tiny_service: 100 facts,
  17 kinds, every kind DEFECTS.md relies on (verified in the scanners suite). Resolved: Python is
  parsed with the stdlib `ast` (exact, no new dependency); JS/TS, C#, Java/Kotlin and Go use line
  patterns for now — upgrade to tree-sitter queries if the cross-stack eval repo (M3.2) needs more
  precision; route mount prefixes (`include_router(prefix=)`, `app.use('/x', r)`) are not applied;
  a fully `==`-pinned requirements file and a pom.xml with explicit versions count as locked;
  `pip install ruff` classifies as `lint` (harmless; documented leniency); attribute additions
  recorded in the DATA_MODEL table; E501 relaxed for tests (one-line input literals). Spend: $0.
- 2026-10-07 — M1.6: `profile/{detectors,__init__}.py` — every RUBRICS §2 flag always present;
  frameworks, package managers, CI systems, test frameworks, entrypoints; `archlens facts` also
  writes `profile.json`. Expected profile checked in at `tests/fixtures/repos/tiny_service.profile.json`
  (outside the fixture so it's never part of the assessed repo). Resolved: has_database means an
  ORM/driver import or dependency (redis-only projects don't count, so DATA-* don't apply to them);
  connection-string config detection is left to the LLM checks; global `fetch(` / `new HttpClient`
  are matched as text since they need no import; lang_java covers Java/Kotlin/Scala; language
  shares use code files only (no YAML/JSON/Markdown, vendored or generated). Spend: $0.
- 2026-10-07 — M1.7: `index/{chunker,embed,store,__init__}.py` — tree-sitter symbol chunks (Python,
  JS/TS/TSX/JSX, Java, C#, Go; big classes split into header + members; > 80 lines and other files
  → 60-line windows, 10 overlap; blank edges trimmed), chunk text redacted before storage and
  embedding, one SQLite file per snapshot (FTS5 porter/unicode61 + sqlite-vec), hybrid search with
  RRF (k=60) and wcmatch path globs; `FakeEmbedder` (hashed bag of words) until M2.1;
  `CachedEmbedder` over the M0.6 cache store. AC: six known queries on tiny_service hit the top 3;
  re-indexing makes 0 provider calls (34/34 cache hits). Resolved: **ADR-015** — per-language
  grammar wheels instead of tree-sitter-language-pack (it downloads grammars at runtime; its
  cache was cleaned and removed) and uv-managed Python (local build lacked loadable SQLite
  extensions); free text is turned into quoted FTS tokens so no FTS syntax reaches MATCH. Spend: $0.

---

## M2 — Evaluate → verify → score → report, three metrics (weeks 1–2)

- [x] **M2.1 LLM client.** `LLMClient`, `FakeLLM`, cassettes, retries, rate limiter, exact cache
  (boundary-normalized key), cost accounting, budget guard (LLM.md §1–2, §8–10).
  AC: unit tests for each component, incl. a cache hit across two runs with different boundaries;
  one `live` smoke test (structured output from the evaluator deployment) recorded as a cassette and
  replayed offline.
- [x] **M2.2 Rubrics.** Loader + schema validation + rule registry + `RuleContext`;
  `security.yaml` (exists), `testing.yaml`, `cicd.yaml`; all deterministic rules these need.
  AC: loader rejects unknown keys/rules and missing absence probes (for `absence_allowed` and
  `na_allowed`); every rule has pass/fail/NA/unknown-on-error tests.
- [x] **M2.3 Repo tools.** `list_dir`, `read_file`, `search_code`, `find_symbol`, `get_facts`;
  seen-lines ledger keyed by `session_id`; `untrusted.wrap`.
  AC: limit tests; ledger records exactly the lines shown (incl. prompt facts); boundary token
  can't be smuggled.
- [x] **M2.4 Evaluator.** Prompts with front matter + lock test; session loop with tools and
  structured output; post-processing rules; self-consistency.
  AC: FakeLLM-scripted session test with tool calls; one test per post-processing rule (LLM.md §4),
  incl. evidence-less `pass` → `unknown`.
- [x] **M2.5 Verifier.** Mechanical, entailment (batched), absence replay, skeptic; budget handling.
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

- 2026-10-07 — M2.1: `llm/{client,types,cache,cassette,cost,budget,ratelimit,untrusted,fake,embedder}.py`.
  `LLMClient.complete` = exact cache (boundary-normalized key) → budget reservation (worst case)
  → semaphore + TPM bucket → provider with retries (429/5xx/timeouts, `retry-after(-ms)`, jittered
  backoff, 5 attempts, one LLMCallRecord each) → schema validation (`InvalidModelOutput` for
  invalid/truncated/refused/empty; never cached when truncated). `OpenAIProvider` (only `openai`
  import) is tested against a mocked HTTP transport: strict `json_schema`, strict tools, no
  temperature for reasoning models, APIM key in its header and never as bearer. AC: cache hit
  across two runs with different boundaries; live smoke on gpt-5-mini (337 in / 160 out, 64
  reasoning, **$0.0004**) recorded as a cassette and replayed offline with a new boundary.
  Resolved: **ADR-016** (chars/3.5 estimates, no tiktoken); openai 3.x uses `httpx2`; Entra uses the
  sync `DefaultAzureCredential` in a thread (the aio one needs aiohttp); the tool-call loop lives
  in M2.4 on top of `complete()` (FakeLLM already scripts tool calls). Spend: $0.0004.
- 2026-10-07 — M2.2: `rubric/{context,registry,loader}.py` + `rubric/rules/{secrets,vulns,sast,files,tests,ci}.py`
  (13 rules), `rubrics/testing.yaml` and `rubrics/cicd.yaml`. `@rule("<family>.<name>")` takes the
  `Params` model from the `params` annotation (`RuleParams`: frozen, unknown keys rejected);
  `run_rule` wraps the outcome into a `CheckResult` and turns an unknown rule, invalid params or a
  crashing rule into `unknown`. Loader rejects invalid YAML, unknown keys, unknown rules, invalid
  params, missing absence probes and metric ≠ file stem (`RubricError`); unknown flags are only
  logged. AC: 163 new tests — pass/fail/NA/unknown (error, timeout, never ran) per rule; offline
  tiny_service run matches DEFECTS.md (D07, D17, D18, D20–D22, C03, C04; scanner checks
  `unknown`), and `-m scanners` matches D01, D02, D04 with CI-03 pass; a test keeps the shipped
  YAML in sync with the §3 catalogue (weight, IDs, type, severity, rule, applies_when).
  Resolved: a tool with no run record → `unknown` (`tool_not_run`); `RuleContext.read_text` reads
  only listed readable files (never symlinks or unlisted paths such as `.git/`) and returns None
  above `max_bytes`; `evidence()` uses the verifier's `SnippetReader` so hashes match; evidence
  capped at 20 items per outcome (claims carry full counts); a CI step counts for every stage its
  command matches (`ruff check . && pytest` lints *and* tests); a job-level `write-all` fails
  CI-04 even under a restrictive top level; TEST-05 gets `applies_when: has_tests` (catalogue
  updated; no tests is penalized by TEST-01); `AppliesWhen.holds(flags)` (unknown flag = False);
  `GLOB_FLAGS` moved to `archlens/globs.py` so rules don't import the index; the DEFECTS.md parser
  moved to `tests/fixture_repos.answer_key()`. tiny_service also gets TEST-02 fail (ratio 0.08),
  not in the answer key. Spend: $0.
- 2026-10-07 — M2.3: `tools/{repo_tools,specs,seen}.py`. `RepoTools` (per run) + `ToolSession`
  (per `session_id`): each tool is a pure function returning the exact line ranges it shows;
  `ToolSession.call` alone records the `SearchRecord`, marks the ledger, redacts and wraps, and
  never raises for bad model input (`error: …` result). `show_facts` renders prompt facts and
  marks their evidence lines before the first turn. Index gains a `symbols` table (every
  definition, nested included) + `index.find_symbols`. AC: limit tests per tool (entries, lines,
  bytes, top_k, symbols, facts); a scripted session parses every numbered line back out of the
  results and requires the ledger to equal it; boundary smuggling via a file is blocked. Fixed
  along the way: `untrusted.wrap` removed the boundary in one pass, so `half + boundary + half`
  rejoined it — now removed until none is left; the redaction fallback `generic-secret` pattern
  was quadratic on long identifier runs (0.7 s per 12 KB line; a 2 MB one-line file would stall
  a run) — now linear (lookbehind + lookahead + possessive), same matches, regression test.
  Resolved: tool args are nullable without non-null defaults (strict schemas keep `default`);
  out-of-range args are clamped, not rejected; regex search runs on redacted text of non-vendored
  files with 100 ms per file and 10 s total; `find_symbol` is case-insensitive over plain or
  qualified names; `fact_path` prefers code evidence (`route.path` is a URL). Deps: `regex`,
  dev `types-regex`. Tests: 648 offline (+47), scanners 3/3. Spend: $0.
- 2026-10-07 — M2.4: `llm/{prompts,session}.py`, `evaluate/{llm_session,consistency,deterministic}.py`,
  `prompts/evaluator.{system,metric,repo}.md` + `prompts.lock`, `archlens prompts lock`.
  `run_tool_session` (generic loop over `complete()`, reused by the skeptic in M2.5): sequential
  tool calls, final no-tools turn at the tool/context budget, refused over-budget calls, one
  retry on invalid output, budget/provider failures → outcome errors (cassette misses propagate).
  `Evaluator.evaluate`: facts ≤ `max_facts` (severity, then path round-robin), static-first
  messages (system → rubric → tools → wrapped profile + facts), facts' evidence lines marked in the
  ledger before turn 1, LLM.md §4 post-processing. AC: FakeLLM-scripted tiny_service session with
  two tool calls; one test per post-processing rule incl. evidence-less `pass` → `unknown`; lock
  test (`tests/unit/test_prompts_lock.py`) + CLI. Resolved: a third prompt `evaluator.repo.md`
  holds the repo-specific part (versioned like the others) and the run number, so consistency
  reruns never share an exact-cache key; the session's `prompt_version` joins the three versions
  with `;`; `LLMClientProtocol` keeps only `complete`/`embed` (LLM.md §1 updated); model `unknown`
  → `reason="model_unknown"`; a failed rerun keeps the first result with low confidence;
  `InvalidModelOutput` now carries the call records so `llm_call_ids` stay complete; claims
  trimmed to 300 chars / 5 citations; `severity_rank` moved to `models.enums`. Deps: `jinja2`.
  Tests: 689 offline (+41). Spend: $0.
- 2026-10-07 — M2.5: `verify/{mechanical,entailment,absence,skeptic,pipeline}.py`,
  `prompts/{verifier.entailment,verifier.findings,skeptic.system,skeptic.finding}.md`.
  `Verifier.verify(results) → findings` routes each result per ARCHITECTURE §2.5, then batches
  entailment per metric (≤ 5), then challenges verified critical `fail`s with the skeptic
  (`run_tool_session`, own session id + ledger; cited snippets shown and marked). AC: one test per
  table row; invented lines, unseen lines, paths outside the snapshot / unlisted / binary /
  symlink, spans ≥ 60 and hash mismatches of carried evidence rejected; absence hit → `rejected`
  with the hit as `CodeEvidence`; skeptic refutation with valid citations → `disputed` (with
  unseen lines → ignored); `BudgetExceeded` → pending and later batches `unverified`
  ("skipped: budget"), no further calls, skeptic skipped, absence replay still runs. Resolved:
  one bad citation fails the whole mechanical step (the evidence is taken whole or not at all);
  an absence probe that can't finish (regex timeout without a hit, bad pattern, no index) →
  `unverified` rather than verified; the claim is wrapped as untrusted data in entailment and
  skeptic prompts; entailment gets one retry on unusable output; `VerifierOptions` for the
  EVALUATION §6 ablations (mechanical only, skeptic off); `RepoTools.listing()` and
  `ToolSession.show_evidence()` added; `tiny_env` fixture moved to `tests/unit/conftest.py`.
  Tests: 727 offline (+38). Spend: $0.

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
