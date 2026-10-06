"""`files.*` rules over the snapshot listing (SEC-07, CTR-04, DOC-05)."""

from pydantic import Field

from archlens import __version__
from archlens.globs import glob_match
from archlens.models import Evidence, RuleOutcome, ScanEvidence
from archlens.rubric.context import RuleContext
from archlens.rubric.registry import RuleParams, rule
from archlens.rubric.rules._common import MAX_EVIDENCE

TOOL = "files"


class AnyExistsParams(RuleParams):
    globs: list[str] = Field(min_length=1)


@rule("files.any_exists")
def any_exists(ctx: RuleContext, params: AnyExistsParams) -> RuleOutcome:
    """pass if any listed path matches `globs`, else fail with ScanEvidence of the globs."""
    matched = [f.path for f in ctx.files if glob_match(f.path, params.globs)]
    query = f"any of {params.globs}"
    if not matched:
        return RuleOutcome(
            verdict="fail",
            claim=f"No file matches {', '.join(params.globs)}.",
            evidence=[_scan(query, 0)],
        )
    evidence: list[Evidence] = [_scan(query, len(matched))]
    evidence += [e for p in matched[: MAX_EVIDENCE - 1] if (e := ctx.evidence(p, 1)) is not None]
    return RuleOutcome(verdict="pass", claim=f"Found {', '.join(matched[:3])}.", evidence=evidence)


def _scan(query: str, count: int) -> ScanEvidence:
    return ScanEvidence(tool=TOOL, tool_version=__version__, query=query, result_count=count)
