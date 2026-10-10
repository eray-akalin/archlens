"""Hand-built instances of every top-level contract, nesting as many sub-models as possible."""

from datetime import UTC, datetime

from pydantic import BaseModel

from archlens.models import (
    AbsenceProbe,
    AppliesWhen,
    AssessmentReport,
    AssessmentRequest,
    CheckResult,
    CheckSpec,
    Citation,
    CodeEvidence,
    ConfigFingerprint,
    CostSummary,
    EntailmentBatchOutput,
    EntailmentItem,
    Fact,
    FactSet,
    FileEntry,
    Finding,
    Job,
    LLMCallRecord,
    LLMCheckOutput,
    MetricEvaluationOutput,
    MetricNarrative,
    MetricScore,
    Narrative,
    RepoProfile,
    RepoSnapshot,
    Rubric,
    RunState,
    ScanEvidence,
    SearchRecord,
    SkepticOutput,
    StageState,
    ToolRunRecord,
    Verification,
    VerificationStep,
    finding_id,
)

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
SHA = "0123456789abcdef0123456789abcdef01234567"


def code_evidence() -> CodeEvidence:
    return CodeEvidence(
        path="app/api/users.py",
        start_line=10,
        end_line=14,
        snippet="@router.post('/users')\ndef create_user(body: UserIn): ...",
        snippet_sha256="ab" * 32,
    )


def scan_evidence() -> ScanEvidence:
    return ScanEvidence(tool="gitleaks", tool_version="8.0.0", query="all rules", result_count=0)


def tool_run() -> ToolRunRecord:
    return ToolRunRecord(
        tool="gitleaks",
        version="8.0.0",
        command=["gitleaks", "dir", "<repo>", "--redact"],
        status="ok",
        duration_ms=420,
        fact_count=1,
    )


def fact_set() -> FactSet:
    fact = Fact(
        id="route:0123456789ab",
        kind="route",
        source="ast:routes",
        severity=None,
        attributes={"method": "POST", "path": "/users", "decorators": ["router.post"], "n": 1},
        evidence=[code_evidence()],
    )
    return FactSet(commit_sha=SHA, facts=[fact], tool_runs=[tool_run()])


def repo_snapshot() -> RepoSnapshot:
    entry = FileEntry(
        path="app/api/users.py",
        size=812,
        sha256="cd" * 32,
        language="python",
        loc=40,
        is_binary=False,
        is_generated=False,
        is_vendored=False,
    )
    return RepoSnapshot(
        repo_url="https://github.com/org/repo",
        ref="main",
        commit_sha=SHA,
        files=[entry],
        created_at=NOW,
    )


def repo_profile() -> RepoProfile:
    return RepoProfile(
        languages={"python": 900, "yaml": 40},
        frameworks=["fastapi", "sqlalchemy"],
        package_managers=["uv"],
        ci_systems=["github_actions"],
        test_frameworks=["pytest"],
        flags={"has_http_api": True, "has_database": True, "has_k8s": False},
        entrypoints=["app/main.py"],
    )


def check_result() -> CheckResult:
    return CheckResult(
        check_id="SEC-04",
        metric="security",
        origin="llm",
        verdict="pass",
        claim="POST /users validates its body with the Pydantic model UserIn.",
        citations=[Citation(path="app/api/users.py", start_line=10, end_line=14)],
        evidence=[code_evidence()],
        confidence="high",
        search_log=[
            SearchRecord(
                tool="read_file",
                args={"path": "app/api/users.py", "start_line": 1, "end_line": None},
                result_count=1,
                paths=["app/api/users.py"],
            )
        ],
        llm_call_ids=["01J9ZQ3V5Y7K8M2N4P6R8T0VWX"],
        session_id="sess-security-0",
        rubric_version="1.0.0",
        prompt_version="evaluator.metric@1.0.0+deadbeef",
        model="gpt-5-mini",
    )


def verified_finding() -> Finding:
    result = check_result()
    verification = Verification(
        status="verified",
        steps=[
            VerificationStep(step="mechanical", passed=True, detail="1 citation valid"),
            VerificationStep(
                step="entailment",
                passed=True,
                detail="yes",
                llm_call_id="01J9ZQ3V5Y7K8M2N4P6R8T0VWY",
            ),
        ],
    )
    return Finding(
        id=finding_id(result.check_id, SHA), result=result, verification=verification, scored=True
    )


def rejected_finding() -> Finding:
    result = CheckResult(
        check_id="SEC-06",
        metric="security",
        origin="llm",
        verdict="partial",
        claim="No CORS or security-header configuration was found.",
        confidence="medium",
        rubric_version="1.0.0",
    )
    verification = Verification(
        status="rejected",
        steps=[VerificationStep(step="absence", passed=False, detail="probe hit: app/main.py:8")],
    )
    return Finding(
        id=finding_id(result.check_id, SHA), result=result, verification=verification, scored=False
    )


def llm_call_record() -> LLMCallRecord:
    return LLMCallRecord(
        id="01J9ZQ3V5Y7K8M2N4P6R8T0VWX",
        stage="evaluate",
        metric="security",
        model="gpt-5-mini",
        prompt_version="evaluator.metric@1.0.0+deadbeef",
        input_tokens=42_000,
        cached_input_tokens=9_000,
        output_tokens=6_100,
        reasoning_tokens=3_000,
        latency_ms=18_400,
        cost_usd=0.0207,
        cache_hit=False,
        attempt=0,
    )


def narrative() -> Narrative:
    return Narrative(
        executive_summary="Security is solid at the API boundary.",
        per_metric=[MetricNarrative(metric="security", text="Input validation is consistent.")],
        cited_findings=[finding_id("SEC-04", SHA)],
    )


def assessment_report() -> AssessmentReport:
    return AssessmentReport(
        run_id="01J9ZQ3V5Y7K8M2N4P6R8T0VWZ",
        repo_url="https://github.com/org/repo",
        commit_sha=SHA,
        created_at=NOW,
        config=ConfigFingerprint(
            archlens_version="0.1.0",
            rubric_versions={"security": "1.0.0"},
            prompt_versions={"evaluator.metric": "1.0.0+deadbeef"},
            models={"evaluator": "gpt-5-mini", "verifier": "gpt-4.1-mini"},
            tool_versions={"gitleaks": "8.0.0"},
        ),
        profile=repo_profile(),
        metric_scores=[
            MetricScore(
                metric="security",
                status="scored",
                score=7.5,
                coverage=0.8,
                capped_by=[],
                counts={"pass": 4, "partial": 1, "fail": 1, "unknown": 1},
            ),
            MetricScore(
                metric="testing",
                status="insufficient_evidence",
                score=None,
                coverage=0.4,
                capped_by=[],
                counts={"unknown": 3},
            ),
        ],
        overall_score=None,
        findings=[verified_finding()],
        other_findings=[rejected_finding()],
        tool_runs=[tool_run()],
        cost=CostSummary(
            input_tokens=42_000,
            cached_input_tokens=9_000,
            output_tokens=6_100,
            reasoning_tokens=3_000,
            usd=0.0207,
            by_stage={"evaluate": 0.0207},
            by_metric={"security": 0.0207},
        ),
        timings_ms={"ingest": 1200, "facts": 5300},
        narrative=narrative(),
    )


def rubric() -> Rubric:
    return Rubric(
        metric="security",
        title="Security",
        version="1.0.0",
        weight=1.5,
        description="Common high-impact security failures.",
        checks=[
            CheckSpec(
                id="SEC-07",
                title="Automated dependency updates are configured",
                type="deterministic",
                severity="low",
                rule="files.any_exists",
                params={"globs": [".github/dependabot.yml"]},
                rationale="Updates lag without automation.",
                remediation="Enable Dependabot.",
            ),
            CheckSpec(
                id="SEC-06",
                title="CORS and security headers are safely configured",
                type="llm",
                severity="medium",
                applies_when=AppliesWhen(any=["has_http_api"]),
                fact_kinds=["route"],
                evidence_policy="absence_allowed",
                absence_probes=[
                    AbsenceProbe(kind="regex", pattern="(?i)cors", path_glob="**/*.py"),
                    AbsenceProbe(kind="symbol", pattern="*Middleware"),
                ],
                na_allowed=True,
                guidance="- pass: ...\n- fail: ...",
                rationale="Permissive CORS is dangerous.",
                remediation="Allow-list origins.",
            ),
        ],
    )


def run_state() -> RunState:
    return RunState(
        run_id="01J9ZQ3V5Y7K8M2N4P6R8T0VWZ",
        repo_url="https://github.com/org/repo",
        ref=None,
        status="running",
        stages=[
            StageState(stage="ingest", status="done", started_at=NOW, finished_at=NOW, error=None),
            StageState(
                stage="facts", status="running", started_at=NOW, finished_at=None, error=None
            ),
        ],
        metrics_done=[],
        cost_usd=0.0,
    )


SAMPLES: dict[str, BaseModel] = {
    "assessment_report": assessment_report(),
    "repo_snapshot": repo_snapshot(),
    "repo_profile": repo_profile(),
    "fact_set": fact_set(),
    "check_result": check_result(),
    "finding": verified_finding(),
    "run_state": run_state(),
    "assessment_request": AssessmentRequest(
        repo_url="https://github.com/o/r", ref="main", metrics=["security", "testing"]
    ),
    "job": Job(
        run_id="01J0000000000000000000000",
        key_id="0123abcd4567ef89",
        repo_url="https://github.com/o/r",
        ref=None,
        metrics=None,
        created_at=NOW,
    ),
    "llm_call_record": llm_call_record(),
    "rubric": rubric(),
    "metric_evaluation_output": MetricEvaluationOutput(
        results=[
            LLMCheckOutput(
                check_id="SEC-04",
                verdict="pass",
                claim="Bodies are validated by Pydantic models.",
                citations=[Citation(path="app/api/users.py", start_line=10, end_line=14)],
                confidence="high",
            )
        ]
    ),
    "entailment_batch_output": EntailmentBatchOutput(
        items=[EntailmentItem(ref="f1", supports="yes", rationale="UserIn is the body type.")]
    ),
    "skeptic_output": SkepticOutput(refuted=False, reason="No counterexample found.", citations=[]),
    "narrative": narrative(),
}
