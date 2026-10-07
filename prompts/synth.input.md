---
id: synth.input
version: 1.0.0
role: synth
---
# Assessment of {{ repo }} at commit {{ commit }}
Overall score (scale 0-10): {{ overall }}

## Metrics
{% for m in metrics -%}
- {{ m.metric }}: status {{ m.status }}, score {{ m.score }}, coverage {{ m.coverage }}%,
  checks pass {{ m.counts.pass }}, partial {{ m.counts.partial }}, fail {{ m.counts.fail }},
  not applicable {{ m.counts.not_applicable }}, unknown {{ m.counts.unknown }}
  {%- if m.capped_by %}; capped by critical findings in {{ m.capped_by | join(", ") }}{% endif %}
{% endfor %}
## Verified findings ({{ findings | length }})
{% for f in findings %}
### {{ f.id }} — {{ f.metric }}, {{ f.verdict }}{% if f.title %}: {{ f.title }}{% endif %}
{{ f.claim }}
{{ f.code }}
{% endfor %}
{{ other }} other findings were not verified and are not described.
