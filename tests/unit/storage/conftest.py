"""Backend-parameterized fixtures for the storage contract suite.

To run the suite against another backend, add an entry to BACKENDS (M4.2 adds
`pytest.param("azure", marks=pytest.mark.live)` with a factory that targets a test account).
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from archlens.storage import Storage, open_local_storage

StorageFactory = Callable[[], Storage]


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def _local(tmp_path: Path, clock: FakeClock) -> StorageFactory:
    return lambda: open_local_storage(tmp_path / "data", clock=clock)


BACKENDS: dict[str, Callable[[Path, FakeClock], StorageFactory]] = {"local": _local}
BACKEND_PARAMS = [pytest.param("local")]


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(params=BACKEND_PARAMS)
def make_storage(
    request: pytest.FixtureRequest, tmp_path: Path, clock: FakeClock
) -> StorageFactory:
    """Each call opens a new Storage over the same backing location (to test persistence)."""
    return BACKENDS[request.param](tmp_path, clock)


@pytest.fixture
def storage(make_storage: StorageFactory) -> Storage:
    return make_storage()
