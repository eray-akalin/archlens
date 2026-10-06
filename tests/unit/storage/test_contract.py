"""Storage contract: every backend must pass these (local now, Azure in M4.2)."""

import asyncio

import pytest

from archlens.errors import StorageKeyError
from archlens.models import RunState, StageState
from archlens.storage import Storage
from tests.unit.storage.conftest import FakeClock, StorageFactory

RUN = "01J9ZQ3V5Y7K8M2N4P6R8T0VWZ"
OTHER = "01J9ZQ3V5Y7K8M2N4P6R8T0VX0"


# --- artifacts --------------------------------------------------------------------------------


async def test_artifact_round_trip_and_overwrite(storage: Storage) -> None:
    await storage.artifacts.put(RUN, "report.md", b"# v1")
    assert await storage.artifacts.get(RUN, "report.md") == b"# v1"
    await storage.artifacts.put(RUN, "report.md", b"# v2")
    assert await storage.artifacts.get(RUN, "report.md") == b"# v2"


async def test_missing_artifact_is_none(storage: Storage) -> None:
    assert await storage.artifacts.get(RUN, "assessment.json") is None
    assert await storage.artifacts.names(RUN) == []


async def test_artifact_names_are_sorted_and_per_run(storage: Storage) -> None:
    await storage.artifacts.put(RUN, "report.md", b"m")
    await storage.artifacts.put(RUN, "assessment.json", b"{}")
    await storage.artifacts.put(OTHER, "report.html", b"<p>")
    assert await storage.artifacts.names(RUN) == ["assessment.json", "report.md"]
    assert await storage.artifacts.get(OTHER, "report.md") is None


@pytest.mark.parametrize(
    "name", ["../state.json", "a/b.md", "", ".hidden", "report.md\x00", "..", "x" * 200]
)
async def test_unsafe_artifact_names_are_rejected(storage: Storage, name: str) -> None:
    with pytest.raises(StorageKeyError):
        await storage.artifacts.put(RUN, name, b"x")
    with pytest.raises(StorageKeyError):
        await storage.artifacts.get(RUN, name)


@pytest.mark.parametrize("run_id", ["../etc", "a/b", "", "run id", "x" * 65])
async def test_unsafe_run_ids_are_rejected(storage: Storage, run_id: str) -> None:
    with pytest.raises(StorageKeyError):
        await storage.artifacts.get(run_id, "report.md")
    with pytest.raises(StorageKeyError):
        await storage.run_state.load(run_id)


# --- run state --------------------------------------------------------------------------------


def _state(status: str = "queued") -> RunState:
    return RunState(
        run_id=RUN,
        repo_url="https://github.com/org/repo",
        ref=None,
        status=status,  # pyright: ignore[reportArgumentType]
        stages=[
            StageState(
                stage="ingest", status="pending", started_at=None, finished_at=None, error=None
            )
        ],
        metrics_done=[],
        cost_usd=0.0,
    )


async def test_run_state_round_trip_and_update(storage: Storage) -> None:
    assert await storage.run_state.load(RUN) is None
    await storage.run_state.save(_state("queued"))
    await storage.run_state.save(_state("running"))
    loaded = await storage.run_state.load(RUN)
    assert loaded == _state("running")


# --- checkpoints ------------------------------------------------------------------------------


async def test_checkpoints_by_stage_and_metric(storage: Storage) -> None:
    await storage.checkpoints.save(RUN, "facts", b'{"facts": []}')
    await storage.checkpoints.save(RUN, "evaluate/security", b"[]")
    await storage.checkpoints.save(RUN, "evaluate/cicd", b"[]")
    assert await storage.checkpoints.load(RUN, "facts") == b'{"facts": []}'
    assert await storage.checkpoints.keys(RUN) == ["evaluate/cicd", "evaluate/security", "facts"]
    assert await storage.checkpoints.keys(OTHER) == []
    assert await storage.checkpoints.load(RUN, "verify") is None


@pytest.mark.parametrize("key", ["../x", "a/b/c", "Facts", "", "facts/"])
async def test_unsafe_checkpoint_keys_are_rejected(storage: Storage, key: str) -> None:
    with pytest.raises(StorageKeyError):
        await storage.checkpoints.save(RUN, key, b"x")


# --- cache ------------------------------------------------------------------------------------


async def test_cache_round_trip_and_namespaces(storage: Storage) -> None:
    key = "a" * 64
    assert await storage.cache.get("llm", key) is None
    await storage.cache.put("llm", key, b"response")
    assert await storage.cache.get("llm", key) == b"response"
    assert await storage.cache.get("embed", key) is None


async def test_cache_ttl_uses_the_store_clock(storage: Storage, clock: FakeClock) -> None:
    await storage.cache.put("llm", "k1", b"v", ttl_s=60)
    await storage.cache.put("llm", "k2", b"forever")
    clock.now += 59
    assert await storage.cache.get("llm", "k1") == b"v"
    clock.now += 1
    assert await storage.cache.get("llm", "k1") is None
    assert await storage.cache.get("llm", "k2") == b"forever"


@pytest.mark.parametrize(
    ("namespace", "key"), [("LLM", "k"), ("llm", "a/b"), ("llm", ""), ("", "k")]
)
async def test_invalid_cache_keys_are_rejected(storage: Storage, namespace: str, key: str) -> None:
    with pytest.raises(StorageKeyError):
        await storage.cache.put(namespace, key, b"x")


# --- persistence and concurrency ----------------------------------------------------------------


async def test_data_survives_reopening(make_storage: StorageFactory) -> None:
    first = make_storage()
    await first.artifacts.put(RUN, "report.md", b"m")
    await first.run_state.save(_state())
    await first.checkpoints.save(RUN, "facts", b"{}")
    await first.cache.put("llm", "k", b"v")

    second = make_storage()
    assert await second.artifacts.get(RUN, "report.md") == b"m"
    assert await second.run_state.load(RUN) == _state()
    assert await second.checkpoints.keys(RUN) == ["facts"]
    assert await second.cache.get("llm", "k") == b"v"


async def test_concurrent_writes_to_different_keys(storage: Storage) -> None:
    metrics = [f"m{i}" for i in range(10)]
    await asyncio.gather(
        *(storage.checkpoints.save(RUN, f"evaluate/{m}", m.encode()) for m in metrics)
    )
    await asyncio.gather(*(storage.cache.put("metric", m, m.encode()) for m in metrics))
    assert await storage.checkpoints.keys(RUN) == sorted(f"evaluate/{m}" for m in metrics)
    for m in metrics:
        assert await storage.cache.get("metric", m) == m.encode()
