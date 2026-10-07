"""The autouse offline guard in tests/conftest.py (CLAUDE.md rule 7) actually blocks."""

import socket
from pathlib import Path

import pytest

from archlens.config import ToolsConfig
from archlens.facts.runner import collect_facts
from archlens.ingest.snapshot import build_snapshot
from archlens.models import IngestLimits


def test_internet_sockets_are_blocked() -> None:
    with pytest.raises(RuntimeError, match="network access in an offline test"):
        socket.create_connection(("example.com", 443), timeout=1)


def test_external_scanners_are_blocked(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    snapshot = build_snapshot(tmp_path, limits=IngestLimits())
    with pytest.raises(RuntimeError, match="external scanners ran"):
        collect_facts(
            tmp_path, snapshot, workdir=tmp_path / "w", tools=ToolsConfig(tools={}),
            tools_dir=tmp_path / "t",
        )  # fmt: skip
