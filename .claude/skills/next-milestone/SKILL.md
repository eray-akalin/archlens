---
name: next-milestone
description: Pick up the next unchecked task in docs/MILESTONES.md, implement it against its acceptance criteria, verify, and record progress.
disable-model-invocation: true
argument-hint: "[optional task id, e.g. M1.3]"
---

# Next milestone task

1. Open `docs/MILESTONES.md`. If `$ARGUMENTS` names a task id, take that task; otherwise take the
   first unchecked `- [ ]` task whose milestone prerequisites are all checked.
2. State the task id, its acceptance criteria, and which docs it depends on. Read those docs
   (only the sections that matter) before writing code.
3. Make a short plan (files to create/change, tests to write). If the task touches money
   (real LLM calls in bulk, Azure resources), stop after the plan and ask for confirmation.
4. Implement. Write tests first for deterministic logic (rules, scoring, parsers, verifier steps).
5. Run, and fix until all are green:
   - `uv run ruff check .`
   - `uv run ruff format --check .`
   - `uv run pyright`
   - `uv run pytest`
6. Check every acceptance criterion explicitly and say how each one is met (command + result).
   If one cannot be met, leave the box unchecked and explain why.
7. In `docs/MILESTONES.md`: tick the box and add one line to that milestone's **Log** with the date,
   what was done, and any ambiguity you resolved and how.
8. If a contract changed, confirm `docs/DATA_MODEL.md` and `schemas/` were updated in the same change.
9. Commit with a conventional-commit message. Do not push.
