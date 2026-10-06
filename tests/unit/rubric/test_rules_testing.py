"""tests.present, tests.ratio, tests.coverage_configured."""

from pathlib import Path

import pytest

from archlens.models import CodeEvidence, Fact, ToolStatus
from tests.unit.rubric.helpers import ci_step, fact, first_scan, make_ctx, run


def tfile(path: str, tests: int = 3) -> Fact:
    return fact("test_file", path=path, framework="pytest", test_count=tests, assert_count=tests)


def metrics(path: str, loc: int, *, is_test: bool = False, is_generated: bool = False) -> Fact:
    return fact(
        "file_metrics",
        evidence=[],
        path=path,
        language="python",
        loc=loc,
        is_test=is_test,
        is_generated=is_generated,
        public_functions=0,
        documented_functions=0,
    )


# --- tests.present -----------------------------------------------------------------------------


def test_present_pass(tmp_path: Path) -> None:
    files = [tfile("tests/test_a.py", 2), tfile("tests/test_b.py", 5)]
    outcome = run("tests.present", make_ctx(tmp_path, files))
    assert outcome.verdict == "pass"
    assert outcome.claim == "2 test files with 7 tests (pytest)."
    assert files[0].evidence[0] in outcome.evidence


def test_present_fail_without_test_files(tmp_path: Path) -> None:
    outcome = run("tests.present", make_ctx(tmp_path))
    assert outcome.verdict == "fail"
    assert first_scan(outcome).result_count == 0


def test_present_min_test_files(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, [tfile("tests/test_a.py")])
    assert run("tests.present", ctx, min_test_files=2).verdict == "fail"


# --- tests.ratio -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("test_loc", "expected"), [(50, "pass"), (30, "pass"), (20, "partial"), (5, "fail")]
)
def test_ratio_thresholds(test_loc: int, expected: str, tmp_path: Path) -> None:
    facts = [
        metrics("app/a.py", 60),
        metrics("app/b.py", 40),
        metrics("tests/t.py", test_loc, is_test=True),
    ]
    outcome = run("tests.ratio", make_ctx(tmp_path, facts))
    assert outcome.verdict == expected
    assert f"{test_loc / 100:.2f}" in outcome.claim


def test_ratio_ignores_generated_files(tmp_path: Path) -> None:
    facts = [
        metrics("app/a.py", 100),
        metrics("app/gen_pb2.py", 5000, is_generated=True),
        metrics("tests/t.py", 40, is_test=True),
    ]
    assert run("tests.ratio", make_ctx(tmp_path, facts)).verdict == "pass"


def test_ratio_not_applicable_without_source(tmp_path: Path) -> None:
    outcome = run("tests.ratio", make_ctx(tmp_path, [metrics("tests/t.py", 10, is_test=True)]))
    assert (outcome.verdict, outcome.reason) == ("not_applicable", "no_source_code")


def test_ratio_custom_thresholds(tmp_path: Path) -> None:
    facts = [metrics("app/a.py", 100), metrics("tests/t.py", 20, is_test=True)]
    assert run("tests.ratio", make_ctx(tmp_path, facts), pass_at=0.2).verdict == "pass"


@pytest.mark.parametrize("status", ["error", "timeout", None])
@pytest.mark.parametrize(
    ("name", "tool"), [("tests.present", "ast:tests"), ("tests.ratio", "ast:metrics")]
)
def test_extractor_failure_is_unknown(
    name: str, tool: str, status: ToolStatus | None, tmp_path: Path
) -> None:
    outcome = run(name, make_ctx(tmp_path, status={tool: status}))
    assert outcome.verdict == "unknown"
    assert outcome.reason == (f"tool_{status}" if status else "tool_not_run")


# --- tests.coverage_configured -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "content", "line"),
    [
        (".coveragerc", "[run]\nbranch = True\n", 1),
        ("pyproject.toml", '[project]\nname = "x"\n\n[tool.coverage.run]\nbranch = true\n', 4),
        ("pyproject.toml", '[tool.pytest.ini_options]\naddopts = "--cov=app"\n', 2),
        ("setup.cfg", "[metadata]\nname = x\n[coverage:run]\nbranch = True\n", 3),
        ("web/jest.config.js", "module.exports = {\n  collectCoverage: true,\n};\n", 2),
        ("package.json", '{\n  "scripts": {\n    "test": "vitest --coverage"\n  }\n}\n', 3),
        (
            "src/Api.Tests/Api.Tests.csproj",
            '<Project>\n  <PackageReference Include="coverlet.collector" />\n</Project>\n',
            2,
        ),
        (
            "pom.xml",
            "<project>\n  <plugin><artifactId>jacoco-maven-plugin</artifactId></plugin>\n</project>\n",
            2,
        ),
        ("codecov.yml", "coverage:\n  status: {}\n", 1),
    ],
)
def test_coverage_from_config_files(path: str, content: str, line: int, tmp_path: Path) -> None:
    outcome = run("tests.coverage_configured", make_ctx(tmp_path, files={path: content}))
    assert outcome.verdict == "pass"
    code = [e for e in outcome.evidence if isinstance(e, CodeEvidence)]
    assert [(e.path, e.start_line) for e in code] == [(path, line)]


@pytest.mark.parametrize(
    ("command", "uses"),
    [
        ("pytest --cov=app --cov-report=xml", None),
        ("coverage run -m pytest", None),
        ("npm test -- --coverage", None),
        ('dotnet test --collect:"XPlat Code Coverage"', None),
        ("go test -coverprofile=c.out ./...", None),
        (None, "codecov/codecov-action@v4"),
    ],
)
def test_coverage_from_ci_steps(command: str | None, uses: str | None, tmp_path: Path) -> None:
    step = ci_step(run=command, uses=uses, run_kind="test")
    outcome = run("tests.coverage_configured", make_ctx(tmp_path, [step]))
    assert outcome.verdict == "pass"
    assert step.evidence[0] in outcome.evidence


def test_coverage_fail(tmp_path: Path) -> None:
    files = {
        "pyproject.toml": '[project]\nname = "x"\n',
        "package.json": '{"scripts": {"test": "jest"}}\n',
    }
    ctx = make_ctx(tmp_path, [ci_step(run="pytest -q", run_kind="test")], files=files)
    outcome = run("tests.coverage_configured", ctx)
    assert outcome.verdict == "fail"
    assert first_scan(outcome).result_count == 0


def test_coverage_ignores_vendored_files(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, files={"node_modules/lib/.nycrc": "{}\n"})
    assert run("tests.coverage_configured", ctx).verdict == "fail"


@pytest.mark.parametrize("status", ["error", "timeout", None])
def test_coverage_unknown_when_ci_unreadable(status: ToolStatus | None, tmp_path: Path) -> None:
    outcome = run("tests.coverage_configured", make_ctx(tmp_path, status={"ast:ci": status}))
    assert outcome.verdict == "unknown"


def test_coverage_config_still_passes_when_ci_extractor_failed(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, files={".coveragerc": "[run]\n"}, status={"ast:ci": "error"})
    assert run("tests.coverage_configured", ctx).verdict == "pass"
