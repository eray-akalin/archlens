# Architecture

## 1. Goal and quality bar

Given a repository at a specific commit, produce an architecture assessment where:

- every finding that affects a score cites code that a verifier has confirmed,
- scores are a deterministic function of verified findings and the rubric,
- the run is reproducible: same commit + rubric versions + prompt versions + models ⇒ same
  findings (modulo LLM nondeterminism, which is measured — see `EVALUATION.md`) and identical
  scoring of those findings,
- a typical repo (≤ 50k LOC) costs ≤ $0.50 and ≤ 10 minutes.

## 2. Pipeline

```
ingest ─► facts ─► profile ─► index ─► evaluate ─► verify ─► score ─► report
                                         (10 metrics in parallel)
```

Each stage is an async function `run(ctx: RunContext, inputs) -> output` whose output is a Pydantic
model from `DATA_MODEL.md`. The orchestrator persists every output as a checkpoint.

| # | Stage | Input | Output | LLM? |
|---|---|---|---|---|
| 0 | `ingest` | URL or local path, `IngestLimits` | `RepoSnapshot` | no |
| 1 | `facts` | snapshot | `FactSet` | no |
| 2 | `profile` | snapshot, facts | `RepoProfile` | no |
| 3 | `index` | snapshot | `CodeIndex` (SQLite file) | embeddings only |
| 4 | `evaluate` | rubrics, facts, profile, index | `list[CheckResult]` per metric | yes |
| 5 | `verify` | check results, snapshot, index | `list[Finding]` | yes (cheap) |
| 6 | `score` | findings, rubrics, profile | `list[MetricScore]`, overall | no |
| 7 | `report` | everything above | `AssessmentReport` + renderings | yes (prose only) |

### 2.0 Ingest
- Remote: hardened shallow clone at the requested ref into an ephemeral dir (`SECURITY.md` §3).
  Local path: copy-on-read view; never mutate the user's directory.
- Walk files, skip binaries / vendored / generated (`node_modules/`, `vendor/`, `dist/`, `*.min.js`,
  lockfiles are kept as manifests but not indexed as code), compute `sha256`, LOC, language.
- Enforce `IngestLimits`: exceeding `max_total_bytes`, `max_files` or the timeout fails ingest with
  a clear error rather than assessing a partial repo silently. Files above `max_file_bytes` are
  listed in the snapshot (with `size`) but never read, scanned or indexed.

### 2.1 Facts (deterministic)
Scanner adapters and AST extractors, each producing `Fact`s with evidence. Which adapters run is
decided from the snapshot's file list alone (e.g. hadolint only if Dockerfiles exist). Adapters run
concurrently in a process pool with per-tool timeouts. A tool with no input files gets a
`ToolRunRecord(status="skipped")`; a failing tool gets `status="error"`. Rules map `skipped` to
`not_applicable` and `error`/`timeout` to `unknown`; the run continues either way.

Adapters (`src/archlens/facts/scanners/`): gitleaks, osv-scanner, semgrep, hadolint, checkov,
actionlint, lizard. Extractors (`src/archlens/facts/extractors/`): imports & import graph,
manifests & dependencies, CI workflows/jobs/steps, Dockerfile instructions, routes, test files,
logging/print calls, docstrings, doc files.

Pin every scanner version in the Dockerfile and in `config/tools.yaml`; record the version in each
`ToolRunRecord`. Confirm CLI flags with `<tool> --help` for the pinned version and record the
exact command in the adapter docstring.

### 2.2 Profile (deterministic)
Runs after facts because several flags are derived from them (routes → `has_http_api`, CI steps →
`has_deploy_step`). Detects languages (by LOC), frameworks (import/manifest signatures), package
managers, CI system, container/IaC presence, test frameworks, DB usage. Emits boolean `flags`
used by rubric `applies_when` (catalogue in `RUBRICS.md` §2).

### 2.3 Index
Symbol-aware chunks (tree-sitter: function/class/method; fallback: 60-line windows with 10-line
overlap). Stored in one SQLite file per snapshot: FTS5 table for BM25, `sqlite-vec` table for
embeddings, chunk table with `path, start_line, end_line, symbol, lang`, and a `symbols` table
with every definition (nested ones included) for `find_symbol` and symbol absence probes. Search = BM25 ∪ vector,
fused with reciprocal rank fusion. Embedding calls go through the LLM client (cost-tracked,
cached by chunk hash).

### 2.4 Evaluate
For each applicable metric (in parallel, bounded by a semaphore):
1. Deterministic checks → run their rule with a `RuleContext` (facts, profile, file list). No LLM.
2. LLM checks → one tool-calling evaluator session per metric covering all of that metric's LLM
   checks. Input: rubric guidance, relevant facts (`fact_kinds`, plus any `injection_attempt`
   facts), profile summary, read-only tools.
   Output: `MetricEvaluationOutput` (structured). Details in `LLM.md` §4.
3. Checks with `self_consistency: 2` (critical LLM checks) are evaluated twice; a disagreement
   gets one tie-break run and the strict majority stands (`confidence` at most `medium`); still no
   majority ⇒ `verdict="unknown"`, `confidence="low"`, `reason="inconsistent"`.

### 2.5 Verify
Applies to LLM-origin results with verdict `pass`, `partial`, `fail` or `not_applicable`
(`unknown` results skip verification and stay unscored). Deterministic results are `verified` by
construction — their evidence comes from facts — and never go through these steps.

Which path a result takes depends on whether it has citations (details in `LLM.md` §6):

| Result | Steps | Outcome |
|---|---|---|
| has citations | 1 mechanical → 2 entailment | both pass ⇒ `verified`; mechanical fails ⇒ `rejected`; entailment `no` ⇒ `rejected`, `insufficient` ⇒ `unverified` |
| no citations, verdict `fail`/`partial`, check `absence_allowed` | 3 absence replay (+ judge) | zero probe hits ⇒ `verified`; hits (attached) go to the absence judge: claim holds ⇒ `verified`, contradicted ⇒ `rejected`, unclear ⇒ `unverified` |
| no citations, verdict `not_applicable`, check `na_allowed` | 3 absence replay (+ judge) | as above |
| no citations, any other case | – | coerced earlier to `unknown` (LLM.md §4) |
| verified `fail` on a `critical` check | 4 skeptic (extra) | refutation with mechanically valid citations ⇒ `disputed` |

1. **Mechanical** — converts each `Citation` into `CodeEvidence`: path resolves inside the
   snapshot, `1 ≤ start ≤ end ≤ file length`, span < 60 lines, every cited line is in the session's
   seen-lines ledger; the snippet is read, redacted and hashed here.
2. **Entailment** — cheap model judges whether the snippets support the claim. Runs on every cited
   LLM result (batched; ~$0.02 per run), so a manipulated `pass` still needs code that an
   independent call accepts.
3. **Absence replay** — run the check's `absence_probes`; hits are shown to the entailment model
   (prompt `verifier.absence`), which decides whether they contradict the claim — a probe match is
   a lead, not proof (a stub README matches `README*` yet confirms "the README is a stub").
   With entailment off ("mechanical only") any hit ⇒ `rejected`.
4. **Skeptic** — separate tool session tries to refute.

If `BudgetExceeded` is raised during verification, results not yet verified become `unverified`
(their last step reads `skipped: budget`) and no further LLM step runs; a skipped skeptic leaves
the finding `verified` with a `VerificationStep(step="skeptic", passed=True,
detail="skipped: budget")`. Absence replay needs no LLM and still runs; its judge is an LLM
step and is skipped like entailment.

### 2.6 Score
Pure function, specified in `RUBRICS.md` §4.

### 2.7 Report
- `assessment.json` — the canonical `AssessmentReport`.
- `report.md`, `report.html` — rendered from Jinja2 templates; all numbers come from the JSON.
  A basic HTML template ships with the first report task (M2.7); M6.1 only polishes it.
- `findings.sarif` — SARIF 2.1.0, one result per verified `fail`/`partial` finding with a code
  location.
- `pr_comment.md` — score delta + new/resolved findings vs. a base report (M5).
- Narrative (executive summary + per-metric paragraph) is written by the synthesizer model from
  verified findings only; it returns `cited_findings` and the renderer rejects references to
  unknown finding IDs.

## 3. Module layout

```
src/archlens/
  cli.py                 Typer app: assess, facts, eval, schema, prompts, serve, worker
  config.py              Settings (pydantic-settings, prefix ARCHLENS_)
  errors.py              Typed exceptions
  globs.py               Path globs (wcmatch GLOBSTAR | BRACE | DOTGLOB), shared by rules and tools
  models/                Pydantic contracts (DATA_MODEL.md) + SCHEMA_VERSION
  ingest/                clone.py, snapshot.py, limits.py, filters.py
  profile/               detectors.py, flags.py
  facts/
    scanners/            gitleaks.py osv.py semgrep.py hadolint.py checkov.py actionlint.py lizard_.py
    extractors/          imports.py manifests.py ci.py docker.py routes.py tests.py logging_.py docs.py
    runner.py            runs adapters concurrently, builds FactSet
  index/                 chunker.py, store.py (FTS5 + sqlite-vec), search.py (RRF)
  tools/                 paths.py (jail), repo_tools.py (LLM-facing tools), specs.py (their
                         argument models), seen.py (seen-lines ledger)
  rubric/                loader.py, registry.py, context.py (RuleContext), rules/<family>.py
                         (the YAML schema models live in models/rubric.py)
  evaluate/              deterministic.py, llm_session.py, consistency.py
  verify/                mechanical.py, entailment.py, absence.py, skeptic.py, pipeline.py
  score/                 scorer.py
  report/                builder.py, synth.py, view.py + render.py (Markdown and HTML),
                         templates/, sarif.py, pr.py
  llm/                   client.py, fake.py, cassette.py, ratelimit.py, cache.py, cost.py,
                         prompts.py, session.py (tool loop), untrusted.py
  security/              redact.py, url_policy.py
  orchestrator/          pipeline.py, checkpoint.py, context.py
  storage/               base.py (Protocols), local.py, azure.py
  api/                   app.py (FastAPI factory, `GET /healthz`), routes.py, auth.py (X-API-Key)
  worker/                main.py (queue consumer for Container Apps Job)
  mcp/                   server.py (M5)
  telemetry/             otel.py, metrics.py
  eval/                  runner.py, metrics.py, sheets.py, mutations/<id>.py (code lives in the package)
prompts/                 *.md Jinja2 templates with front matter; prompts.lock
rubrics/                 <metric>.yaml × 10
config/                  pricing.yaml, tools.yaml, models.yaml
schemas/                 generated JSON Schemas (committed; CI checks drift)
eval/                    data only: repos.yaml, variants.yaml, configs/, patches/, labels/, results/
infra/                   main.bicep, modules/*.bicep, ai.bicep; azure.yaml at repo root
tests/                   unit/, integration/, fixtures/{repos,scanners}/, cassettes/
```

## 4. Orchestration

- Code-only orchestrator (`orchestrator/pipeline.py`) — a fixed DAG of async stage functions.
  The LLM never decides control flow (ADR-001).
- `RunContext` carries `run_id`, settings, storage, LLM client, budget guard, telemetry tracer,
  and the seen-lines ledger.
- Checkpoints: after each stage, `checkpoint.save(run_id, stage, output)`; the evaluate stage
  checkpoints per metric (`evaluate/<metric>`: its results plus its sessions' seen lines, which
  the verifier needs after a resume). LLM call records and stage timings are checkpointed too, so
  a resumed run's cost covers every call. `archlens assess <target> --resume <run_id>` skips
  completed stages/metrics; the repository is re-read (remote: re-cloned at the recorded commit)
  and must match the checkpointed snapshot, otherwise the resume fails. The index file lives in
  `<data_dir>/runs/<run_id>/index.sqlite` so it survives the clone.
- Concurrency: `ARCHLENS_MAX_CONCURRENCY` bounds concurrent LLM sessions; a token-bucket limiter
  keyed on `ARCHLENS_TPM_LIMIT` paces requests; 429s are retried with the server's `retry-after`.
- Failure semantics: a stage-level exception fails the run (with checkpoint kept); a check-level
  problem yields `unknown` for that check only.

## 5. Caching

| Cache | Key | Store | Purpose |
|---|---|---|---|
| LLM exact cache | sha256(deployment, prompt_version, messages with the per-run `repo_data` boundary replaced by a fixed placeholder, tools, response schema, params) | local SQLite / Cosmos | identical re-runs cost nothing |
| Metric result cache | sha256(metric rubric version, prompt version, models, sorted hashes of files matching `scope_globs`, hash of relevant facts) | same | PR mode: re-evaluate only metrics whose inputs changed |
| Embedding cache | sha256(model, chunk text) | same | re-indexing unchanged code is free |
| Provider prompt cache | automatic (≥1024-token identical prefix) | provider | lower input cost; requires static-first prompt layout |

No semantic cache for evaluator calls (ADR-006): a near-match from another repo would return
another repo's findings.

## 6. Run modes

**Local CLI** — everything in-process, storage under `ARCHLENS_DATA_DIR`
(`runs/<run_id>/{checkpoints,artifacts}`, `cache.sqlite`). Only LLM calls leave the machine.

**Cloud** (M4) —
```
client ─► API (Container Apps, scale-to-zero)
            │ POST /assessments → Cosmos job doc + Storage Queue message
            ▼
          Worker (Container Apps Job, queue-triggered) ─► runs pipeline
            │ artifacts → Blob, status/findings → Cosmos
            ▼
          LLM traffic ─► APIM gateway ─► Azure OpenAI (managed identity)
          Telemetry ─► OpenTelemetry ─► Application Insights
          Web UI (Static Web Apps) ─► API
```

API surface (FastAPI):
- `POST /assessments` `{repo_url, ref?, metrics?}` → `202 {run_id}`
- `GET /assessments/{run_id}` → status + stage progress
- `GET /assessments/{run_id}/report` → `AssessmentReport`
- `GET /assessments/{run_id}/artifacts/{name}` → any artifact the run produced (md, html; sarif
  once M5.2 exists); `404` for artifacts not produced
- `POST /assessments/{run_id}/ask` `{question}` → answer grounded in the report (report Q&A, added
  in M5.4; the only place a semantic cache may be used)
- `GET /healthz`

As built (M4.3): `POST /assessments` checks the API key, the URL policy, the ref and the metric
names, then the quotas (per key: `ARCHLENS_MAX_CONCURRENT_RUNS_PER_KEY` runs queued or running,
`ARCHLENS_RUNS_PER_DAY_PER_KEY` in the last 24 h), and writes a `Job`, a `queued` `RunState` and a
queue message. Runs are visible only to the key that created them — another key's run, a
malformed id or an artifact the run didn't produce are all `404`. HTML artifacts are served with
`Content-Security-Policy: default-src 'none'` and `nosniff`. Storage gained two protocols:
`JobStore` (create, get, by_key) and `JobQueue` (send, receive with a visibility timeout,
delete with the receipt — Azure Storage Queue semantics; the local backend is file-per-message
with an flock). `archlens worker [--once]` takes a message, runs the pipeline (resuming when
the state says `running`: an earlier attempt died), deletes the message when the run ends either
way, and marks the run failed after 2 deliveries that never finished.

## 7. Configuration

`archlens.config.Settings` (pydantic-settings, env prefix `ARCHLENS_`). See `.env.example` for the
full list. Non-secret defaults also live in `config/models.yaml` (model routing, reasoning effort,
token caps) and `config/pricing.yaml` (USD per 1M tokens per deployment — user-maintained; verify
against the Azure pricing page before eval runs). `config/tools.yaml` pins scanner versions and
checksums and lists `disabled` tools; `ARCHLENS_SEMGREP_REGISTRY_RULES=false` (the image's default)
disables semgrep for licensing reasons.

## 8. Observability

- One OTel trace per run; spans per stage, per metric session, per LLM call, per tool call.
- Metrics: `archlens.llm.tokens{kind=input|cached|output|reasoning, stage, metric, model}`,
  `archlens.llm.cost_usd{stage, metric}`, `archlens.check.verdicts{metric, verdict}`,
  `archlens.verify.status{step, status}`, `archlens.run.duration_s`.
- Token metrics are emitted by the app (the APIM Consumption gateway can't emit LLM token metrics;
  the app-side version is also more granular).
- Local mode: console exporter off by default; `ARCHLENS_OTEL_ENABLED=true` + connection string
  sends to Application Insights.
