"""size.max_file_loc, complexity.ccn_ratio, imports.no_cycles, imports.any_of, deps.lockfile_present."""

from pathlib import Path

import pytest

from archlens.models import Evidence, Fact, ScanEvidence, ToolStatus
from archlens.rubric.rules.imports import strongly_connected
from tests.unit.rubric.helpers import fact, first_scan, make_ctx, run


def metrics(path: str, loc: int, *, generated: bool = False) -> Fact:
    return fact(
        "file_metrics", evidence=[], path=path, loc=loc, is_generated=generated, is_test=False
    )


def function(name: str, ccn: int) -> Fact:
    return fact("function_metrics", name=name, ccn=ccn, nloc=10, params=1)


def edge(a: str, b: str, *, internal: bool = True, language: str = "python") -> Fact:
    evidence: list[Evidence] = [
        ScanEvidence(tool="t", tool_version="1", query=f"{a}->{b}", result_count=1)
    ]
    return fact(
        "import_edge", evidence=evidence, from_module=a, to_module=b, internal=internal,
        language=language,
    )  # fmt: skip


def manifest(path: str, locked: bool) -> Fact:
    return fact("manifest", path=path, type="pyproject", has_lockfile=locked, dependency_count=3)


TOOL_RULES = [
    ("size.max_file_loc", "ast:metrics"),
    ("complexity.ccn_ratio", "lizard"),
    ("imports.no_cycles", "ast:imports"),
    ("imports.any_of", "ast:imports"),
    ("deps.lockfile_present", "ast:manifests"),
]


@pytest.mark.parametrize("status", ["error", "timeout", None])
@pytest.mark.parametrize(("name", "tool"), TOOL_RULES)
def test_tool_failure_is_unknown(
    name: str, tool: str, status: ToolStatus | None, tmp_path: Path
) -> None:
    outcome = run(name, make_ctx(tmp_path, status={tool: status}, flags={"lang_python": True}))
    assert outcome.verdict == "unknown"


# --- size.max_file_loc -------------------------------------------------------------------------


def test_file_size(tmp_path: Path) -> None:
    small = [metrics("a.py", 200), metrics("gen.py", 5000, generated=True)]
    assert run("size.max_file_loc", make_ctx(tmp_path / "a", small)).verdict == "pass"
    two = [*small, metrics("b.py", 1500), metrics("c.py", 1200)]
    outcome = run("size.max_file_loc", make_ctx(tmp_path / "b", two))
    assert outcome.verdict == "partial" and outcome.claim.startswith(
        "2 files over 1000 LOC: b.py (1500)"
    )
    three = [*two, metrics("d.py", 1001)]
    assert run("size.max_file_loc", make_ctx(tmp_path / "c", three)).verdict == "fail"
    assert run("size.max_file_loc", make_ctx(tmp_path / "d", two), threshold=2000).verdict == "pass"
    assert run("size.max_file_loc", make_ctx(tmp_path / "e")).verdict == "not_applicable"


# --- complexity.ccn_ratio ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("complex_", "total", "verdict"), [(0, 50, "pass"), (2, 50, "partial"), (5, 50, "fail")]
)
def test_ccn_ratio(complex_: int, total: int, verdict: str, tmp_path: Path) -> None:
    facts = [function(f"f{i}", 20 if i < complex_ else 3) for i in range(total)]
    outcome = run("complexity.ccn_ratio", make_ctx(tmp_path, facts))
    assert outcome.verdict == verdict and f"{complex_} of {total} functions" in outcome.claim


def test_ccn_ratio_edges(tmp_path: Path) -> None:
    assert run("complexity.ccn_ratio", make_ctx(tmp_path / "a")).verdict == "not_applicable"
    skipped = make_ctx(tmp_path / "b", status={"lizard": "skipped"})
    assert run("complexity.ccn_ratio", skipped).verdict == "not_applicable"
    facts = [function("f", 16)]
    assert run("complexity.ccn_ratio", make_ctx(tmp_path / "c", facts), ccn=20).verdict == "pass"


# --- imports.no_cycles -------------------------------------------------------------------------


def test_cycle_detected_with_evidence(tmp_path: Path) -> None:
    facts = [
        edge("app.a", "app.b"),
        edge("app.b", "app.c"),
        edge("app.c", "app.a"),
        edge("app.d", "app.a"),
    ]
    outcome = run("imports.no_cycles", make_ctx(tmp_path, facts, flags={"lang_python": True}))
    assert outcome.verdict == "fail" and "app.a ↔ app.b ↔ app.c" in outcome.claim
    assert len(outcome.evidence) == 4  # scan record + the three cycle edges


def test_no_cycle_and_external_edges_ignored(tmp_path: Path) -> None:
    facts = [
        edge("app.a", "app.b"),
        edge("app.b", "requests", internal=False),
        edge("requests", "app.a", internal=False),
    ]
    outcome = run("imports.no_cycles", make_ctx(tmp_path, facts, flags={"lang_python": True}))
    assert outcome.verdict == "pass" and first_scan(outcome).result_count == 0


def test_js_index_modules_close_cycles(tmp_path: Path) -> None:
    facts = [
        edge("src/x/index", "src/y", language="typescript"),
        edge("src/y", "src/x", language="typescript"),
    ]
    outcome = run("imports.no_cycles", make_ctx(tmp_path, facts, flags={"lang_js_ts": True}))
    assert outcome.verdict == "fail"


def test_cycles_not_applicable_for_other_languages(tmp_path: Path) -> None:
    outcome = run(
        "imports.no_cycles",
        make_ctx(tmp_path, [edge("a", "b"), edge("b", "a")], flags={"lang_go": True}),
    )
    assert (outcome.verdict, outcome.reason) == ("not_applicable", "language_not_supported")


def test_tarjan() -> None:
    graph = {"a": {"b"}, "b": {"a", "c"}, "c": {"d"}, "d": {"c"}, "e": set()}
    assert sorted(strongly_connected(graph)) == [["a", "b"], ["c", "d"], ["e"]]
    deep = {str(i): {str(i + 1)} for i in range(3000)} | {"3000": {"0"}}  # no recursion limit
    assert len(max(strongly_connected(deep), key=len)) == 3001


# --- imports.any_of ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("module", "verdict"),
    [("logging", "pass"), ("logging.handlers", "pass"), ("structlog", "pass"), ("pino/file", "pass"),
     ("Microsoft.Extensions.Logging.Abstractions", "pass"), ("log/slog", "pass"),
     ("loggingx", "fail"), ("requests", "fail")],
)  # fmt: skip
def test_logging_framework(module: str, verdict: str, tmp_path: Path) -> None:
    outcome = run("imports.any_of", make_ctx(tmp_path, [edge("app.main", module, internal=False)]))
    assert outcome.verdict == verdict


def test_any_of_params_and_no_imports(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path / "a", [edge("app.main", "tracing", internal=False)])
    assert run("imports.any_of", ctx, modules=["tracing"]).verdict == "pass"
    outcome = run("imports.any_of", make_ctx(tmp_path / "b"))
    assert (outcome.verdict, outcome.reason) == ("not_applicable", "no_imports")


# --- deps.lockfile_present ---------------------------------------------------------------------


def test_lockfiles(tmp_path: Path) -> None:
    locked = [manifest("pyproject.toml", True), manifest("web/package.json", True)]
    assert run("deps.lockfile_present", make_ctx(tmp_path / "a", locked)).verdict == "pass"
    mixed = [manifest("pyproject.toml", False), manifest("requirements.txt", True)]
    outcome = run("deps.lockfile_present", make_ctx(tmp_path / "b", mixed))
    assert (
        outcome.verdict == "fail" and "pyproject.toml" in outcome.claim
    )  # every manifest must be locked
    assert run("deps.lockfile_present", make_ctx(tmp_path / "c")).verdict == "not_applicable"
