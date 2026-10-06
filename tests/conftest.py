"""Shared pytest configuration.

Live tests make real LLM calls and cost money. They run only when the *process environment* has
ARCHLENS_LIVE_TESTS=1. The value is read from os.environ on purpose — never from `.env` or
Settings — so `pytest -m live` on its own can never spend money.
"""

import os

import pytest

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
