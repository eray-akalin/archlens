"""`findings.sarif`: one result per verified fail/partial finding with a code location."""

import json
from typing import Any

from archlens.models import Rubric
from archlens.report import render_sarif
from tests.unit.report.helpers import RUBRICS, SHA, code, finding, sample_report, spec


def sarif_of(report_rubrics: dict[str, Rubric] = RUBRICS, **kwargs: Any) -> dict[str, Any]:
    return json.loads(render_sarif(sample_report(**kwargs), report_rubrics))


def test_only_verified_issues_with_code_become_results() -> None:
    log = sarif_of()
    assert log["version"] == "2.1.0" and log["$schema"].endswith("sarif-2.1.0.json")
    run = log["runs"][0]
    # SEC-05: verified fail with code. Not: SEC-07 (no code location), SEC-01 / TEST-01 (pass),
    # SEC-06 (rejected), TEST-05 (unverified)
    [result] = run["results"]
    assert result["ruleId"] == "SEC-05" and result["level"] == "error"
    assert result["message"]["text"].startswith("fail: Builds SQL from input")
    location = result["locations"][0]["physicalLocation"]
    assert location["artifactLocation"] == {"uri": "app/api/users.py", "uriBaseId": "%SRCROOT%"}
    assert location["region"] == {"startLine": 34, "endLine": 34}
    assert len(result["partialFingerprints"]["archlens/v1"]) == 32
    [rule] = run["tool"]["driver"]["rules"]
    assert rule["id"] == "SEC-05" and rule["name"] == "Database queries are parameterized"
    assert rule["properties"] == {"tags": ["architecture", "security"], "severity": "critical"}
    assert run["versionControlProvenance"] == [
        {"repositoryUri": "https://github.com/example/tiny-service", "revisionId": SHA}
    ]


def test_levels_follow_severity_one_step_lower_for_partial() -> None:
    rubrics = {"security": RUBRICS["security"].model_copy(update={"checks": [
        spec("SEC-05", "q", "critical"), spec("SEC-06", "h", "medium"),
        spec("SEC-08", "x", "high"), spec("SEC-09", "y", "low"),
    ]})}  # fmt: skip
    found = [
        finding("security", "SEC-05", "partial", evidence=[code("a.py", 1, "x")]),
        finding("security", "SEC-06", "fail", evidence=[code("b.py", 2, "y")]),
        finding("security", "SEC-08", "partial", evidence=[code("c.py", 3, "z")]),
        finding("security", "SEC-09", "fail", evidence=[code("d.py", 4, "w")]),
    ]
    levels = {r["ruleId"]: r["level"] for r in sarif_of(rubrics, found=found, rubrics=rubrics)
              ["runs"][0]["results"]}  # fmt: skip
    assert levels == {"SEC-05": "warning", "SEC-06": "warning", "SEC-08": "warning",
                      "SEC-09": "note"}  # fmt: skip


def test_extra_locations_and_unknown_checks() -> None:
    found = [finding("security", "SEC-05", "fail",
                     evidence=[code("a.py", 1, "x"), code("b.py", 5, "y\nz")])]  # fmt: skip
    run = json.loads(render_sarif(sample_report(found=found), {}))["runs"][0]
    [result] = run["results"]
    assert result["level"] == "warning"  # no rubric → medium
    assert result["relatedLocations"][0]["physicalLocation"]["region"] == {
        "startLine": 5, "endLine": 6
    }  # fmt: skip
    assert run["tool"]["driver"]["rules"] == [{"id": "SEC-05"}]


def test_fingerprint_is_stable_across_runs_and_lines() -> None:
    def fingerprint(start: int, commit: str) -> str:
        found = [finding("security", "SEC-05", "fail", evidence=[code("a.py", start, "x = 1")])]
        log = json.loads(render_sarif(sample_report(found=found, commit_sha=commit), RUBRICS))
        return log["runs"][0]["results"][0]["partialFingerprints"]["archlens/v1"]

    assert fingerprint(3, SHA) == fingerprint(9, "f" * 40)  # moved lines, new commit: same issue
