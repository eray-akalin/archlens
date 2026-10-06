"""Secret redaction (docs/SECURITY.md §5).

Two inputs: exact spans from gitleaks findings (`SecretSpan`, located without the value since
gitleaks runs with --redact) and a fallback pattern set. A secret becomes
`«redacted:<rule_id>:<first 4 hex of sha256(value)>»`, followed by as many newlines as the value
spanned, so line numbers of everything after it stay valid for citations. Redaction is idempotent.
"""

import hashlib
import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

_TOKEN_PREFIX = "«redacted:"
_GENERIC_MIN_ENTROPY = 3.5


@dataclass(frozen=True)
class SecretSpan:
    """A secret's location in a file; lines and columns are 1-based and inclusive."""

    path: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    rule_id: str


@dataclass(frozen=True)
class _Pattern:
    rule_id: str
    regex: re.Pattern[str]
    group: int = 0  # which group is the secret value
    min_entropy: float = 0.0


_PATTERNS: tuple[_Pattern, ...] = (
    _Pattern(
        "private-key",
        re.compile(
            r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"
            r"[\s\S]*?"
            r"-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"
        ),
    ),
    _Pattern(
        "aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA|A3T[A-Z0-9])[A-Z2-7]{16}\b")
    ),
    _Pattern(
        "github-token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b"),
    ),
    _Pattern("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    _Pattern("gcp-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    _Pattern("stripe-key", re.compile(r"\b[rs]k_live_[0-9A-Za-z]{20,}\b")),
    _Pattern("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    _Pattern("azure-storage-key", re.compile(r"AccountKey=([A-Za-z0-9+/]{40,}={0,2})"), group=1),
    _Pattern("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    # `password = "..."`, `API_TOKEN: ...`, `aws_secret_access_key=...` with a long, random value.
    # Linear time on hostile input: a match starts only at an identifier's first character, the
    # keyword is checked in a lookahead, and the identifier is consumed possessively (`*+`), so a
    # long run of identifier characters is never re-scanned from every position.
    _Pattern(
        "generic-secret",
        re.compile(
            r"(?i)(?<![A-Za-z0-9_.-])"
            r"(?=[A-Za-z0-9_.-]*?(?:password|passwd|pwd|secret|token|api_?key|access_?key))"
            r"[A-Za-z0-9_.-]*+[\"']?\s*[:=]\s*[\"']?(?!«)([^\s\"'`,;)]{16,})"
        ),
        group=1,
        min_entropy=_GENERIC_MIN_ENTROPY,
    ),
)


def shannon_entropy(value: str) -> float:
    """Bits per character."""
    if not value:
        return 0.0
    counts = Counter(value)
    return -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values())


def redaction_token(rule_id: str, value: str) -> str:
    """`«redacted:<rule_id>:<4 hex>»` plus one newline per newline in `value`."""
    digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:4]
    return f"{_TOKEN_PREFIX}{rule_id}:{digest}»" + "\n" * value.count("\n")


def redact(text: str) -> str:
    """Apply the fallback patterns to arbitrary text (logs, tool output, report fields)."""
    for pattern in _PATTERNS:
        text = _apply_pattern(pattern, text)
    return text


class Redactor:
    """Redacts known secret spans (from gitleaks) plus the fallback patterns."""

    def __init__(self, spans: Iterable[SecretSpan] = ()) -> None:
        self._spans: dict[str, list[SecretSpan]] = {}
        for span in spans:
            self._spans.setdefault(span.path, []).append(span)

    def redact(self, text: str) -> str:
        return redact(text)

    def redact_excerpt(self, path: str, text: str, first_line: int = 1) -> str:
        """Redact `text`, a run of whole lines of `path` starting at `first_line`."""
        lines = text.splitlines(keepends=True)
        starts = [0]
        for line in lines:
            starts.append(starts[-1] + len(line))
        last_line = first_line + len(lines) - 1

        def offset(line_no: int, col: int, *, end: bool) -> int:
            """Character offset in `text`; spans running past the excerpt are clipped to it."""
            if line_no < first_line:
                return 0
            if line_no > last_line:
                return len(text)
            idx = line_no - first_line
            body_len = len(lines[idx].rstrip("\r\n"))
            col = min(max(col, 1), body_len if end else body_len + 1)
            return starts[idx] + (col if end else col - 1)  # end is inclusive → exclusive offset

        intervals = sorted(
            (
                offset(s.start_line, s.start_col, end=False),
                offset(s.end_line, s.end_col, end=True),
                s.rule_id,
            )
            for s in self._spans.get(path, [])
            if s.end_line >= first_line and s.start_line <= last_line
        )
        merged: list[tuple[int, int, str]] = []  # overlapping spans become one redaction
        for start, stop, rule in intervals:
            if stop <= start:
                continue
            if merged and start <= merged[-1][1]:
                prev_start, prev_stop, prev_rule = merged[-1]
                merged[-1] = (prev_start, max(prev_stop, stop), prev_rule)
            else:
                merged.append((start, stop, rule))
        for start, stop, rule in reversed(merged):  # right to left keeps earlier offsets valid
            if not text[start:stop].startswith(_TOKEN_PREFIX):
                text = text[:start] + redaction_token(rule, text[start:stop]) + text[stop:]
        return redact(text)


def _apply_pattern(pattern: _Pattern, text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        value = match.group(pattern.group)
        if value.startswith(_TOKEN_PREFIX) or shannon_entropy(value) < pattern.min_entropy:
            return match.group(0)
        start, end = match.span(pattern.group)
        whole_start = match.start(0)
        return (
            match.group(0)[: start - whole_start]
            + redaction_token(pattern.rule_id, value)
            + match.group(0)[end - whole_start :]
        )

    return pattern.regex.sub(replace, text)
