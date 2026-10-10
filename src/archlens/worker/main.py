"""One queued job → one assessment.

The message stays hidden for `VISIBILITY_S` (longer than the job's 20-minute timeout) while the
run is in progress. It is deleted once the run ends, whether it succeeded or failed, because
the run state records the outcome. If the worker dies mid-run, the message comes back: a state
still `running` means an earlier attempt died, so the run resumes from its checkpoints. After
`MAX_ATTEMPTS` deliveries the run is marked failed instead of looping forever.
"""

import logging
from collections.abc import Awaitable, Callable

from archlens.errors import ArchLensError
from archlens.models import Job
from archlens.orchestrator.context import RunOptions
from archlens.storage import Storage

logger = logging.getLogger(__name__)

VISIBILITY_S = 30 * 60
MAX_ATTEMPTS = 2

RunJob = Callable[[Job, RunOptions, bool], Awaitable[object]]  # (job, options, resume)


async def process_next(storage: Storage, run: RunJob) -> str | None:
    """Process one queued job; returns its run_id, or None when the queue is empty. Never raises
    for a failing run (the run state records it); raises for storage errors."""
    message = await storage.queue.receive(VISIBILITY_S)
    if message is None:
        return None
    job = await storage.jobs.get(message.run_id)
    state = await storage.run_state.load(message.run_id)
    if job is None or state is None or state.status in ("done", "failed"):
        logger.warning("run %s: nothing to do (job or state missing, or finished)", message.run_id)
        await storage.queue.delete(message)
        return message.run_id
    if message.dequeue_count > MAX_ATTEMPTS:
        state.status = "failed"
        for stage in state.stages:
            if stage.status == "running":
                stage.status = "failed"
                stage.error = f"worker gave up after {MAX_ATTEMPTS} attempts"
        await storage.run_state.save(state)
        await storage.queue.delete(message)
        return message.run_id
    options = RunOptions(
        target=job.repo_url, ref=job.ref, metrics=tuple(job.metrics) if job.metrics else None
    )
    try:
        await run(job, options, state.status == "running")
    except ArchLensError:
        logger.exception("run %s failed", job.run_id)  # the pipeline marked the state failed
    await storage.queue.delete(message)
    return job.run_id
