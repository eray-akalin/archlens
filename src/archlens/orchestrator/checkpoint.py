"""Typed checkpoints for `--resume` (ARCHITECTURE.md §4).

Keys: one per stage (`ingest`, `facts`, `profile`, `index`, `verify`, `score`, `report`), one per
metric for evaluate (`evaluate/<metric>`: results plus that metric's seen-lines sessions, which
the verifier needs after a resume), and run bookkeeping (`llm_calls`, `timings`). Payloads are
internal to the orchestrator; the contracts they hold are versioned in `archlens.models`.
"""

from pydantic import BaseModel, JsonValue, TypeAdapter

from archlens.models import CheckResult, Contract, Finding, LLMCallRecord, MetricScore
from archlens.storage import CheckpointStore


class IndexCheckpoint(Contract):
    files: int
    chunks: int
    symbols: int


class MetricCheckpoint(Contract):
    results: list[CheckResult]
    ledger: dict[str, JsonValue]


class FindingsCheckpoint(Contract):
    findings: list[Finding]


class ScoresCheckpoint(Contract):
    metric_scores: list[MetricScore]
    overall: float | None


_RECORDS = TypeAdapter(list[LLMCallRecord])
_TIMINGS = TypeAdapter(dict[str, int])


class Checkpoints:
    def __init__(self, store: CheckpointStore, run_id: str) -> None:
        self.store = store
        self.run_id = run_id

    async def load[M: BaseModel](self, key: str, model: type[M]) -> M | None:
        data = await self.store.load(self.run_id, key)
        return None if data is None else model.model_validate_json(data)

    async def save(self, key: str, value: BaseModel) -> None:
        await self.store.save(self.run_id, key, value.model_dump_json().encode())

    async def keys(self) -> list[str]:
        return await self.store.keys(self.run_id)

    async def load_records(self) -> list[LLMCallRecord]:
        data = await self.store.load(self.run_id, "llm_calls")
        return [] if data is None else _RECORDS.validate_json(data)

    async def save_records(self, records: list[LLMCallRecord]) -> None:
        await self.store.save(self.run_id, "llm_calls", _RECORDS.dump_json(records))

    async def load_timings(self) -> dict[str, int]:
        data = await self.store.load(self.run_id, "timings")
        return {} if data is None else _TIMINGS.validate_json(data)

    async def save_timings(self, timings: dict[str, int]) -> None:
        await self.store.save(self.run_id, "timings", _TIMINGS.dump_json(timings))
