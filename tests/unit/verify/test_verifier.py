"""Verification pipeline on tiny_service with FakeLLM: one test per ARCHITECTURE.md §2.5 row,
entailment batching, the skeptic and budget handling."""

from pathlib import Path

import pytest

from archlens.config import SessionLimits
from archlens.errors import BudgetExceeded, InvalidModelOutput
from archlens.llm.fake import FakeLLM, Scripted
from archlens.llm.types import ToolCall
from archlens.models import (
    CheckResult,
    CheckSpec,
    CheckType,
    Citation,
    CodeEvidence,
    EntailmentBatchOutput,
    EntailmentItem,
    Finding,
    Rubric,
    ScanEvidence,
    SkepticOutput,
    Verdict,
)
from archlens.verify.entailment import EntailmentPrompts, Supports
from archlens.verify.pipeline import Verifier, VerifierOptions
from archlens.verify.skeptic import SkepticPrompts
from tests.unit.conftest import TinyEnv

REPO = Path(__file__).parents[3]
USERS = "app/api/users.py"
MAIN = "app/main.py"
SESSION = "demo:evaluate:0"
SHA = "c" * 40


def spec(check_id: str, severity: str = "high", **fields: object) -> CheckSpec:
    base: dict[str, object] = {
        "id": check_id, "title": f"title {check_id}", "type": "llm", "severity": severity,
        "rationale": "r", "remediation": "r", "guidance": "- pass: ...\n- fail: ...",
    }  # fmt: skip
    return CheckSpec.model_validate(base | fields)


def regex(pattern: str) -> dict[str, str]:
    return {"kind": "regex", "pattern": pattern, "path_glob": "**/*.py"}


DEMO = Rubric(
    metric="demo",
    title="Demo",
    version="1.0.0",
    weight=1.0,
    description="d",
    checks=[
        spec("DM-01"),
        spec("DM-02", "critical"),
        spec("DM-03", evidence_policy="absence_allowed", absence_probes=[regex("(?i)traceparent")]),
        spec("DM-04", evidence_policy="absence_allowed", absence_probes=[regex("allow_origins")]),
        spec(
            "DM-05", na_allowed=True, absence_probes=[{"kind": "path_glob", "pattern": "**/*.tf"}]
        ),
        spec("DM-06", na_allowed=True, absence_probes=[{"kind": "symbol", "pattern": "search_*"}]),
        spec(
            "DM-07",
            "low",
            type="deterministic",
            rule="files.any_exists",
            guidance=None,
            params={"globs": ["README*"]},
        ),
        *(spec(f"DM-{i}") for i in range(10, 17)),
    ],
)


def result(
    check_id: str,
    verdict: Verdict,
    *cites: tuple[str, int, int],
    origin: CheckType = "llm",
    session: str = SESSION,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        metric="demo",
        origin=origin,
        verdict=verdict,
        claim=f"{check_id} claim",
        citations=[Citation(path=p, start_line=a, end_line=b) for p, a, b in cites],
        confidence="high",
        session_id=session,
        rubric_version="1.0.0",
    )


def verifier(
    env: TinyEnv, llm: FakeLLM, options: VerifierOptions | None = None, *, judge: bool = False
) -> Verifier:
    return Verifier(
        llm,
        env.tools,
        {"demo": DEMO},
        SHA,
        entailment_prompts=EntailmentPrompts.load(REPO / "prompts"),
        skeptic_prompts=SkepticPrompts.load(REPO / "prompts"),
        skeptic_limits=SessionLimits(max_tool_calls=6, max_context_tokens=20_000),
        options=options,
        absence_prompts=EntailmentPrompts.load_absence(REPO / "prompts") if judge else None,
    )


def entail(*answers: tuple[str, Supports]) -> EntailmentBatchOutput:
    return EntailmentBatchOutput(
        items=[EntailmentItem(ref=r, supports=s, rationale=f"{r} {s}") for r, s in answers]
    )


def seen(env: TinyEnv, path: str, start: int, end: int, session: str = SESSION) -> None:
    env.tools.ledger.add(session, path, start, end)


async def verify_one(env: TinyEnv, llm: FakeLLM, item: CheckResult) -> Finding:
    (finding,) = await verifier(env, llm).verify([item])
    return finding


# --- one test per row of the ARCHITECTURE §2.5 table -------------------------------------------


async def test_deterministic_is_verified_by_construction(tiny_env: TinyEnv) -> None:
    item = result("DM-07", "fail", origin="deterministic")
    finding = await verify_one(tiny_env, FakeLLM(), item)
    assert finding.verification.status == "verified" and finding.verification.steps == []
    assert finding.scored and finding.id == f"DM-07@{SHA[:12]}"


async def test_llm_unknown_skips_verification(tiny_env: TinyEnv) -> None:
    finding = await verify_one(tiny_env, FakeLLM(), result("DM-01", "unknown"))
    assert (finding.verification.status, finding.scored) == ("unverified", False)


async def test_cited_mechanical_and_entailment_pass(tiny_env: TinyEnv) -> None:
    seen(tiny_env, USERS, 30, 36)
    llm = FakeLLM({("verifier", "verifier.entailment", "DM-01"): [entail(("DM-01", "yes"))]})
    finding = await verify_one(tiny_env, llm, result("DM-01", "fail", (USERS, 34, 34)))
    assert finding.verification.status == "verified" and finding.scored
    assert [s.step for s in finding.verification.steps] == ["mechanical", "entailment"]
    (evidence,) = finding.result.evidence
    assert isinstance(evidence, CodeEvidence) and 'text(f"SELECT' in evidence.snippet
    entailment = finding.verification.steps[1]
    assert entailment.llm_call_id == llm.records[-1].id
    prompt = str(llm.calls[0].messages[1]["content"])
    boundary = tiny_env.tools.boundary
    assert (
        'source="claim DM-01">' in prompt
        and f'<repo_data boundary="{boundary}" source="{USERS}">' in prompt
    )
    assert "  34│ " in prompt


async def test_cited_mechanical_failure_is_rejected_without_an_llm_call(tiny_env: TinyEnv) -> None:
    llm = FakeLLM()
    unseen = await verify_one(tiny_env, llm, result("DM-01", "fail", (USERS, 34, 34)))
    invented = await verify_one(tiny_env, llm, result("DM-01", "fail", (USERS, 900, 901)))
    for finding in (unseen, invented):
        assert finding.verification.status == "rejected" and not finding.scored
        assert [s.step for s in finding.verification.steps] == ["mechanical"]
    assert "never shown" in unseen.verification.steps[0].detail
    assert "outside the file" in invented.verification.steps[0].detail
    assert llm.calls == []


@pytest.mark.parametrize(
    ("supports", "status"), [("no", "rejected"), ("insufficient", "unverified")]
)
async def test_cited_entailment_no_or_insufficient(
    tiny_env: TinyEnv, supports: Supports, status: str
) -> None:
    seen(tiny_env, USERS, 34, 34)
    llm = FakeLLM({("verifier", "verifier.entailment", "DM-01"): [entail(("DM-01", supports))]})
    finding = await verify_one(tiny_env, llm, result("DM-01", "pass", (USERS, 34, 34)))
    assert finding.verification.status == status and not finding.scored


async def test_missing_entailment_item_is_unverified(tiny_env: TinyEnv) -> None:
    seen(tiny_env, USERS, 34, 34)
    llm = FakeLLM({("verifier", "verifier.entailment", "DM-01"): [entail(("DM-99", "yes"))]})
    finding = await verify_one(tiny_env, llm, result("DM-01", "pass", (USERS, 34, 34)))
    assert finding.verification.status == "unverified"
    assert "no item for this ref" in finding.verification.steps[-1].detail


async def test_absence_with_no_probe_hits_is_verified(tiny_env: TinyEnv) -> None:
    finding = await verify_one(tiny_env, FakeLLM(), result("DM-03", "fail"))
    assert finding.verification.status == "verified" and finding.scored
    (record,) = finding.result.evidence
    assert isinstance(record, ScanEvidence)
    assert (record.tool, record.result_count) == ("absence_probe", 0)


async def test_absence_probe_hit_rejects_with_evidence(tiny_env: TinyEnv) -> None:
    finding = await verify_one(tiny_env, FakeLLM(), result("DM-04", "partial"))
    assert finding.verification.status == "rejected" and not finding.scored
    (hit,) = finding.result.evidence
    assert isinstance(hit, CodeEvidence) and (hit.path, hit.start_line) == (MAIN, 11)
    assert "allow_origins" in hit.snippet


async def test_evidence_less_na_is_checked_by_absence(tiny_env: TinyEnv) -> None:
    absent = await verify_one(tiny_env, FakeLLM(), result("DM-05", "not_applicable"))
    assert absent.verification.status == "verified" and not absent.scored  # NA is never scored
    found = await verify_one(tiny_env, FakeLLM(), result("DM-06", "not_applicable"))
    assert found.verification.status == "rejected"
    hit = found.result.evidence[0]
    assert isinstance(hit, CodeEvidence) and hit.path == USERS and hit.start_line == 32


JUDGE_04 = ("verifier", "verifier.absence", "DM-04")


@pytest.mark.parametrize(
    ("supports", "status"),
    [("yes", "verified"), ("no", "rejected"), ("insufficient", "unverified")],
)
async def test_probe_hits_go_to_the_absence_judge(
    tiny_env: TinyEnv, supports: Supports, status: str
) -> None:
    llm = FakeLLM({JUDGE_04: [entail(("DM-04", supports))]})
    (finding,) = await verifier(tiny_env, llm, judge=True).verify([result("DM-04", "partial")])
    assert finding.verification.status == status
    first, second = finding.verification.steps
    assert (first.step, first.passed) == ("absence", False) and "absence judge" in first.detail
    assert (second.step, second.passed) == ("absence", supports == "yes")
    assert second.detail.startswith(f"judge {supports}:")
    (hit,) = finding.result.evidence  # the hits stay attached either way
    assert isinstance(hit, CodeEvidence) and (hit.path, hit.start_line) == (MAIN, 11)
    assert "Code found by the search" in str(llm.calls[0].messages[-1]["content"])


async def test_mechanical_only_rejects_probe_hits_without_a_judge(tiny_env: TinyEnv) -> None:
    options = VerifierOptions(entailment=False)
    vf = verifier(tiny_env, FakeLLM(), options, judge=True)
    (finding,) = await vf.verify([result("DM-04", "partial")])
    assert finding.verification.status == "rejected"


async def test_evidence_less_other_cases_stay_unverified(tiny_env: TinyEnv) -> None:
    finding = await verify_one(tiny_env, FakeLLM(), result("DM-01", "fail"))  # positive_required
    assert finding.verification.status == "unverified"


async def test_absence_that_cannot_be_confirmed_is_unverified(tiny_env: TinyEnv) -> None:
    tiny_env.tools.index_path = None  # symbol probes need the index
    finding = await verify_one(tiny_env, FakeLLM(), result("DM-06", "not_applicable"))
    assert finding.verification.status == "unverified"
    assert "no symbol index" in finding.verification.steps[0].detail


# --- skeptic -----------------------------------------------------------------------------------

SKEPTIC = ("skeptic", "skeptic.system", "DM-02")
ENTAIL_02 = ("verifier", "verifier.entailment", "DM-02")


async def critical_fail(env: TinyEnv, *skeptic: Scripted) -> Finding:
    seen(env, USERS, 34, 34)
    llm = FakeLLM({ENTAIL_02: [entail(("DM-02", "yes"))], SKEPTIC: list(skeptic)})
    return await verify_one(env, llm, result("DM-02", "fail", (USERS, 34, 34)))


async def test_skeptic_refutation_with_valid_citations_disputes(tiny_env: TinyEnv) -> None:
    read = ToolCall(
        "k1", "read_file", '{"path": "app/api/users.py", "start_line": 32, "end_line": 35}'
    )
    refute = SkepticOutput(
        refuted=True, reason="name is validated upstream",
        citations=[Citation(path=USERS, start_line=33, end_line=33)],
    )  # fmt: skip
    finding = await critical_fail(tiny_env, [read], refute)
    assert finding.verification.status == "disputed" and not finding.scored
    step = finding.verification.steps[-1]
    assert (step.step, step.passed) == ("skeptic", False)
    assert step.detail.startswith(f"refuted with {USERS}:33-33")


async def test_skeptic_refutation_with_unseen_lines_is_ignored(tiny_env: TinyEnv) -> None:
    refute = SkepticOutput(
        refuted=True,
        reason="trust me",
        citations=[Citation(path=USERS, start_line=13, end_line=14)],
    )
    finding = await critical_fail(tiny_env, refute)
    assert finding.verification.status == "verified" and finding.scored
    assert "refutation ignored" in finding.verification.steps[-1].detail


async def test_skeptic_sees_the_cited_code(tiny_env: TinyEnv) -> None:
    keep = SkepticOutput(refuted=False, reason="the f-string reaches the query", citations=[])
    finding = await critical_fail(tiny_env, keep)
    assert finding.verification.status == "verified"
    assert finding.verification.steps[-1].detail.startswith("not refuted")
    ledger = tiny_env.tools.ledger.lines("demo:skeptic:DM-02", USERS)
    assert ledger == {34}  # the cited snippet shown in its prompt


async def test_skeptic_only_for_critical_fails(tiny_env: TinyEnv) -> None:
    seen(tiny_env, USERS, 34, 34)
    llm = FakeLLM({("verifier", "verifier.entailment", "DM-01"): [entail(("DM-01", "yes"))]})
    finding = await verify_one(tiny_env, llm, result("DM-01", "fail", (USERS, 34, 34)))
    assert [s.step for s in finding.verification.steps] == ["mechanical", "entailment"]


# --- batching, options, failures, budget -------------------------------------------------------


async def test_entailment_batches_five_per_call(tiny_env: TinyEnv) -> None:
    seen(tiny_env, USERS, 1, 60)
    ids = [f"DM-{i}" for i in range(10, 17)]
    first, second = ids[:5], ids[5:]
    llm = FakeLLM()
    for batch in (first, second):
        answers: list[tuple[str, Supports]] = [(check_id, "yes") for check_id in batch]
        llm.add(("verifier", "verifier.entailment", *batch), entail(*answers))
    findings = await verifier(tiny_env, llm).verify(
        [result(i, "pass", (USERS, 34, 34)) for i in ids]
    )
    assert [c.tags for c in llm.calls] == [tuple(first), tuple(second)]
    assert {f.verification.status for f in findings} == {"verified"}


async def test_entailment_retries_an_unusable_answer_once(tiny_env: TinyEnv) -> None:
    seen(tiny_env, USERS, 34, 34)
    key = ("verifier", "verifier.entailment", "DM-01")
    llm = FakeLLM({key: [InvalidModelOutput("truncated"), entail(("DM-01", "yes"))]})
    assert (await verify_one(tiny_env, llm, result("DM-01", "pass", (USERS, 34, 34)))).scored
    broken = FakeLLM({key: [InvalidModelOutput("bad"), InvalidModelOutput("bad again")]})
    finding = await verify_one(tiny_env, broken, result("DM-01", "pass", (USERS, 34, 34)))
    assert finding.verification.status == "unverified"
    assert "invalid output" in finding.verification.steps[-1].detail


async def test_mechanical_only_and_no_skeptic(tiny_env: TinyEnv) -> None:
    seen(tiny_env, USERS, 34, 34)
    llm = FakeLLM()
    options = VerifierOptions(entailment=False, skeptic=False)
    (finding,) = await verifier(tiny_env, llm, options).verify(
        [result("DM-02", "fail", (USERS, 34, 34))]
    )
    assert finding.verification.status == "verified"
    assert [s.step for s in finding.verification.steps] == ["mechanical"]
    assert llm.calls == []


async def test_budget_stops_every_remaining_llm_step(tiny_env: TinyEnv) -> None:
    seen(tiny_env, USERS, 1, 60)
    ids = [f"DM-{i}" for i in range(10, 17)]
    llm = FakeLLM(
        {("verifier", "verifier.entailment", *ids[:5]): [BudgetExceeded("budget reached")]}
    )
    items = [
        *(result(i, "pass", (USERS, 34, 34)) for i in ids),
        result("DM-03", "fail"),  # absence path: no LLM, still verified
    ]
    v = verifier(tiny_env, llm)
    findings = await v.verify(items)
    assert v.budget_exhausted and len(llm.calls) == 1  # the second batch never calls the model
    for finding in findings[:7]:
        assert finding.verification.status == "unverified"
        assert finding.verification.steps[-1].detail == "skipped: budget"
    assert findings[7].verification.status == "verified"


async def test_budget_skips_the_skeptic_but_keeps_the_finding(tiny_env: TinyEnv) -> None:
    seen(tiny_env, USERS, 34, 34)
    llm = FakeLLM({ENTAIL_02: [entail(("DM-02", "yes"))], SKEPTIC: [BudgetExceeded("budget")]})
    finding = await verify_one(tiny_env, llm, result("DM-02", "fail", (USERS, 34, 34)))
    assert finding.verification.status == "verified" and finding.scored
    step = finding.verification.steps[-1]
    assert (step.step, step.passed) == ("skeptic", True) and step.detail.startswith(
        "skipped: budget"
    )


async def test_findings_keep_input_order(tiny_env: TinyEnv) -> None:
    items = [
        result("DM-04", "fail"),
        result("DM-07", "pass", origin="deterministic"),
        result("DM-01", "unknown"),
    ]
    findings = await verifier(tiny_env, FakeLLM()).verify(items)
    assert [f.result.check_id for f in findings] == ["DM-04", "DM-07", "DM-01"]
