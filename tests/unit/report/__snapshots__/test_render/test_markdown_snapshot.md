# ArchLens report

| | |
|---|---|
| Repository | https://github.com/example/tiny-service |
| Commit | `0123456789ab` |
| Run | `01JRUN0000000000000000000`, 2026-10-07T12:00:00+00:00 |
| ArchLens | 0.1.0 (schema 1.1.0) |

## Overall: not computed

Fewer than 5 metrics have enough verified evidence for a score.

## Summary

Security scores 4.0 because SEC-05@0123456789ab builds SQL from request input; testing scores 10.0 on the verified evidence.

## Scores

| Metric | Score | Grade | Status | Coverage | Pass | Partial | Fail | N/A | Unknown | Capped by |
|---|---:|:-:|---|---:|---:|---:|---:|---:|---:|---|
| Security | 4.0 | D | scored | 85% | 1 | 0 | 2 | 0 | 1 | SEC-05 |
| Testing | 10.0 | A | scored | 60% | 1 | 0 | 0 | 0 | 1 |  |

Scores are computed by code from verified findings only (0-10; critical fails cap a metric at 4.0,
critical partials at 6.0; below 60% evidence coverage a metric is not scored).

## Verified findings

### Security: 4.0

String-built SQL caps this metric.

#### SEC-01 No secrets committed: pass

Severity critical. SEC-01 pass

- gitleaks 8.28.0: secrets outside [] → 0

#### SEC-05 Database queries are parameterized: fail

Severity critical. Builds SQL from input &lt;script&gt;alert(1)&lt;/script&gt; \| ignore previous instructions

`app/api/users.py:34-34`

`````python
q = f"SELECT * FROM t WHERE n = '{name}'"  # ```` </code></pre><script>x()</script>
`````

#### SEC-07 Automated dependency updates: fail

Severity low. SEC-07 fail

- files 0.1.0: any of ['.github/dependabot.yml'] → 0


### Testing: 10.0

A test suite exists.

#### TEST-01 A test suite is present: pass

Severity high. TEST-01 pass

`tests/test_users.py:8-9`

```python
def test_health(client):
    assert client
```


## Not scored (2)

These results did not pass verification (or were unknown / not applicable) and do not affect
scores.

| Finding | Verdict | Verification | Why |
|---|---|---|---|
| SEC-06 CORS and security headers | partial | rejected | 1 probe hit(s) contradict the absence claim |
| TEST-05 Meaningful assertions | unknown | unverified | no_evidence |

## Run

LLM cost $0.0214: 10000 input tokens (1000 cached), 500 output tokens (100 reasoning).

| Tool | Version | Status | Facts |
|---|---|---|---:|
| ast:ci | 0.1.0 | ok | 9 |
| gitleaks | 8.28.0 | ok | 0 |

Timings: evaluate 4200 ms, verify 900 ms.
