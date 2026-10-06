"""IngestLimits enforcement: exceeding a total fails ingest instead of assessing a partial repo."""

from archlens.errors import IngestLimitExceeded
from archlens.models import IngestLimits


def enforce_totals(total_bytes: int, file_count: int, limits: IngestLimits, *, where: str) -> None:
    """Raise IngestLimitExceeded if the repository is too large. `where` names the check point."""
    if file_count > limits.max_files:
        raise IngestLimitExceeded(
            f"{where}: {file_count} files exceeds max_files={limits.max_files}"
        )
    if total_bytes > limits.max_total_bytes:
        raise IngestLimitExceeded(
            f"{where}: {total_bytes} bytes exceeds max_total_bytes={limits.max_total_bytes}"
        )
