"""Post-processing rules of docs/LLM.md §4 — one test per rule."""

from archlens.evaluate.llm_session import SessionInfo, failed, postprocess
from archlens.models import CheckSpec, Citation, LLMCheckOutput, SearchRecord, Verdict

INFO = SessionInfo(
    metric="security",
    rubric_version="1.0.0",
    session_id="security:evaluate:0",
    prompt_version="evaluator.system@1.0.0+aaaaaaaa",
    model="gpt-5-mini",
    attempt=0,
    search_log=(
        SearchRecord(tool="read_file", args={"path": "a.py"}, result_count=3, paths=["a.py"]),
    ),
    llm_call_ids=("01CALL",),
)
CITE = Citation(path="app/a.py", start_line=3, end_line=5)


def spec(check_id: str, **fields: object) -> CheckSpec:
    base: dict[str, object] = {
        "id": check_id,
        "title": "t",
        "type": "llm",
        "severity": "high",
        "rationale": "r",
        "remediation": "r",
        "guidance": "- pass: ...",
    }
    return CheckSpec.model_validate(base | fields)


PROBES = [{"kind": "path_glob", "pattern": "x"}]
POSITIVE = spec("EX-01")
ABSENCE = spec("EX-02", evidence_policy="absence_allowed", absence_probes=PROBES)
NA_OK = spec("EX-03", na_allowed=True, absence_probes=PROBES)


def out(
    check_id: str, verdict: Verdict, citations: list[Citation] | None = None, claim: str = "c"
) -> LLMCheckOutput:
    cites = [CITE] if citations is None else citations
    return LLMCheckOutput(
        check_id=check_id, verdict=verdict, claim=claim, citations=cites, confidence="high"
    )


def one(output: LLMCheckOutput, check: CheckSpec) -> tuple[str, str | None, str]:
    (result,) = postprocess([output], [check], INFO)
    return result.verdict, result.reason, result.confidence


def test_cited_verdicts_are_kept_with_session_fields() -> None:
    (result,) = postprocess([out("EX-01", "pass")], [POSITIVE], INFO)
    assert (result.verdict, result.reason, result.citations) == ("pass", None, [CITE])
    assert result.origin == "llm" and result.evidence == []  # evidence comes from the verifier
    assert (result.session_id, result.prompt_version, result.model) == (
        INFO.session_id, INFO.prompt_version, "gpt-5-mini"
    )  # fmt: skip
    assert result.search_log == list(INFO.search_log) and result.llm_call_ids == ["01CALL"]


def test_rule1_unknown_ids_dropped_and_duplicates_keep_the_first() -> None:
    outputs = [out("EX-99", "fail"), out("EX-01", "fail"), out("EX-01", "pass")]
    (result,) = postprocess(outputs, [POSITIVE], INFO)
    assert (result.check_id, result.verdict) == ("EX-01", "fail")


def test_rule1_missing_check_becomes_unknown() -> None:
    results = postprocess([out("EX-01", "pass")], [POSITIVE, ABSENCE], INFO)
    assert [(r.check_id, r.verdict, r.reason, r.confidence) for r in results] == [
        ("EX-01", "pass", None, "high"),
        ("EX-02", "unknown", "missing_from_output", "low"),
    ]


def test_rule2_na_without_na_allowed_becomes_unknown() -> None:
    assert one(out("EX-01", "not_applicable"), POSITIVE) == ("unknown", "na_not_allowed", "low")


def test_rule2_na_kept_where_allowed() -> None:
    assert one(out("EX-03", "not_applicable", []), NA_OK)[:2] == ("not_applicable", None)
    assert one(out("EX-03", "not_applicable"), NA_OK)[:2] == ("not_applicable", None)


def test_rule3_evidence_less_pass_becomes_unknown() -> None:
    for check in (POSITIVE, ABSENCE, NA_OK):
        assert one(out(check.id, "pass", []), check) == ("unknown", "no_evidence", "low")


def test_rule3_evidence_less_fail_or_partial_only_on_absence_allowed() -> None:
    assert one(out("EX-02", "fail", []), ABSENCE)[:2] == ("fail", None)
    assert one(out("EX-02", "partial", []), ABSENCE)[:2] == ("partial", None)
    assert one(out("EX-01", "fail", []), POSITIVE)[:2] == ("unknown", "no_evidence")
    assert one(out("EX-03", "partial", []), NA_OK)[:2] == ("unknown", "no_evidence")


def test_model_unknown_keeps_its_claim() -> None:
    verdict, reason, _ = one(
        out("EX-01", "unknown", [], claim="Could not find handlers."), POSITIVE
    )
    assert (verdict, reason) == ("unknown", "model_unknown")


def test_claims_and_citations_are_trimmed_not_rejected() -> None:
    long_claim = "word  " * 200
    cites = [Citation(path="a.py", start_line=i, end_line=i) for i in range(1, 9)]
    (result,) = postprocess([out("EX-01", "fail", cites, claim=long_claim)], [POSITIVE], INFO)
    assert len(result.claim) == 300 and result.claim.endswith("…") and "  " not in result.claim
    assert [c.start_line for c in result.citations] == [1, 2, 3, 4, 5]


def test_failed_session_marks_every_check() -> None:
    results = failed([POSITIVE, ABSENCE], INFO, "budget")
    assert {(r.verdict, r.reason, r.confidence) for r in results} == {("unknown", "budget", "low")}
    assert "budget" in results[0].claim
