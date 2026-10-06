"""Seen-lines ledger."""

from archlens.tools.seen import SeenLines


def test_ranges_and_missing_lines() -> None:
    ledger = SeenLines()
    ledger.add("s1", "a.py", 3, 5)
    ledger.add("s1", "a.py", 9)
    ledger.add_lines("s1", "a.py", [0, -2])  # invalid line numbers are ignored
    assert ledger.lines("s1", "a.py") == {3, 4, 5, 9}
    assert ledger.has("s1", "a.py", 3, 5)
    assert ledger.missing("s1", "a.py", 4, 9) == [6, 7, 8]
    assert not ledger.has("s1", "b.py", 1, 1)


def test_sessions_are_isolated() -> None:
    ledger = SeenLines()
    ledger.add("eval", "a.py", 1, 10)
    assert ledger.has("eval", "a.py", 2, 3)
    assert not ledger.has("skeptic", "a.py", 2, 3)
    assert ledger.sessions() == ["eval"]


def test_dump_merges_ranges_and_round_trips() -> None:
    ledger = SeenLines()
    ledger.add("s", "a.py", 1, 3)
    ledger.add("s", "a.py", 4, 4)
    ledger.add("s", "a.py", 8, 9)
    ledger.add("t", "b.py", 2)
    dumped = ledger.dump()
    assert dumped == {"s": {"a.py": [[1, 4], [8, 9]]}, "t": {"b.py": [[2, 2]]}}
    restored = SeenLines.load(dumped)
    assert restored.dump() == dumped
