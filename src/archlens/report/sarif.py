"""`findings.sarif` (ARCHITECTURE.md §2.7): SARIF 2.1.0 for code scanning.

One result per verified `fail`/`partial` finding that has a code location (the first cited range;
any others become related locations). Levels follow the check's severity — critical/high `error`,
medium `warning`, low/info `note` — one step lower for `partial`. Every result carries a stable
`archlens/v1` fingerprint (check, path, snippet hash), so code scanning tracks it across commits.
"""

import hashlib
import json
from collections.abc import Mapping
from typing import Literal

from archlens.models import AssessmentReport, CheckSpec, CodeEvidence, Finding, Rubric

SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
INFORMATION_URI = "https://github.com/eray-akalin/archlens"
Level = Literal["error", "warning", "note"]
LEVELS: tuple[Level, ...] = ("error", "warning", "note")
SEVERITY_LEVEL = {"critical": 0, "high": 0, "medium": 1, "low": 2, "info": 2}


def render_sarif(report: AssessmentReport, rubrics: Mapping[str, Rubric]) -> str:
    """The SARIF log for `report` as JSON text. Checks missing from `rubrics` still get a rule
    (id only), so a result never points at an undefined rule."""
    specs = {check.id: check for rubric in rubrics.values() for check in rubric.checks}
    metrics = {check.id: rubric.metric for rubric in rubrics.values() for check in rubric.checks}
    results: list[dict[str, object]] = []
    rule_ids: list[str] = []
    for finding in report.findings:
        result = _result(finding, specs.get(finding.result.check_id))
        if result is None:
            continue
        results.append(result)
        if finding.result.check_id not in rule_ids:
            rule_ids.append(finding.result.check_id)
    run: dict[str, object] = {
        "tool": {
            "driver": {
                "name": "ArchLens",
                "semanticVersion": report.config.archlens_version,
                "informationUri": INFORMATION_URI,
                "rules": [
                    _rule(rule_id, specs.get(rule_id), metrics.get(rule_id))
                    for rule_id in sorted(rule_ids)
                ],
            }
        },
        "automationDetails": {"id": f"archlens/{report.run_id}"},
        "results": results,
    }
    if report.repo_url:
        run["versionControlProvenance"] = [
            {"repositoryUri": report.repo_url, "revisionId": report.commit_sha}
        ]
    log = {"$schema": SCHEMA, "version": "2.1.0", "runs": [run]}
    return json.dumps(log, indent=2, ensure_ascii=False) + "\n"


def _level(spec: CheckSpec | None, verdict: str) -> Level:
    index = SEVERITY_LEVEL[spec.severity] if spec else 1
    return LEVELS[min(index + (verdict == "partial"), len(LEVELS) - 1)]


def _rule(rule_id: str, spec: CheckSpec | None, metric: str | None) -> dict[str, object]:
    if spec is None:
        return {"id": rule_id}
    return {
        "id": rule_id,
        "name": spec.title,
        "shortDescription": {"text": spec.title},
        "fullDescription": {"text": " ".join(spec.rationale.split())},
        "help": {"text": " ".join(spec.remediation.split())},
        "defaultConfiguration": {"level": _level(spec, "fail")},
        "properties": {"tags": ["architecture", metric or "archlens"], "severity": spec.severity},
    }


def _location(evidence: CodeEvidence) -> dict[str, object]:
    return {
        "physicalLocation": {
            "artifactLocation": {"uri": evidence.path, "uriBaseId": "%SRCROOT%"},
            "region": {"startLine": evidence.start_line, "endLine": evidence.end_line},
        }
    }


def _result(finding: Finding, spec: CheckSpec | None) -> dict[str, object] | None:
    result = finding.result
    code = [e for e in result.evidence if isinstance(e, CodeEvidence)]
    if result.verdict not in ("fail", "partial") or not code:
        return None
    first = code[0]
    fingerprint = hashlib.sha256(
        f"{result.check_id}\0{first.path}\0{first.snippet_sha256}".encode()
    ).hexdigest()[:32]
    sarif: dict[str, object] = {
        "ruleId": result.check_id,
        "level": _level(spec, result.verdict),
        "message": {"text": f"{result.verdict}: {' '.join(result.claim.split())}"},
        "locations": [_location(first)],
        "partialFingerprints": {"archlens/v1": fingerprint},
        "properties": {"verdict": result.verdict, "metric": result.metric, "finding": finding.id},
    }
    if len(code) > 1:
        sarif["relatedLocations"] = [
            {"id": i, **_location(e)} for i, e in enumerate(code[1:], start=1)
        ]
    return sarif
