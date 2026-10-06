"""secrets.none_found, vulns.max_severity, sast.max_severity, files.any_exists."""

from pathlib import Path

import pytest
from pydantic import JsonValue, ValidationError

from archlens.models import CodeEvidence, Fact, ScanEvidence, Severity, ToolStatus
from archlens.rubric import get_rule
from tests.unit.rubric.helpers import fact, first_scan, make_ctx, run

# --- secrets.none_found ------------------------------------------------------------------------


def test_secrets_pass_records_the_empty_scan(tmp_path: Path) -> None:
    outcome = run("secrets.none_found", make_ctx(tmp_path))
    assert outcome.verdict == "pass"
    assert outcome.evidence == [
        ScanEvidence(
            tool="gitleaks",
            tool_version="9.9.9",
            query="secrets outside ['**/test*/**', '**/*example*']",
            result_count=0,
        )
    ]


def test_secrets_in_excluded_paths_pass(tmp_path: Path) -> None:
    facts = [
        fact("secret", path="tests/unit/test_auth.py", rule_id="jwt"),
        fact("secret", path=".env.example", rule_id="generic-api-key"),
    ]
    outcome = run("secrets.none_found", make_ctx(tmp_path, facts))
    assert outcome.verdict == "pass"
    assert "2 in excluded paths" in outcome.claim


def test_secret_outside_excludes_fails_with_its_evidence(tmp_path: Path) -> None:
    leaked = fact("secret", path="app/settings.py", rule_id="aws-access-token")
    ignored = fact("secret", path="tests/test_x.py", rule_id="jwt")
    outcome = run("secrets.none_found", make_ctx(tmp_path, [leaked, ignored]))
    assert outcome.verdict == "fail"
    assert outcome.evidence == leaked.evidence
    assert "app/settings.py" in outcome.claim and "aws-access-token" in outcome.claim


def test_secrets_exclude_globs_param(tmp_path: Path) -> None:
    facts = [fact("secret", path="tests/test_x.py", rule_id="jwt")]
    outcome = run("secrets.none_found", make_ctx(tmp_path, facts), exclude_globs=[])
    assert outcome.verdict == "fail"


# --- shared missing-data semantics (RUBRICS §5) ------------------------------------------------

SCANNER_RULES = [
    ("secrets.none_found", "gitleaks"),
    ("vulns.max_severity", "osv-scanner"),
    ("sast.max_severity", "semgrep"),
]


@pytest.mark.parametrize(("name", "tool"), SCANNER_RULES)
def test_skipped_tool_is_not_applicable(name: str, tool: str, tmp_path: Path) -> None:
    outcome = run(name, make_ctx(tmp_path, status={tool: "skipped"}))
    assert (outcome.verdict, outcome.reason) == ("not_applicable", "tool_skipped")


@pytest.mark.parametrize("status", ["error", "timeout"])
@pytest.mark.parametrize(("name", "tool"), SCANNER_RULES)
def test_failed_tool_is_unknown(name: str, tool: str, status: ToolStatus, tmp_path: Path) -> None:
    outcome = run(name, make_ctx(tmp_path, status={tool: status}))
    assert (outcome.verdict, outcome.reason) == ("unknown", f"tool_{status}")
    assert f"{tool} broke" in outcome.claim


@pytest.mark.parametrize(("name", "tool"), SCANNER_RULES)
def test_tool_that_never_ran_is_unknown(name: str, tool: str, tmp_path: Path) -> None:
    outcome = run(name, make_ctx(tmp_path, status={tool: None}))
    assert (outcome.verdict, outcome.reason) == ("unknown", "tool_not_run")


# --- vulns.max_severity ------------------------------------------------------------------------


def vuln(severity: Severity) -> Fact:
    return fact(
        "vuln_dependency",
        severity,
        package="pyyaml",
        version="5.3",
        ids=["GHSA-x"],
        ecosystem="PyPI",
    )


@pytest.mark.parametrize(
    ("severities", "expected"),
    [
        ([], "pass"),
        (["low", "info"], "pass"),
        (["low", "medium"], "partial"),
        (["medium", "high"], "fail"),
        (["critical"], "fail"),
    ],
)
def test_vulns_default_thresholds(
    severities: list[Severity], expected: str, tmp_path: Path
) -> None:
    facts = [vuln(s) for s in severities]
    outcome = run("vulns.max_severity", make_ctx(tmp_path, facts))
    assert outcome.verdict == expected


def test_vulns_fail_cites_the_worst_first(tmp_path: Path) -> None:
    medium, critical = vuln("medium"), vuln("critical")
    medium = medium.model_copy(
        update={"evidence": [ScanEvidence(tool="t", tool_version="1", query="m", result_count=1)]}
    )
    outcome = run("vulns.max_severity", make_ctx(tmp_path, [medium, critical]))
    assert outcome.verdict == "fail"
    assert "worst: critical" in outcome.claim and "pyyaml 5.3 GHSA-x" in outcome.claim
    assert outcome.evidence[0] == critical.evidence[0]


def test_vulns_custom_thresholds(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, [vuln("high")])
    assert run("vulns.max_severity", ctx, fail_at="critical").verdict == "partial"
    assert run("vulns.max_severity", ctx, fail_at="critical", partial_at=None).verdict == "pass"


# --- sast.max_severity -------------------------------------------------------------------------


def sast(rule_id: str, severity: Severity | None) -> Fact:
    return fact("sast_finding", severity, rule_id=rule_id, category="security", message="m")


@pytest.mark.parametrize(
    ("severity", "expected"), [("low", "pass"), ("medium", "partial"), ("high", "fail")]
)
def test_sast_thresholds(severity: Severity, expected: str, tmp_path: Path) -> None:
    facts = [sast("python.lang.security.ssrf-requests", severity)]
    outcome = run("sast.max_severity", make_ctx(tmp_path, facts))
    assert outcome.verdict == expected


def test_sast_unknown_severity_never_reaches_a_threshold(tmp_path: Path) -> None:
    outcome = run("sast.max_severity", make_ctx(tmp_path, [sast("r", None)]))
    assert outcome.verdict == "pass"
    assert "1 lower-severity finding" in outcome.claim


def test_sast_exclude_rule_globs(tmp_path: Path) -> None:
    facts = [sast("python.lang.security.audit.ssrf-requests", "high")]
    ctx = make_ctx(tmp_path, facts)
    assert (
        run("sast.max_severity", ctx, exclude_rule_globs=["python.lang.security.audit.*"]).verdict
        == "pass"
    )
    assert run("sast.max_severity", ctx, exclude_rule_globs=["javascript.*"]).verdict == "fail"


# --- files.any_exists --------------------------------------------------------------------------


def test_any_exists_pass_cites_the_matched_file(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, files={".github/dependabot.yml": "version: 2\n", "app.py": "x = 1\n"})
    outcome = run("files.any_exists", ctx, globs=[".github/dependabot.y{a,}ml", "renovate.json"])
    assert outcome.verdict == "pass"
    scan, code = outcome.evidence
    assert isinstance(scan, ScanEvidence) and scan.result_count == 1
    assert isinstance(code, CodeEvidence) and code.path == ".github/dependabot.yml"
    assert code.snippet == "version: 2"


def test_any_exists_globstar_and_dotfiles(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, files={"svc/.dockerignore": ""})
    outcome = run("files.any_exists", ctx, globs=["**/.dockerignore"])
    assert outcome.verdict == "pass"
    assert len(outcome.evidence) == 1  # an empty file has no lines to cite


def test_any_exists_fail_records_the_globs(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, files={"app.py": "x = 1\n"})
    outcome = run("files.any_exists", ctx, globs=["renovate.json"])
    assert outcome.verdict == "fail"
    scan = first_scan(outcome)
    assert len(outcome.evidence) == 1 and scan.tool == "files"
    assert (scan.query, scan.result_count) == ("any of ['renovate.json']", 0)


@pytest.mark.parametrize("params", [{}, {"globs": []}, {"globs": ["x"], "glob": ["y"]}])
def test_any_exists_params_are_validated(params: dict[str, JsonValue]) -> None:
    registered = get_rule("files.any_exists")
    assert registered is not None
    with pytest.raises(ValidationError):
        registered.parse_params(params)
