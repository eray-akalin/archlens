"""Fact-based precondition checks shared by mutations (EVALUATION.md §2: never model-based)."""

from archlens.globs import glob_match
from archlens.models import Fact, FactSet, severity_rank
from archlens.rubric.rules.ci import is_restricted
from archlens.rubric.rules.docker import is_pinned_image, runs_as_non_root
from archlens.rubric.rules.secrets import NoneFoundParams

ALLOWED_ACTION_OWNERS = ("actions", "github")


def _str(fact: Fact, key: str) -> str:
    value = fact.attributes.get(key)
    return value if isinstance(value, str) else ""


def no_secrets(facts: FactSet) -> bool:
    excludes = NoneFoundParams().exclude_globs
    return not any(not glob_match(_str(f, "path"), excludes) for f in facts.by_kind("secret"))


def no_severe_vulns(facts: FactSet) -> bool:
    return all(
        severity_rank(f.severity) < severity_rank("high") for f in facts.by_kind("vuln_dependency")
    )


def non_root_dockerfiles(facts: FactSet) -> list[str]:
    return [_str(f, "path") for f in facts.by_kind("dockerfile") if runs_as_non_root(f)]


def pinned_dockerfiles(facts: FactSet) -> list[str]:
    out: list[str] = []
    for f in facts.by_kind("dockerfile"):
        bases = f.attributes.get("external_bases")
        images = [str(b) for b in bases] if isinstance(bases, list) else []
        if images and all(is_pinned_image(i) for i in images):
            out.append(_str(f, "path"))
    return out


def github_workflows(facts: FactSet) -> list[Fact]:
    return [f for f in facts.by_kind("ci_workflow") if _str(f, "system") == "github_actions"]


def all_workflows_restricted(facts: FactSet) -> bool:
    workflows = github_workflows(facts)
    return bool(workflows) and all(is_restricted(w) for w in workflows)


def third_party_actions(facts: FactSet) -> list[Fact]:
    return [
        s for s in facts.by_kind("ci_step")
        if _str(s, "system") == "github_actions" and "/" in _str(s, "uses")
        and _str(s, "uses").split("/", 1)[0].lower() not in ALLOWED_ACTION_OWNERS
    ]  # fmt: skip


def all_actions_pinned(facts: FactSet) -> bool:
    steps = third_party_actions(facts)
    return bool(steps) and all(s.attributes.get("pinned") is True for s in steps)


def has_tests_in_ci(facts: FactSet) -> bool:
    return bool(facts.by_kind("test_file")) and any(
        _str(s, "run_kind") == "test" for s in facts.by_kind("ci_step")
    )


def readme_lines(facts: FactSet) -> int:
    readmes = [f for f in facts.by_kind("doc_file") if _str(f, "type") == "readme"]
    return max((int(f.attributes.get("loc") or 0) for f in readmes), default=0)  # type: ignore[arg-type]


def has_file(facts: FactSet, path: str) -> bool:
    return any(_str(f, "path") == path for f in facts.by_kind("file_metrics"))
