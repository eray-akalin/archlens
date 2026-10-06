"""Evidence attached to facts, check results and findings (DATA_MODEL.md §2)."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from archlens.models.base import Contract

# end_line - start_line must stay below this (a citation is a focused excerpt, not a file).
MAX_EVIDENCE_SPAN = 60


class CodeEvidence(Contract):
    """A line range of a snapshot file. `snippet` and its hash are filled by the system."""

    kind: Literal["code"] = "code"
    path: str
    start_line: Annotated[int, Field(ge=1)]
    end_line: int
    snippet: str
    snippet_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def _check_span(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be >= start_line")
        if self.end_line - self.start_line >= MAX_EVIDENCE_SPAN:
            raise ValueError(f"evidence span must be < {MAX_EVIDENCE_SPAN} lines")
        return self


class ScanEvidence(Contract):
    """A tool or probe result; `result_count=0` records "nothing found"."""

    kind: Literal["scan"] = "scan"
    tool: str
    tool_version: str
    query: str
    result_count: Annotated[int, Field(ge=0)]


Evidence = Annotated[CodeEvidence | ScanEvidence, Field(discriminator="kind")]
