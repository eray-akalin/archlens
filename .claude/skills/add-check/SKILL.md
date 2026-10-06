---
name: add-check
description: Add or implement a rubric check end to end — YAML entry, deterministic rule or LLM guidance, absence probes, tests, fixture defect, and catalogue update.
argument-hint: "[check id, e.g. CTR-01]"
---

# Add or implement a rubric check

Target check: `$ARGUMENTS`. Its intended meaning is in the catalogue table in `docs/RUBRICS.md`.

1. Read the "Rubric YAML schema" and "Deterministic rule registry" sections of `docs/RUBRICS.md`
   and the metric's YAML in `rubrics/`.
2. Write or update the check entry in `rubrics/<metric>.yaml`. Bump the metric `version`
   according to the semver rule in `docs/RUBRICS.md`.
3. If `type: deterministic`:
   - Implement the rule in `src/archlens/rubric/rules/<family>.py` with `@rule("<family>.<name>")`.
   - Pure function `(ctx: RuleContext, params) -> RuleOutcome`; evidence comes from the facts it
     used, or a `ScanEvidence` record when the verdict rests on "scanner found nothing".
   - Follow the missing-data semantics in `docs/RUBRICS.md` §5 (skipped → NA, error → unknown).
   - Unit tests covering pass / fail / not_applicable / unknown-on-tool-error (and partial if defined).
   - If the rule needs a fact kind that doesn't exist yet, add the extractor/adapter first,
     with its own parser test.
4. If `type: llm`:
   - Write `guidance` with explicit pass / partial / fail criteria and what counts as evidence.
   - List the `fact_kinds` the evaluator should receive as context.
   - If `evidence_policy: absence_allowed` or `na_allowed: true`, add `absence_probes` precise
     enough to have no hits on a repo that genuinely lacks the thing, and at least one hit on a
     repo that has it.
5. If the check is meant to fire on `tests/fixtures/repos/tiny_service/`, plant the defect there and
   add it to that fixture's `DEFECTS.md`.
6. Update the catalogue row in `docs/RUBRICS.md`.
7. Run ruff, pyright, pytest; all green before finishing.
