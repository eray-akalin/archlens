"""The queue worker (M4.3): one job per message, resume after a crash, give up after retries."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from archlens.errors import ArchLensError
from archlens.models import Job
from archlens.orchestrator.context import RunOptions
from archlens.orchestrator.pipeline import queued_state
from archlens.storage import Storage, open_local_storage
from archlens.worker import MAX_ATTEMPTS, VISIBILITY_S, process_next

RUN = "01J9ZQ3V5Y7K8M2N4P6R8T0VWZ"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


class Recorder:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, RunOptions, bool]] = []
        self.error = error

    async def __call__(self, job: Job, options: RunOptions, resume: bool) -> None:
        self.calls.append((job.run_id, options, resume))
        if self.error is not None:
            raise self.error


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
async def storage(tmp_path: Path, clock: Clock) -> Storage:
    storage = open_local_storage(tmp_path / "data", clock=clock)
    job = Job(
        run_id=RUN, key_id="0123abcd4567ef89", repo_url="https://github.com/o/r", ref="main",
        metrics=["security"], created_at=datetime(2026, 10, 10, tzinfo=UTC),
    )  # fmt: skip
    await storage.jobs.create(job)
    await storage.run_state.save(queued_state(RUN, job.repo_url, job.ref))
    await storage.queue.send(RUN)
    return storage


async def test_empty_queue(tmp_path: Path) -> None:
    assert await process_next(open_local_storage(tmp_path / "x"), Recorder()) is None


async def test_runs_a_queued_job_and_deletes_the_message(storage: Storage, clock: Clock) -> None:
    run = Recorder()
    assert await process_next(storage, run) == RUN
    ((run_id, options, resume),) = run.calls
    assert run_id == RUN and resume is False
    assert (options.target, options.ref, options.metrics) == (
        "https://github.com/o/r", "main", ("security",)
    )  # fmt: skip
    clock.now += VISIBILITY_S + 1
    assert await storage.queue.receive(60) is None  # deleted, not just hidden


async def test_a_running_state_resumes(storage: Storage) -> None:
    state = await storage.run_state.load(RUN)
    assert state is not None
    state.status = "running"  # an earlier attempt died mid-run
    await storage.run_state.save(state)
    run = Recorder()
    await process_next(storage, run)
    assert run.calls[0][2] is True


async def test_finished_runs_are_skipped(storage: Storage) -> None:
    state = await storage.run_state.load(RUN)
    assert state is not None
    state.status = "done"
    await storage.run_state.save(state)
    run = Recorder()
    assert await process_next(storage, run) == RUN and run.calls == []


async def test_a_failing_run_is_not_retried(storage: Storage, clock: Clock) -> None:
    assert await process_next(storage, Recorder(ArchLensError("clone failed"))) == RUN
    clock.now += VISIBILITY_S + 1
    assert await storage.queue.receive(60) is None


async def test_gives_up_after_repeated_crashes(storage: Storage, clock: Clock) -> None:
    for _ in range(MAX_ATTEMPTS):  # the worker dies before deleting: the message comes back
        assert await storage.queue.receive(VISIBILITY_S) is not None
        clock.now += VISIBILITY_S + 1
    state = await storage.run_state.load(RUN)
    assert state is not None
    state.status, state.stages[0].status = "running", "running"
    await storage.run_state.save(state)
    run = Recorder()
    assert await process_next(storage, run) == RUN and run.calls == []
    final = await storage.run_state.load(RUN)
    assert final is not None and final.status == "failed"
    assert final.stages[0].error == f"worker gave up after {MAX_ATTEMPTS} attempts"
