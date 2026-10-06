"""Documentation files → `doc_file` facts (DOC-02 API spec, DOC-03 architecture/ADRs, DOC-05)."""

import re

from pydantic import JsonValue

from archlens.facts.base import ScanContext, make_fact
from archlens.facts.extractors.common import code_evidence
from archlens.models import Fact

SOURCE = "fs:docs"
DOC_TYPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("readme", re.compile(r"(?i)(^|/)readme(\.[\w]+)?$")),
    (
        "adr",
        re.compile(
            r"(?i)(^|/)(adrs?|decisions)(/|$)|(^|/)adr[-_]?\d+[^/]*\.md$|(^|/)docs/[^/]*adr[^/]*$"
        ),
    ),
    ("architecture", re.compile(r"(?i)(^|/)architecture[^/]*\.(md|rst|txt|adoc)$")),
    ("openapi", re.compile(r"(?i)(^|/)(openapi|swagger)[^/]*\.(ya?ml|json)$")),
    ("contributing", re.compile(r"(?i)(^|/)contributing(\.[\w]+)?$")),
    ("changelog", re.compile(r"(?i)(^|/)(changelog|history|changes)(\.[\w]+)?$")),
)


def doc_type(path: str) -> str | None:
    return next((kind for kind, pattern in DOC_TYPES if pattern.search(path)), None)


def extract_doc_files(ctx: ScanContext) -> list[Fact]:
    facts: list[Fact] = []
    for f in ctx.snapshot.files:
        kind = doc_type(f.path)
        if kind is None or f.is_vendored or f.symlink_target is not None:
            continue
        attributes: dict[str, JsonValue] = {"path": f.path, "type": kind, "loc": f.loc}
        facts.append(make_fact("doc_file", SOURCE, attributes, code_evidence(ctx, f.path, 1)))
    return facts
