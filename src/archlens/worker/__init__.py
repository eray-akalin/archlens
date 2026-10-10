"""Queue consumer for the hosted API (Container Apps Job, docs/ARCHITECTURE.md §6)."""

from archlens.worker.main import MAX_ATTEMPTS, VISIBILITY_S, RunJob, process_next

__all__ = ["MAX_ATTEMPTS", "VISIBILITY_S", "RunJob", "process_next"]
