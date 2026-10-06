"""Stage 2: RepoProfile from the snapshot and facts (docs/ARCHITECTURE.md §2.2, RUBRICS.md §2).

Deterministic: lists are sorted, languages are ordered by LOC (desc) then name. The only file
reads are the plain-text outbound-HTTP probes, through the jailed SnippetReader.
"""

from collections import Counter

from archlens.evidence import SnippetReader
from archlens.facts.extractors.common import CODE_LANGUAGES
from archlens.models import FactSet, RepoProfile, RepoSnapshot
from archlens.profile.detectors import (
    DATA_LIBRARIES,
    ENTRYPOINTS,
    HTTP_FRAMEWORKS,
    IAC_FILES,
    K8S_MARKERS,
    LANGUAGE_FLAGS,
    LANGUAGE_SHARE,
    MESSAGING,
    OUTBOUND_HTTP,
    OUTBOUND_TEXT,
    PACKAGE_MANAGERS,
    matches,
)

__all__ = ["FLAGS", "build_profile"]

FLAGS = (
    "has_http_api", "has_database", "has_message_consumer", "has_outbound_http", "has_dockerfile",
    "has_compose", "has_k8s", "has_iac", "has_ci", "has_deploy_step", "has_tests",
    *LANGUAGE_FLAGS,
)  # fmt: skip


def _dependency_names(facts: FactSet) -> set[str]:
    names = {
        str(f.attributes["to_module"])
        for f in facts.by_kind("import_edge")
        if not f.attributes["internal"]
    }
    names |= {str(f.attributes["name"]) for f in facts.by_kind("dependency")}
    return names


def _detected(names: set[str], table: dict[str, tuple[str, ...]]) -> set[str]:
    return {label for label, prefixes in table.items() if any(matches(n, prefixes) for n in names)}


def _outbound_text(snapshot: RepoSnapshot, reader: SnippetReader | None) -> bool:
    if reader is None:
        return False
    for f in snapshot.files:
        pattern = OUTBOUND_TEXT.get(f.language or "")
        if pattern is None or not f.readable or f.is_vendored or f.is_generated:
            continue
        if any(pattern.search(line) for line in reader.lines(f.path) or []):
            return True
    return False


def build_profile(
    snapshot: RepoSnapshot, facts: FactSet, reader: SnippetReader | None = None
) -> RepoProfile:
    """Profile with every flag of RUBRICS.md §2 set to True or False (never missing)."""
    code = [
        f
        for f in snapshot.files
        if f.language in CODE_LANGUAGES and not f.is_vendored and not f.is_generated
    ]
    loc: Counter[str] = Counter()
    for f in code:
        loc[str(f.language)] += f.loc
    total = sum(loc.values())
    languages = dict(sorted(loc.items(), key=lambda item: (-item[1], item[0])))

    names = _dependency_names(facts)
    http = _detected(names, HTTP_FRAMEWORKS)
    data = _detected(names, DATA_LIBRARIES)
    messaging = _detected(names, MESSAGING)
    paths = [f.path for f in snapshot.files if not f.is_vendored]
    deploy = facts.by_kind("deploy_config")

    flags = {
        "has_http_api": bool(facts.by_kind("route")) or bool(http),
        "has_database": bool(data),
        "has_message_consumer": bool(messaging),
        "has_outbound_http": any(matches(n, OUTBOUND_HTTP) for n in names)
        or _outbound_text(snapshot, reader),
        "has_dockerfile": any(
            f.language == "dockerfile" for f in snapshot.files if not f.is_vendored
        ),
        "has_compose": any(f.attributes["kind"] == "compose_service" for f in deploy),
        "has_k8s": any(f.attributes["kind"] == "k8s_workload" for f in deploy)
        or any(K8S_MARKERS.search(p) for p in paths),
        "has_iac": any(IAC_FILES.search(p) for p in paths),
        "has_ci": bool(facts.by_kind("ci_workflow")),
        "has_deploy_step": any(
            f.attributes["run_kind"] == "deploy" for f in facts.by_kind("ci_step")
        ),
        "has_tests": bool(facts.by_kind("test_file")),
    }
    for flag, family in LANGUAGE_FLAGS.items():
        share = sum(loc[lang] for lang in family) / total if total else 0.0
        flags[flag] = share >= LANGUAGE_SHARE

    return RepoProfile(
        languages=languages,
        frameworks=sorted(http | data | messaging),
        package_managers=sorted(
            {name for name, pattern in PACKAGE_MANAGERS if any(pattern.search(p) for p in paths)}
        ),
        ci_systems=sorted({str(f.attributes["system"]) for f in facts.by_kind("ci_workflow")}),
        test_frameworks=sorted(
            {str(f.attributes["framework"]) for f in facts.by_kind("test_file")}
        ),
        flags={flag: flags[flag] for flag in FLAGS},
        entrypoints=sorted(p for p in paths if ENTRYPOINTS.search(p)),
    )
