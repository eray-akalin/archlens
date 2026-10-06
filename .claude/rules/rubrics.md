---
paths:
  - "rubrics/**"
  - "src/archlens/rubric/**"
  - "src/archlens/score/**"
---

# Rubric and scoring rules

Full spec: `docs/RUBRICS.md` (schema, check catalogue, rule registry) and `docs/DATA_MODEL.md`.

- One YAML file per metric in `rubrics/`; `rubrics/security.yaml` is the reference example.
- Check IDs are stable (`SEC-01`). Never renumber; retire a check with `retired: true`.
- Any change to a check's meaning, weight, severity or guidance bumps the metric's `version`
  (semver: guidance wording = patch, weights/severity = minor, add/remove check = major).
- `type: deterministic` checks must name a `rule` registered with `@rule("<family>.<name>")` in
  `src/archlens/rubric/rules/`. Unknown rule names fail rubric loading.
- `evidence_policy: absence_allowed` or `na_allowed: true` requires at least one `absence_probes`
  entry; the loader enforces this.
- Rules have the signature `(ctx: RuleContext, params) -> RuleOutcome` and follow the
  missing-data semantics in `docs/RUBRICS.md` §5.
- Scoring lives only in `src/archlens/score/`. Keep it a pure function of
  `(rubrics, findings, profile)`; property tests in `tests/unit/score/` guard determinism.
- Update the catalogue table in `docs/RUBRICS.md` in the same change as the YAML.
