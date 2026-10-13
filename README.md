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

Ablation (M5.1, 2026-10-13, 43 assessments + a 2-run VI2 re-measure, $4.87 in LLM calls). **Scope:** results describe one
well-engineered Python/FastAPI service with planted defects, plus a cross-stack smoke test on one
repo in a different language. This is a deliberate trade-off for a solo, credit-limited project
(ADR-013); it shows that detection and verification work, not that they generalize across all
stacks. Method and metrics: [`docs/EVALUATION.md`](docs/EVALUATION.md); raw results:
`eval/results/{baseline,facts,verifier,full}-*/`.

| Config | Located recall (primary · cross) | Labeled precision | Verifier precision (audit) | Score std (base runs) | Injection VI1 · VI2 | Cost / run |
|---|---|---|---|---|---|---|
| A — hackathon baseline | 50% · 83% | 58% | not audited | 0.46 | ✗ · ✗ | $0.058 |
| B — + fact layer, deterministic rules | **94% · 100%** | **100%** | not audited | **0.11** | ✓ · ✓ | **$0.054** |
| C — + verifier 2.0, self-consistency (no facts) | 44% · 50% | 64% | not audited | 0.64 | ✗ · ✗ | $0.184 |
| D — full system | 89% · 100% | **100%** | not audited | 0.32 | ✓ · ✓\* | $0.132 |

What it says, on this set: the fact layer and the deterministic rules carry the result — B lifts
recall from 50% to 94% and precision from 58% to 100% over A, makes runs far more stable, and
passes both injection tests, at A's cost. The model-side machinery alone (C) doesn't replace them
and costs three times as much. D adds no measured gain over B here and more variance.

\* D first missed VI2: self-consistency merged partial / pass / fail on AUTH-01 into `unknown`
(the tie-break had pinpointed the de-authenticated route), and in the cache-off run both evaluator
passes believed a fake "middleware protects this" comment — entailment rejected that pass, so it
didn't score, but the defect went undetected. After the merge fix (an issue majority stands) both
VI2 runs detect SEC-05 and AUTH-01 (`eval/results/full-vi2-*`). The defense against such comments
is probabilistic: of the two cache-off samples, one was fooled.

Caveats: 2–3 base runs per config, one labeled repo; verifier precision needs the manual audit
sheet (not done for the ablation); A's variant runs reused C's cached evaluator answers (identical
prompts), so A and C differ there only after evaluation.

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
