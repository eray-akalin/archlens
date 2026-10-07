---
id: verifier.findings
version: 1.0.0
role: verifier
---
{% for finding in findings %}
## Finding {{ finding.ref }}
Check: {{ finding.title }}
Verdict: {{ finding.verdict }}
Claim:
{{ finding.claim }}
Cited code:
{{ finding.code }}
{% endfor %}

Answer with exactly one item for each ref: {{ refs | join(", ") }}.
