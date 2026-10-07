"""Seen-lines ledger (docs/LLM.md §4, CLAUDE.md rule 4).

Records every file line shown to a model, keyed by `(session_id, path, line)`: lines of tool
results and evidence lines of facts given in a prompt. The verifier's mechanical step rejects a
citation to any line the citing session never saw. Sessions are isolated from each other.
"""

from collections.abc import Iterable, Mapping

from pydantic import JsonValue, TypeAdapter

_DUMP = TypeAdapter(dict[str, dict[str, list[tuple[int, int]]]])


class SeenLines:
    def __init__(self) -> None:
        self._seen: dict[str, dict[str, set[int]]] = {}

    def add(self, session_id: str, path: str, start_line: int, end_line: int | None = None) -> None:
        """Mark lines start..end (inclusive; default just `start_line`) of `path` as seen."""
        stop = start_line if end_line is None else end_line
        self.add_lines(session_id, path, range(max(1, start_line), stop + 1))

    def add_lines(self, session_id: str, path: str, lines: Iterable[int]) -> None:
        wanted = {line for line in lines if line >= 1}
        if wanted:
            self._seen.setdefault(session_id, {}).setdefault(path, set()).update(wanted)

    def lines(self, session_id: str, path: str) -> frozenset[int]:
        return frozenset(self._seen.get(session_id, {}).get(path, ()))

    def missing(self, session_id: str, path: str, start_line: int, end_line: int) -> list[int]:
        """Lines of start..end the session never saw (empty → the whole range was shown)."""
        seen = self._seen.get(session_id, {}).get(path, set())
        return [line for line in range(start_line, end_line + 1) if line not in seen]

    def has(self, session_id: str, path: str, start_line: int, end_line: int) -> bool:
        return not self.missing(session_id, path, start_line, end_line)

    def sessions(self) -> list[str]:
        return sorted(self._seen)

    def dump(self) -> dict[str, JsonValue]:
        """JSON for checkpoints: `{session: {path: [[start, end], ...]}}`, ranges merged."""
        return {
            session: {path: _ranges(lines) for path, lines in sorted(paths.items())}
            for session, paths in sorted(self._seen.items())
        }

    def update(self, data: Mapping[str, JsonValue]) -> None:
        """Add the lines of a `dump` (e.g. a metric's checkpointed sessions on resume)."""
        for session, paths in _DUMP.validate_python(data).items():
            for path, ranges in paths.items():
                for start, end in ranges:
                    self.add(session, path, start, end)

    def subset(self, prefix: str) -> dict[str, JsonValue]:
        """`dump` restricted to sessions whose id starts with `prefix`."""
        return {s: v for s, v in self.dump().items() if s.startswith(prefix)}

    @classmethod
    def load(cls, data: Mapping[str, JsonValue]) -> "SeenLines":
        """Inverse of `dump`. Raises pydantic.ValidationError for a malformed checkpoint."""
        ledger = cls()
        for session, paths in _DUMP.validate_python(data).items():
            for path, ranges in paths.items():
                for start, end in ranges:
                    ledger.add(session, path, start, end)
        return ledger


def _ranges(lines: set[int]) -> list[JsonValue]:
    out: list[JsonValue] = []
    start = prev = None
    for line in sorted(lines):
        if start is None or prev is None:
            start = prev = line
        elif line == prev + 1:
            prev = line
        else:
            out.append([start, prev])
            start = prev = line
    if start is not None and prev is not None:
        out.append([start, prev])
    return out
