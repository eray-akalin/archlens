# Decisions

Short ADRs. Status `accepted` unless noted. To change one, add a new ADR that supersedes it;
don't edit history.

### ADR-001 — Code-only orchestrator
The pipeline is a fixed DAG of async stages in plain Python. LLMs act inside stages (evaluator,
verifier, skeptic, synthesizer) but never choose the next stage, tools outside their session, or
budgets. *Why:* predictable cost and latency, reproducible runs, testable control flow, and the
hackathon design already proved it. Microsoft Agent Framework workflows were considered for
checkpointing; plain checkpoints to storage are enough for an 8-stage DAG and avoid a framework
dependency.

### ADR-002 — Deterministic scoring
Scores are a pure function of verified findings and the rubric (RUBRICS.md §4). *Why:* LLM-produced
numbers drift between runs and are open to manipulation; deterministic scoring makes changes
explainable ("score dropped because SEC-02 became a verified fail").

### ADR-003 — Facts first
Static scanners and AST extraction run before any LLM call; about half the checks are answered
from facts alone, and LLM evaluators receive relevant facts as context. *Why:* cheaper, more
stable, and better evidence than having a model hunt for things a scanner finds exactly. The
ablation (EVALUATION.md §6) measures this claim.

### ADR-004 — OpenAI-compatible client, Chat Completions, Azure v1 endpoint
One `openai` SDK client against a configurable base URL; Chat Completions with structured outputs
and strict function tools. *Why:* the same code works for Azure OpenAI directly, through APIM, or
against another provider if student quota is unavailable. The Responses API can be adopted later
behind the same `LLMClient` protocol.

### ADR-005 — SQLite (FTS5 + sqlite-vec) for the code index
One index file per snapshot, hybrid BM25 + vector search with RRF. *Why:* zero infrastructure cost,
portable between local and cloud runs, fast enough for ≤ 50k LOC. Azure AI Search would add a paid
service without improving the assessment.

### ADR-006 — No semantic cache for evaluator calls
Evaluator/verifier calls use an exact content-hash cache only. The APIM semantic cache (Redis) is
allowed only for report Q&A. *Why:* evaluator prompts are repo-specific; a semantically "similar"
prompt from another repo could return another repo's findings — the worst possible error for an
evidence-based tool. Exact caching gives the real savings (re-runs, PR mode).

### ADR-007 — Container Apps (API + queue-triggered Job) instead of App Service
*Why:* scale-to-zero and a monthly free grant fit a credit-limited subscription; a Job per
assessment isolates untrusted repos per execution and gives natural timeouts.

### ADR-008 — GitHub Container Registry instead of ACR
*Why:* ACR has a fixed monthly cost; the image contains no secrets and can be public.

### ADR-009 — APIM Consumption by default, Developer for the final demo window
*Why:* Consumption costs ~$0 but lacks `llm-token-limit` and `llm-emit-token-metric`; the
Developer tier supports them for ~2 weeks of cost. The SKU is a Bicep parameter; app-side token
metrics exist regardless.

### ADR-010 — Clean-room rebuild
This repository is written from scratch. No code, prompts, configs or assets are copied from the
hackathon team repository; only the publicly describable architecture ideas carry over. *Why:*
the hackathon code is a shared team work product; a solo portfolio project must be unambiguously
the author's own.

### ADR-011 — Python 3.12 + uv
*Why:* best ecosystem for tree-sitter, scanners' JSON tooling, eval/analysis, and LLM SDKs; uv gives
fast, locked, reproducible environments in CI and Docker.

### ADR-012 — Evidence filled by the system, with a seen-lines ledger
Models cite `path` + line range only; the system reads the snippet and hash, and rejects citations
of lines the evaluator never received in a tool result during the session. *Why:* blocks the most
common failure mode of LLM reviewers — plausible but invented line references.

### ADR-013 — Eval on one primary repo plus a cross-stack smoke test
The eval set is `fastapi/full-stack-fastapi-template` (all mutations, injection variants,
stability, ~15 manual labels) plus one repo in another stack (generic mutations only). Several
non-interacting defects share one variant, since every run evaluates all metrics.
*Why:* a 12-repo golden set needed ~100 manual labels and repo-specific patches per repo, and
~$35 of runs. This design keeps per-check ground truth, ablation and stability measurements for
~41 runs (~$15) and a fraction of the labeling work.
*Cost:* generalization evidence is limited to one extra stack, and the README must state the scope.
The harness supports adding repos later without code changes.
