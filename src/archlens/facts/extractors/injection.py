"""Repository text aimed at AI reviewers → `injection_attempt` facts (SECURITY.md §5).

Generic prompt-injection signatures — text that addresses AI reviewers or assessment tools,
overrides earlier instructions, asks for perfect scores, imitates our `repo_data` delimiter or a
chat role. A hit is informational: it never changes a verdict by itself. Every evaluator session
gets these facts so the model treats the marked text as untrusted, and the count shows in the
report's tool runs. Repos that build LLM features legitimately contain prompts; their hits are
false positives by design and cost nothing but a warning.
"""

import re

from pydantic import JsonValue

from archlens.facts.base import ScanContext, make_fact
from archlens.facts.extractors.common import code_evidence
from archlens.models import Fact

SOURCE = "fs:injection"
KIND = "injection_attempt"
MAX_LINE_CHARS = 2000
MAX_PER_FILE = 5
MAX_TOTAL = 50
MAX_FILE_BYTES = 500_000

_AI = r"(?:AI|LLM|GPT|language[- ]model|automated)"
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "override",
        re.compile(
            r"(?i)\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+|the\s+)?"
            r"(?:previous|prior|earlier|above|preceding)\s+(?:instructions|prompts|rules|guidance)"
        ),
    ),
    (
        "addresses_ai",
        re.compile(
            rf"(?i)\b(?:if\s+you\s+are|you\s+are|as)\s+an?\s+{_AI}\s+"
            r"(?:reviewer|assessor|auditor|system|model|assistant|agent|tool)\b"
            rf"|\b{_AI}\s+(?:reviewers?|assessors?|auditors?|graders?|evaluators?|"
            r"assessment\s+tools?)\b"
        ),
    ),
    (
        "score_request",
        re.compile(
            r"(?i)\b(?:rate|score|grade|mark)\b[^\n]{0,40}"
            r"(?:10\s*/\s*10|100\s*%|perfect|full\s+marks|every\s+(?:metric|check))"
            r"|\b(?:answer|respond|return|report)\s+[`'\"]?pass[`'\"]?\b[^\n]{0,40}"
            r"\b(?:all|every)\s+checks?\b"
        ),
    ),
    ("delimiter", re.compile(r"(?i)</?\s*repo_data\b")),
    ("role_spoof", re.compile(r"^\s*(?:SYSTEM|ASSISTANT)\s*:\s+\S")),
)


def matches(line: str) -> list[str]:
    """Names of the signatures a line matches."""
    head = line[:MAX_LINE_CHARS]
    return [name for name, pattern in PATTERNS if pattern.search(head)]


def extract_injection_attempts(ctx: ScanContext) -> list[Fact]:
    facts: list[Fact] = []
    for f in ctx.snapshot.files:
        if len(facts) >= MAX_TOTAL:
            break
        if not f.readable or f.is_vendored or f.is_generated or f.size > MAX_FILE_BYTES:
            continue
        found = 0
        for number, line in enumerate(ctx.reader.lines(f.path) or [], start=1):
            names = matches(line)
            if not names:
                continue
            attributes: dict[str, JsonValue] = {
                "path": f.path,
                "line": number,
                "signatures": list[JsonValue](names),
            }
            facts.append(make_fact(KIND, SOURCE, attributes, code_evidence(ctx, f.path, number)))
            found += 1
            if found >= MAX_PER_FILE or len(facts) >= MAX_TOTAL:
                break
    return facts
