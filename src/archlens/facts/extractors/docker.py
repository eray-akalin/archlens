"""Dockerfiles → `dockerfile` facts (CTR-01 non-root, CTR-02 pinned base, CTR-03 multi-stage,
CTR-05 healthcheck). Parsed as text instructions (continuations joined); nothing is built.
"""

import re

from pydantic import JsonValue

from archlens.facts.base import ScanContext, make_fact
from archlens.facts.extractors.common import code_evidence
from archlens.models import Fact

SOURCE = "ast:docker"
_FROM = re.compile(r"^FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?", re.IGNORECASE)


def instructions(lines: list[str]) -> list[tuple[int, str, str]]:
    """(line, INSTRUCTION, arguments) with `\\` continuations joined and comments dropped."""
    result: list[tuple[int, str, str]] = []
    buffer, start = "", 0
    for number, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not buffer and (not stripped or stripped.startswith("#")):
            continue
        if not buffer:
            start = number
        if stripped.endswith("\\"):
            buffer += stripped[:-1] + " "
            continue
        buffer += stripped
        keyword, _, rest = buffer.partition(" ")
        result.append((start, keyword.upper(), rest.strip()))
        buffer = ""
    return result


def parse_dockerfile(lines: list[str]) -> tuple[dict[str, JsonValue], int] | None:
    """(attributes, line of the final FROM); None if there is no FROM."""
    stages: list[tuple[int, str, str | None]] = []  # line, base, alias
    user: str | None = None
    healthcheck = False
    for line, keyword, rest in instructions(lines):
        if keyword == "FROM":
            match = _FROM.match(f"FROM {rest}")
            if match:
                stages.append((line, match.group(1), match.group(2)))
                user, healthcheck = None, False  # USER/HEALTHCHECK are per stage
        elif keyword == "USER" and stages:
            user = rest.split()[0] if rest else None
        elif keyword == "HEALTHCHECK" and stages:
            healthcheck = not rest.upper().startswith("NONE")
    if not stages:
        return None
    aliases = [alias for _, _, alias in stages if alias]
    attributes: dict[str, JsonValue] = {
        "base_images": [base for _, base, _ in stages],
        "external_bases": [
            base for _, base, _ in stages if base not in aliases and base.lower() != "scratch"
        ],
        "stages": len(stages),
        "stage_aliases": list[JsonValue](aliases),
        "final_base": stages[-1][1],
        "user": user,
        "has_healthcheck": healthcheck,
    }
    return attributes, stages[-1][0]


def extract_dockerfiles(ctx: ScanContext) -> list[Fact]:
    facts: list[Fact] = []
    for f in ctx.snapshot.files:
        if f.language != "dockerfile" or not f.readable or f.is_vendored:
            continue
        parsed = parse_dockerfile(ctx.reader.lines(f.path) or [])
        if parsed is None:
            continue
        attributes, line = parsed
        attributes = {"path": f.path, **attributes}
        facts.append(make_fact("dockerfile", SOURCE, attributes, code_evidence(ctx, f.path, line)))
    return facts
