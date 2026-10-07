---
id: skeptic.finding
version: 1.0.0
role: skeptic
---
## Check {{ check.id }}: {{ check.title }} (severity {{ check.severity }})
{{ check.guidance.strip() }}

## The finding to challenge
Verdict: {{ verdict }}
Claim:
{{ claim }}
Cited code:
{{ code }}

Look for code that shows this finding is wrong, then answer.
