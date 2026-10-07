"""Shared pytest configuration.

Live tests make real LLM calls and cost money. They run only when the *process environment* has
ARCHLENS_LIVE_TESTS=1. The value is read from os.environ on purpose — never from `.env` or
Settings — so `pytest -m live` on its own can never spend money.
"""

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests.fixture_repos import MaterializedRepo

LIVE_ENV_VAR = "ARCHLENS_LIVE_TESTS"
_NON_UNIT_MARKERS = frozenset({"integration", "scanners", "live"})


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    live_enabled = os.environ.get(LIVE_ENV_VAR) == "1"
    skip_live = pytest.mark.skip(reason=f"live test: set {LIVE_ENV_VAR}=1 to run (costs money)")
    for item in items:
        names = {mark.name for mark in item.iter_markers()}
        if "live" in names and not live_enabled:
            item.add_marker(skip_live)
        if not names & _NON_UNIT_MARKERS:
            item.add_marker(pytest.mark.unit)


@pytest.fixture
def tiny_service(tmp_path: Path) -> "MaterializedRepo":
    """tiny_service copied to a temp dir, answer key removed, secret and vulnerable pin injected."""
    # Imported lazily: test_live_guard runs this conftest in an isolated pytester session.
    from tests.fixture_repos import materialize_tiny_service

    return materialize_tiny_service(tmp_path / "tiny_service")


@pytest.fixture(autouse=True)
def _offline_guard(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLAUDE.md rule 7: outside `scanners`/`live` tests, no internet socket may be opened and no
    external scanner may run (osv-scanner would query the OSV API)."""
    names = {mark.name for mark in request.node.iter_markers()}
    if names & {"scanners", "live"}:
        return
    import socket

    from archlens.facts import runner

    real_connect = socket.socket.connect

    def connect(sock: socket.socket, address: object) -> None:
        if isinstance(address, tuple):  # AF_INET/AF_INET6; unix sockets pass a path
            raise RuntimeError(f"network access in an offline test: {address!r}")
        real_connect(sock, address)  # type: ignore[arg-type]

    def no_scanners(*args: object, **kwargs: object) -> None:
        raise RuntimeError("external scanners ran in an offline test; pass scanners=False")

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(runner, "run_scanners", no_scanners)
