---
id: evaluator.metric
version: 1.0.0
role: evaluator
---
# Metric: {{ rubric.title }} (`{{ rubric.metric }}`, rubric {{ rubric.version }})

{{ rubric.description.strip() }}

## Checks
{% for check in checks %}
### {{ check.id }}: {{ check.title }}
- Severity: {{ check.severity }}
- Evidence: pass needs citations;
{%- if check.evidence_policy == "absence_allowed" %} fail/partial may have no citations when what the check looks for is absent (the absence is verified).
{%- else %} fail/partial need citations.
{%- endif %}
- not_applicable:
{%- if check.na_allowed %} allowed when the criteria below say so.
{%- else %} not allowed; answer unknown if the check cannot be judged.
{%- endif %}

{{ check.guidance.strip() }}
{% endfor %}
