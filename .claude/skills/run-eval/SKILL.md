---
name: run-eval
description: Run an evaluation or ablation config against the eval set with a cost projection and explicit confirmation first.
disable-model-invocation: true
argument-hint: "[config name, e.g. full | baseline | facts | verifier]"
---

# Run evaluation

Config: `eval/configs/$ARGUMENTS.yaml`. Spec: `docs/EVALUATION.md`.

1. Run `uv run archlens eval --config eval/configs/$ARGUMENTS.yaml --dry-run` and show:
   number of repo variants, repetitions, projected tokens, projected USD, and the remaining run
   budget from the config.
2. Stop and ask for confirmation. Do not continue without an explicit yes.
3. Run the eval without `--dry-run`. If it stops on `BudgetExceeded`, report how far it got;
   do not raise the budget yourself.
4. When it finishes, read `eval/results/<run_id>/summary.json` and report the metrics defined in
   `docs/EVALUATION.md` §4: detection and located recall, spillover, labeled precision/accuracy
   (if labels exist), mechanical pass rate, score stability, verdict agreement, injection Δ,
   cost and time per run.
5. Compare against the previous result for the same config if one exists, and flag regressions.
6. Do not edit result files. If this run should feed the README table, say so and wait.
