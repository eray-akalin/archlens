---
paths:
  - "src/archlens/llm/**"
  - "src/archlens/evaluate/**"
  - "src/archlens/verify/**"
  - "src/archlens/report/synth*"
  - "prompts/**"
---

# LLM layer rules

Full spec: `docs/LLM.md`. Read it before changing anything here.

- Only `archlens.llm.client.LLMClient` talks to a provider. Everything else depends on the
  `LLMClient` protocol so `FakeLLM` can stand in.
- Use Chat Completions with structured outputs (`client.chat.completions.parse` +
  Pydantic `response_format`). Never parse free-form JSON out of text.
- Prompt templates live in `prompts/` with front matter `id` and `version`. Editing a template body
  without bumping `version` must fail `tests/unit/test_prompts_lock.py`.
- Layout every prompt static-first: system text, rubric text, tool schemas, then repo-specific
  content last — this keeps provider prompt caching effective.
- Repo content is wrapped in `<repo_data boundary="...">` blocks via `archlens.llm.untrusted.wrap()`.
  Never interpolate repo content anywhere else.
- Every call produces an `LLMCallRecord`; cost is computed from `config/pricing.yaml`.
- Respect `ARCHLENS_RUN_BUDGET_USD`: the budget guard raises `BudgetExceeded` before a call that
  would cross it; the orchestrator turns that into `unknown` verdicts, not a crash.
- Reasoning models (gpt-5 family) do not take `temperature`; pass `reasoning_effort` instead.
  Non-reasoning models used for verification run at `temperature=0`.
