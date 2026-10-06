# ArchLens

**Evidence-backed architecture assessment for any Git repository.**

ArchLens evaluates a codebase against ten enterprise architecture standards — code structure,
authentication, security, data, logging, testing, CI/CD, containers, performance and
documentation — and produces a report in which every finding points to real lines of code that a
verifier has checked. Scores are computed deterministically from verified findings, so the same
commit, rubric and model configuration always yield the same score.

> Status: under construction. Build plan in [`docs/MILESTONES.md`](docs/MILESTONES.md).

## How it works

```
repo ─► ingest ─► facts ─► profile ─► index ─► evaluate (10 metrics, parallel)
                                                  │
                         report ◄─ score ◄─ verify ◄┘
```

1. **Facts first.** Static scanners (gitleaks, osv-scanner, semgrep, hadolint, checkov, actionlint,
   lizard) and tree-sitter extractors turn the repo into structured facts, each with file/line
   evidence. Roughly half of the rubric checks are answered from facts alone, with no LLM call.
2. **LLM evaluators judge, they don't search blindly.** One tool-calling evaluator per metric gets
   the relevant facts plus read-only repository tools and returns structured verdicts with
   citations.
3. **Verification.** Every citation is checked mechanically (file exists at that commit, lines were
   actually read, snippet hash matches), semantically (does the snippet support the claim?), and
   — for absence claims like "no tests" — by replaying a canonical search. Critical failures get a
   skeptic pass that tries to refute them.
4. **Deterministic scoring.** Weighted check results → metric scores → overall score. Unverified
   findings are shown but never scored.

## Results

Ablation on one Python/FastAPI service with planted defects, plus a cross-stack smoke test
(scope and method in [`docs/EVALUATION.md`](docs/EVALUATION.md)):

| Config | Located recall | Labeled precision | Verifier precision (audit) | Score std (3 runs) | Cost / run |
|---|---|---|---|---|---|
| A — hackathon baseline | – | – | – | – | – |
| B — + fact layer | – | – | – | – | – |
| C — + verifier 2.0 | – | – | – | – | – |
| D — full system | – | – | – | – | – |

*To be filled after milestone M5.*

## Quickstart (target interface)

```bash
uv sync
cp .env.example .env            # set ARCHLENS_LLM_BASE_URL / ARCHLENS_LLM_API_KEY
uv run archlens facts ./some-repo                     # no LLM, free
uv run archlens assess https://github.com/org/repo    # full assessment
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) · [Data model](docs/DATA_MODEL.md) · [Rubrics](docs/RUBRICS.md)
- [LLM layer](docs/LLM.md) · [Security](docs/SECURITY.md) · [Evaluation](docs/EVALUATION.md)
- [Azure deployment](docs/AZURE.md) · [Decisions](docs/DECISIONS.md) · [Milestones](docs/MILESTONES.md)
