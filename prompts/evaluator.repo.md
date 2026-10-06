---
id: evaluator.repo
version: 1.0.0
role: evaluator
---
## Repository profile (computed by ArchLens from the repository)
{{ profile }}

## Facts for these checks ({{ shown }} of {{ total }} of kinds {{ kinds | join(", ") or "none" }}; `get_facts` returns more)
{{ facts }}

Evaluation run {{ attempt + 1 }}. Investigate with the tools, then answer with exactly one result
for each of: {{ check_ids | join(", ") }}.
