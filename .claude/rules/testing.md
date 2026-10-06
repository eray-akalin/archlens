---
paths:
  - "tests/**"
  - "eval/**"
  - "src/archlens/eval/**"
---

# Tests and evaluation

- Markers: `unit` (default), `integration` (offline, multiple stages), `scanners` (needs binaries),
  `live` (real LLM, costs money). Default `pytest` run excludes `scanners` and `live`.
- `tests/conftest.py` skips every `live` test unless the process environment has
  `ARCHLENS_LIVE_TESTS=1` (read from `os.environ` only — never from `.env` or Settings), so
  `-m live` alone can't spend money. Run live tests only when asked:
  `ARCHLENS_LIVE_TESTS=1 uv run pytest -m live`.
- LLM calls in tests use `FakeLLM` (scripted responses) or cassettes in `tests/cassettes/`
  replayed by `ARCHLENS_LLM_RECORD_MODE=replay`. A missing cassette is a test failure, not a
  silent network call.
- `tests/fixtures/repos/tiny_service/DEFECTS.md` lists every planted defect and the check it
  should trigger. Keep it in sync when the fixture changes.
- Eval code lives in `src/archlens/eval/`; `eval/` holds only data (repos, variants, configs,
  patches, labels, results). Eval never runs in CI except `--dry-run` and the mutation unit tests.
  Real eval runs need explicit user confirmation after the cost projection.
- Eval results are append-only under `eval/results/<run_id>/`; never edit a past result.
