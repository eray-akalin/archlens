# Data model

These Pydantic v2 models are the contracts between pipeline stages. They live in
`src/archlens/models/`. `SCHEMA_VERSION = "1.1.0"` is stored in `AssessmentReport` and bumped on
any change to a serialized model (semver). `archlens schema export` writes JSON Schemas to
`schemas/`; CI fails if they drift from the models.

Conventions: paths are POSIX, relative to repo root; line numbers are 1-based inclusive; all
models are `frozen=True` unless noted; IDs are deterministic (derived from content), never random,
except `run_id` (ULID).

## 1. Shared enums

```python
Verdict = Literal["pass", "partial", "fail", "not_applicable", "unknown"]
Severity = Literal["critical", "high", "medium", "low", "info"]
Confidence = Literal["low", "medium", "high"]
CheckType = Literal["deterministic", "llm"]
EvidencePolicy = Literal["positive_required", "absence_allowed"]
VerificationStatus = Literal["verified", "unverified", "rejected", "disputed"]
MetricStatus = Literal["scored", "insufficient_evidence", "not_applicable"]
```

## 2. Evidence

```python
class CodeEvidence(BaseModel):
    kind: Literal["code"] = "code"
    path: str
    start_line: int          # >= 1
    end_line: int            # >= start_line; end_line - start_line < 60
    snippet: str             # filled by the system from the snapshot, never by a model
    snippet_sha256: str      # sha256 of normalized snippet (see below)

class ScanEvidence(BaseModel):
    kind: Literal["scan"] = "scan"
    tool: str                # "gitleaks", "absence_probe", "ast:imports", ...
    tool_version: str
    query: str               # rule id, probe description, or command summary
    result_count: int        # 0 for "nothing found"

Evidence = Annotated[CodeEvidence | ScanEvidence, Field(discriminator="kind")]
```

Snippet normalization before hashing: split lines, strip trailing whitespace from each, join with
`\n`, encode UTF-8 (invalid bytes replaced). Secret values are redacted *before* hashing and
storage (`SECURITY.md` §5), so hashes are computed on redacted text consistently.

## 3. Ingest and profile

```python
class IngestLimits(BaseModel):
    max_total_bytes: int = 300_000_000
    max_files: int = 30_000
    max_file_bytes: int = 2_000_000      # larger files are listed but not read
    clone_timeout_s: int = 180

class FileEntry(BaseModel):
    path: str
    size: int
    sha256: str                          # "" when the content was not read (too_large)
    language: str | None
    loc: int
    is_binary: bool
    is_generated: bool                   # build output, minified, lockfile, "generated" header
    is_vendored: bool
    symlink_target: str | None = None    # 1.1.0: symlinks are listed (size 0), never followed
    too_large: bool = False              # 1.1.0: size > max_file_bytes; listed, never read
    # property readable: not a symlink, not too_large, not binary

class RepoSnapshot(BaseModel):
    repo_url: str | None
    ref: str | None
    commit_sha: str
    files: list[FileEntry]
    created_at: datetime
    # root path is held in RunContext, not serialized

class RepoProfile(BaseModel):
    languages: dict[str, int]            # language -> LOC, descending
    frameworks: list[str]                # "fastapi", "django", "express", "aspnetcore", ...
    package_managers: list[str]
    ci_systems: list[str]                # "github_actions", "gitlab_ci", "azure_pipelines"
    test_frameworks: list[str]
    flags: dict[str, bool]               # catalogue in RUBRICS.md §2
    entrypoints: list[str]
```

## 4. Facts

```python
class Fact(BaseModel):
    id: str                  # f"{kind}:{sha1(source, kind, canonical attributes, evidence locs)[:12]}"
    kind: str                # registered fact kind, see table below
    source: str              # "gitleaks@8.x.y", "ast:imports", ...
    severity: Severity | None = None
    attributes: dict[str, JsonValue]
    evidence: list[Evidence]

class ToolRunRecord(BaseModel):
    tool: str
    version: str
    command: list[str]       # argv with paths replaced by placeholders
    status: Literal["ok", "error", "timeout", "skipped"]
    duration_ms: int
    fact_count: int
    error: str | None = None

class FactSet(BaseModel):
    commit_sha: str
    facts: list[Fact]
    tool_runs: list[ToolRunRecord]
    def by_kind(self, *kinds: str) -> list[Fact]: ...
```

Initial fact kinds (extend as rules need them; document additions here):

| Kind | Source | Key attributes |
|---|---|---|
| `secret` | gitleaks | `rule_id`, `fingerprint`, `path`, `start_line`, `start_col`, `end_line`, `end_col` (location only, 1-based inclusive — feeds the Redactor; never the value) |
| `vuln_dependency` | osv-scanner | `package`, `version`, `ecosystem`, `ids`, `max_severity`, `severity_source` (`cvss`/`advisory`/`default`), `cvss` |
| `sast_finding` | semgrep | `rule_id`, `category`, `message` |
| `dockerfile` | AST | `path`, `base_images`, `external_bases` (no stage aliases/scratch), `stages`, `stage_aliases`, `final_base`, `user` (final stage), `has_healthcheck` |
| `hadolint_finding` | hadolint | `code`, `level`, `message` |
| `iac_finding` | checkov | `check_id`, `check_name`, `resource`, `framework` |
| `ci_workflow` | AST (YAML) | `path`, `system`, `name`, `triggers`, `permissions`, `jobs`, `job_permissions`, `environments` |
| `ci_step` | AST (YAML) | `path`, `system`, `job`, `uses`, `run` (redacted, ≤ 500 chars), `run_kind` (`test`/`lint`/`build`/`deploy`/`other`; deploy > test > lint > build), `pinned`, `with_keys` |
| `actionlint_finding` | actionlint | `kind`, `message` |
| `function_metrics` | lizard | `name`, `ccn`, `nloc`, `params` |
| `file_metrics` | AST | `path`, `language`, `loc`, `is_test`, `is_generated`, `public_functions`, `documented_functions` (no evidence: whole-file fact) |
| `deploy_config` | AST (YAML) | `path`, `kind` (`compose_service`, `k8s_workload`), `workload` (k8s kind), `name`, `has_limits`, `has_liveness`, `has_readiness`, `has_healthcheck` |
| `import_edge` | AST | `from_module`, `to_module`, `internal`, `language` |
| `manifest` | AST | `path`, `type` (`pyproject`, `requirements`, `package.json`, ...), `has_lockfile` (a lockfile of its ecosystem in its directory or an ancestor — e.g. a workspace root — or every dependency pinned exactly), `dependency_count` |
| `dependency` | manifests | `name`, `version_spec`, `dev`, `manifest` |
| `route` | AST | `method`, `path` (router prefix applied), `handler`, `is_async`, `decorators`, `framework`, `file` |
| `test_file` | AST | `path`, `framework`, `test_count`, `assert_count` |
| `log_call` / `print_call` | AST | `path`, `logger`, `level`, `in_test` / `path`, `call`, `in_test` |
| `doc_file` | filesystem | `path`, `type` (`readme`, `adr`, `architecture`, `openapi`, `contributing`, `changelog`), `loc` |

Scanner severity → `Severity` mapping (in each adapter, unit-tested):
- semgrep: `metadata.impact` when present (`HIGH`→high, `MEDIUM`→medium, `LOW`→low), else
  `extra.severity` (`ERROR`→high, `WARNING`→medium, `INFO`→low).
- osv-scanner: highest CVSS-based severity among the advisory's ids (`CRITICAL`, `HIGH`, ...);
  no score → `medium` with `attributes.severity_source="default"`.
- checkov: reported severity when present; null → `None` (rules treat unknown severity
  explicitly, see RUBRICS §5).
- hadolint: `error`→high, `warning`→medium, `info`/`style`→low.

## 5. Rubric and rules

Defined in `RUBRICS.md` §1 (YAML schema). Python models: `AbsenceProbe`, `CheckSpec`, `Rubric`
mirror that schema exactly; unknown YAML keys are an error (`extra="forbid"`).

```python
@dataclass(frozen=True)
class RuleContext:                   # what a deterministic rule may look at
    facts: FactSet
    profile: RepoProfile
    files: list[FileEntry]           # full snapshot listing (for glob-based rules)
    def read_text(self, path: str, max_bytes: int = 200_000) -> str | None:
        """Jailed, redacted, size-capped read for config files (coverage config, compose, k8s).
        Only listed, readable files (no symlinks, binaries, oversized files); None otherwise."""
    def evidence(self, path: str, start_line: int, end_line: int | None = None) -> CodeEvidence | None:
        """Redacted, hashed snippet via the same SnippetReader the verifier uses."""
    def tool_run(self, tool: str) -> ToolRunRecord | None:
        """Run record of a scanner or extractor, for the missing-data semantics (RUBRICS §5)."""

class RuleOutcome(BaseModel):
    verdict: Verdict
    claim: str
    evidence: list[Evidence]
    reason: str | None = None
```

The registry wraps a `RuleOutcome` into a `CheckResult` (`origin="deterministic"`, `confidence="high"`,
`rubric_version` from the rubric).

## 6. LLM output (what the model returns)

```python
class Citation(BaseModel):
    path: str
    start_line: int
    end_line: int

class LLMCheckOutput(BaseModel):
    check_id: str
    verdict: Verdict
    claim: str                       # one falsifiable sentence, <= 300 chars
    citations: list[Citation]        # 0..5; empty only allowed for absence_allowed checks
    confidence: Confidence

class MetricEvaluationOutput(BaseModel):
    results: list[LLMCheckOutput]    # exactly one per LLM check requested; extras dropped,
                                     # missing ones become verdict="unknown"

class EntailmentItem(BaseModel):
    ref: str                         # finding ref given in the prompt
    supports: Literal["yes", "no", "insufficient"]
    rationale: str                   # <= 200 chars

class EntailmentBatchOutput(BaseModel):
    items: list[EntailmentItem]      # one per finding in the batch (1..5)

class SkepticOutput(BaseModel):
    refuted: bool
    reason: str
    citations: list[Citation]
```

Citations are kept raw on `CheckResult.citations` until the verifier's mechanical step converts
valid ones to `CodeEvidence`; validation of line ranges therefore never happens at parse time.

## 7. Results and findings

```python
class SearchRecord(BaseModel):       # every tool call an evaluator makes
    tool: str                        # "search_code", "read_file", "list_dir", "find_symbol", "get_facts"
    args: dict[str, JsonValue]
    result_count: int
    paths: list[str]                 # up to 20 result paths

class CheckResult(BaseModel):
    check_id: str
    metric: str
    origin: CheckType
    verdict: Verdict
    claim: str
    citations: list[Citation] = []   # raw model citations (LLM origin); unvalidated
    evidence: list[Evidence] = []    # deterministic: from facts; LLM: built by the mechanical step
    confidence: Confidence
    reason: str | None = None        # why unknown / not_applicable
    search_log: list[SearchRecord] = []
    llm_call_ids: list[str] = []
    session_id: str | None = None    # evaluator session; keys the seen-lines ledger
    rubric_version: str
    prompt_version: str | None = None
    model: str | None = None
    attempt: int = 0                 # self-consistency index

class VerificationStep(BaseModel):
    step: Literal["mechanical", "entailment", "absence", "skeptic"]
    passed: bool
    detail: str
    llm_call_id: str | None = None

class Verification(BaseModel):
    status: VerificationStatus
    steps: list[VerificationStep]

class Finding(BaseModel):
    id: str                          # f"{check_id}@{commit_sha[:12]}"
    result: CheckResult
    verification: Verification
    scored: bool                     # True iff status == "verified" and verdict in pass/partial/fail
```

## 8. Scores and report

```python
class MetricScore(BaseModel):
    metric: str
    status: MetricStatus
    score: float | None              # 0.0–10.0, one decimal; None unless status == "scored"
    coverage: float                  # scored weight / applicable weight
    capped_by: list[str]             # check IDs that triggered the critical cap
    counts: dict[Verdict, int]

class LLMCallRecord(BaseModel):
    id: str                          # ULID
    stage: str
    metric: str | None
    model: str
    prompt_version: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    latency_ms: int
    cost_usd: float
    cache_hit: bool                  # exact-cache hit (no provider call)
    attempt: int                     # retry index

class CostSummary(BaseModel):
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    usd: float
    by_stage: dict[str, float]
    by_metric: dict[str, float]

class MetricNarrative(BaseModel):
    metric: str
    text: str

class Narrative(BaseModel):          # LLM-facing: no dicts (strict structured outputs)
    executive_summary: str
    per_metric: list[MetricNarrative]
    cited_findings: list[str]        # must all exist in findings

class ConfigFingerprint(BaseModel):
    archlens_version: str
    rubric_versions: dict[str, str]
    prompt_versions: dict[str, str]
    models: dict[str, str]           # role -> deployment
    tool_versions: dict[str, str]

class AssessmentReport(BaseModel):
    schema_version: str
    run_id: str
    repo_url: str | None
    commit_sha: str
    created_at: datetime
    config: ConfigFingerprint
    profile: RepoProfile
    metric_scores: list[MetricScore]
    overall_score: float | None
    findings: list[Finding]          # scored == True
    other_findings: list[Finding]    # unverified, rejected, disputed, unknown, not_applicable
    tool_runs: list[ToolRunRecord]
    cost: CostSummary
    timings_ms: dict[str, int]       # per stage
    narrative: Narrative | None
```

## 9. Run state (mutable)

```python
class StageState(BaseModel):
    stage: str
    status: Literal["pending", "running", "done", "failed", "skipped"]
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None

class RunState(BaseModel):
    run_id: str
    repo_url: str | None
    ref: str | None
    status: Literal["queued", "running", "done", "failed"]
    stages: list[StageState]
    metrics_done: list[str]
    cost_usd: float
```
