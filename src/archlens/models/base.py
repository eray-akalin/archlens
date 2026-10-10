"""Base classes and version for the contracts between pipeline stages (DATA_MODEL.md)."""

from pydantic import BaseModel, ConfigDict

# Bump on any change to a serialized model (semver), then run `archlens schema export`.
SCHEMA_VERSION = "1.2.0"


class Contract(BaseModel):
    """Immutable contract. Unknown fields are rejected so drift surfaces as an error."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class MutableContract(BaseModel):
    """Contract that is updated in place (run state). Assignments are validated."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)
