"""Local-backend specifics: on-disk layout, atomic writes, backend selection."""

from pathlib import Path

import pytest

from archlens.config import Settings
from archlens.errors import ConfigError
from archlens.storage import open_local_storage, open_storage
from archlens.storage.local import LocalCheckpointStore

RUN = "01J9ZQ3V5Y7K8M2N4P6R8T0VWZ"


async def test_layout_matches_architecture(tmp_path: Path) -> None:
    storage = open_local_storage(tmp_path)
    await storage.artifacts.put(RUN, "report.md", b"m")
    await storage.checkpoints.save(RUN, "evaluate/security", b"[]")
    run_dir = tmp_path / "runs" / RUN
    assert (run_dir / "artifacts" / "report.md").read_bytes() == b"m"
    assert (run_dir / "checkpoints" / "evaluate" / "security.json").read_bytes() == b"[]"
    assert (tmp_path / "cache.sqlite").is_file()


async def test_failed_write_leaves_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalCheckpointStore(tmp_path)
    await store.save(RUN, "facts", b"old")

    def boom(self: Path, target: Path) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        await store.save(RUN, "facts", b"new")
    monkeypatch.undo()

    checkpoints = tmp_path / "runs" / RUN / "checkpoints"
    assert await store.load(RUN, "facts") == b"old"
    assert [p.name for p in checkpoints.iterdir()] == ["facts.json"]  # temp file cleaned up


def _settings(**values: object) -> Settings:
    # _env_file=None: never read the developer's .env in tests.
    return Settings(_env_file=None, **values)  # pyright: ignore[reportCallIssue]


def test_open_storage_selects_backend(tmp_path: Path) -> None:
    assert open_storage(_settings(storage="local", data_dir=tmp_path)).artifacts is not None
    with pytest.raises(ConfigError) as info:
        open_storage(_settings(storage="azure", data_dir=tmp_path))
    assert info.value.field == "ARCHLENS_STORAGE"
