---
id: synth.qa_input
version: 1.0.0
role: synth
---
## Findings that did not affect any score ({{ other | length }})
{% for f in other %}
### {{ f.id }} — {{ f.metric }}, {{ f.verdict }}, {{ f.status }}{% if f.title %}: {{ f.title }}{% endif %}
{{ f.claim }}
{% if f.why %}Why not scored: {{ f.why }}{% endif %}
{% endfor %}

## Question
{{ question }}
