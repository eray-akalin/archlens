"""RuleContext (jailed, redacted reads), the rule registry and AppliesWhen."""

from pathlib import Path

import pytest

from archlens.evidence import SnippetReader
from archlens.models import AppliesWhen, CheckSpec, Rubric, RuleOutcome
from archlens.rubric import (
    RuleContext,
    RuleParams,
    get_rule,
    registered_rules,
    registry,
    rule,
    run_rule,
)
from archlens.security.redact import Redactor, SecretSpan
from tests.unit.rubric.helpers import make_ctx, tool_run

KEY = "AKIA" + "Q" * 16


# --- RuleContext -------------------------------------------------------------------------------


def test_read_text_is_redacted(tmp_path: Path) -> None:
    span = SecretSpan("conf.py", 2, 10, 2, 21, "custom")
    files = {"conf.py": f'key = "{KEY}"\ntoken = "abcdefghijkl"\n'}
    ctx = make_ctx(tmp_path, files=files, redactor=Redactor([span]))
    text = ctx.read_text("conf.py")
    assert text is not None
    assert KEY not in text and "abcdefghijkl" not in text
    assert text.count("«redacted:") == 2


def test_read_text_only_reads_listed_readable_files(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, files={"a.txt": "a\n", "big.txt": "x" * 300})
    (tmp_path / "late.txt").write_text("written after the snapshot\n")
    (tmp_path / "link.txt").symlink_to(tmp_path / "a.txt")
    assert ctx.read_text("a.txt") == "a\n"
    assert ctx.read_text("big.txt", max_bytes=100) is None
    assert ctx.read_text("late.txt") is None
    assert ctx.read_text("link.txt") is None
    assert ctx.read_text("../outside.txt") is None
    assert ctx.read_text("/etc/hosts") is None


def test_symlinks_in_the_listing_are_never_read(tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text("outside\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "link.txt").symlink_to(tmp_path / "secret.txt")
    ctx = make_ctx(repo)
    assert [f.path for f in ctx.files] == ["link.txt"]
    assert ctx.read_text("link.txt") is None
    assert ctx.evidence("link.txt", 1) is None


def test_evidence_matches_the_verifier_reader(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, files={"app.py": f'x = 1\nkey = "{KEY}"\ny = 2\n'})
    evidence = ctx.evidence("app.py", 1, 2)
    assert evidence == SnippetReader(tmp_path).evidence("app.py", 1, 2)
    assert evidence is not None and KEY not in evidence.snippet
    assert ctx.evidence("missing.py", 1) is None


def test_tool_run_returns_the_latest_record(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    assert ctx.tool_run("gitleaks") is not None
    assert ctx.tool_run("hadolint") is None
    later = ctx.facts.model_copy(
        update={"tool_runs": [*ctx.facts.tool_runs, tool_run("gitleaks", "error")]}
    )
    rerun = RuleContext(later, ctx.profile, ctx.files, ctx.reader)
    record = rerun.tool_run("gitleaks")
    assert record is not None and record.status == "error"


# --- registry ----------------------------------------------------------------------------------


@pytest.fixture
def scratch_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rules registered inside a test disappear afterwards."""
    monkeypatch.setattr(registry, "_REGISTRY", dict(registry.registered_rules()))


class EmptyParams(RuleParams):
    pass


def check(rule_name: str, **params: object) -> CheckSpec:
    return CheckSpec.model_validate(
        {
            "id": "EX-01",
            "title": "t",
            "type": "deterministic",
            "severity": "low",
            "rationale": "r",
            "remediation": "r",
            "rule": rule_name,
            "params": params,
        }
    )


RUBRIC = Rubric(
    metric="example", title="E", version="1.2.3", weight=1.0, description="d", checks=[]
)


def test_run_rule_wraps_the_outcome(tmp_path: Path) -> None:
    result = run_rule(check("files.any_exists", globs=["*.md"]), RUBRIC, make_ctx(tmp_path))
    assert (result.check_id, result.metric, result.origin) == ("EX-01", "example", "deterministic")
    assert (result.verdict, result.confidence, result.rubric_version) == ("fail", "high", "1.2.3")
    assert result.evidence and result.citations == []


def test_run_rule_turns_a_crash_into_unknown(tmp_path: Path, scratch_registry: None) -> None:
    @rule("testonly.boom")
    def boom(ctx: RuleContext, params: EmptyParams) -> RuleOutcome:
        raise RuntimeError("bug")

    result = run_rule(check("testonly.boom"), RUBRIC, make_ctx(tmp_path))
    assert (result.verdict, result.reason) == ("unknown", "rule_error")


@pytest.mark.parametrize(
    ("spec", "reason"),
    [
        (check("nope.missing"), "rule_unknown"),
        (check("files.any_exists"), "params_invalid"),
        (check("tests.ratio", pass_at="lots"), "params_invalid"),
    ],
)
def test_run_rule_never_raises(spec: CheckSpec, reason: str, tmp_path: Path) -> None:
    result = run_rule(spec, RUBRIC, make_ctx(tmp_path))
    assert (result.verdict, result.reason) == ("unknown", reason)


def test_registration_errors(scratch_registry: None) -> None:
    def no_annotation(ctx: RuleContext, params) -> RuleOutcome:
        raise NotImplementedError

    def plain_dict(ctx: RuleContext, params: dict[str, int]) -> RuleOutcome:
        raise NotImplementedError

    def fine(ctx: RuleContext, params: EmptyParams) -> RuleOutcome:
        raise NotImplementedError

    with pytest.raises(ValueError, match="family"):
        rule("NoDot")
    with pytest.raises(TypeError):
        rule("testonly.a")(no_annotation)
    with pytest.raises(TypeError):
        rule("testonly.b")(plain_dict)  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValueError, match="twice"):
        rule("files.any_exists")(fine)


def test_rule_names_match_their_family_module() -> None:
    for name, registered in registered_rules().items():
        family = name.split(".")[0]
        assert registered.func.__module__ == f"archlens.rubric.rules.{family}", name


def test_params_have_defaults_except_required_globs() -> None:
    for name, registered in registered_rules().items():
        if name == "files.any_exists":
            continue
        registered.parse_params({})  # every default documented in RUBRICS §5


def test_get_rule() -> None:
    assert get_rule("ci.present") is not None
    assert get_rule("ci.absent") is None


# --- AppliesWhen -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cond", "expected"),
    [
        ({}, True),
        ({"any": ["has_ci"]}, True),
        ({"any": ["has_k8s", "has_iac"]}, False),
        ({"all": ["has_ci", "has_tests"]}, True),
        ({"all": ["has_ci", "has_k8s"]}, False),
        ({"none": ["has_k8s"]}, True),
        ({"none": ["has_ci"]}, False),
        ({"any": ["unknown_flag"]}, False),
        ({"all": ["has_ci"], "any": ["has_tests"], "none": ["has_iac"]}, True),
    ],
)
def test_applies_when(cond: dict[str, list[str]], expected: bool) -> None:
    flags = {"has_ci": True, "has_tests": True, "has_k8s": False, "has_iac": False}
    assert AppliesWhen.model_validate(cond).holds(flags) is expected
