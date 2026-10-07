"""Manual labels and the verifier audit (EVALUATION.md §3, §4).

Both are Markdown sheets the user fills in by hand, and parsers that read a filled sheet back
into YAML under `eval/labels/` for the metrics. Sheets are for people: snippets in the audit
sheet are the already-redacted evidence from the report, never fresh repo reads.
"""

import json
import random
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import yaml

from archlens.errors import ArchLensError
from archlens.eval.config import VariantRun
from archlens.eval.metrics import Audit, Label
from archlens.models import AssessmentReport, CodeEvidence, Contract, Finding, Rubric, ScanEvidence

LABELS_DIR = Path("eval/labels")
LABELED_REPO = "primary"  # EVALUATION.md §3: no labels for `cross`
LABEL_VERDICTS = ("pass", "partial", "fail", "not_applicable")
AGREE = {"yes": True, "y": True, "no": False, "n": False}
MAX_SNIPPET_LINES = 40
MAX_EVIDENCE = 3

_CHECK = re.compile(r"^###\s+([A-Z]+-\d{2})\b")
_FINDING = re.compile(r"^<!--\s*finding:\s*(\S+)\s*-->$")
_FIELD = re.compile(r"^-\s*(label|path|agree):\s*(.*?)\s*$")
_FENCE = "~~~~"


class LabelError(ArchLensError):
    """A filled sheet or a labels file can't be read."""


class LabelFile(Contract):
    repo: str
    commit: str
    labels: dict[str, Label]


class AuditFile(Contract):
    results: str  # the eval run id
    agree: dict[str, bool]  # "<run name>/<finding id>" → the auditor agrees


def labels_path(repo_id: str) -> Path:
    return LABELS_DIR / f"{repo_id}.yaml"


def label_sheet_path(repo_id: str) -> Path:
    return LABELS_DIR / f"{repo_id}.sheet.md"


def audit_paths(run_id: str) -> tuple[Path, Path]:
    """(sheet, parsed YAML) for one eval run's audit."""
    return LABELS_DIR / f"audit-{run_id}.md", LABELS_DIR / f"audit-{run_id}.yaml"


def read_report(path: Path) -> AssessmentReport:
    """An `AssessmentReport` JSON, or an eval run record (`VariantRun`) holding one. Raises
    LabelError."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "report" in data:
            return VariantRun.model_validate(data).report
        return AssessmentReport.model_validate(data)
    except (OSError, ValueError) as exc:
        raise LabelError(f"{path}: not a readable report: {exc}") from exc


def _one_line(text: str, limit: int = 300) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _locations(finding: Finding) -> list[str]:
    out: list[str] = []
    for item in finding.result.evidence:
        if isinstance(item, CodeEvidence):
            out.append(f"`{item.path}:{item.start_line}-{item.end_line}`")
        else:
            out.append(f"{item.tool} `{_one_line(item.query, 80)}` → {item.result_count}")
    return out[:5]


# --- label sheet -------------------------------------------------------------------------------


def label_sheet(
    repo_id: str,
    commit: str,
    rubrics: Mapping[str, Rubric],
    report: AssessmentReport | None = None,
) -> str:
    """Markdown sheet listing every active check, with the report's current finding when given."""
    findings = (
        {f.result.check_id: f for f in [*report.findings, *report.other_findings]} if report else {}
    )
    out = [
        f"# Label sheet: `{repo_id}` @ `{commit[:12]}`",
        "",
        "Label only checks whose truth is clear on the unmutated repo — about 15, spread over",
        "at least 6 metrics. Set `label:` to pass, partial, fail or not_applicable, and `path:`",
        "to the file that proves it (empty for an absence). Leave `label:` empty to skip a check.",
        f"Then run `archlens eval label-sheet {repo_id} --read`.",
    ]
    if report is not None:
        out += ["", f"Current findings: run `{report.run_id}` on `{report.commit_sha[:12]}`."]
    flags = report.profile.flags if report else None
    for metric, rubric in rubrics.items():
        out += ["", f"## {rubric.title} (`{metric}`)"]
        for check in rubric.checks:
            if check.retired:
                continue
            out += ["", f"### {check.id} — {check.title}", "", f"{check.type}, {check.severity}."]
            if flags is not None and not (
                rubric.applies_when.holds(flags) and check.applies_when.holds(flags)
            ):
                out.append("Does not apply to this repo's profile.")
            finding = findings.get(check.id)
            if finding is not None:
                status = finding.verification.status + (", scored" if finding.scored else "")
                out.append(
                    f"Current: **{finding.result.verdict}** ({status}) — "
                    f"{_one_line(finding.result.claim)}"
                )
                if where := _locations(finding):
                    out.append("Evidence: " + ", ".join(where))
            elif report is not None:
                out.append("Current: no finding.")
            out += ["", "- label:", "- path:"]
    return "\n".join(out) + "\n"


def _blocks(text: str, heading: re.Pattern[str]) -> list[tuple[str, dict[str, str], int]]:
    """(key, `- name: value` fields, line number) per heading; fenced snippets are skipped."""
    blocks: list[tuple[str, dict[str, str], int]] = []
    fenced = False
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith(_FENCE):
            fenced = not fenced
            continue
        if fenced:
            continue
        if match := heading.match(line):
            blocks.append((match.group(1), {}, number))
        elif blocks and (field := _FIELD.match(line)):
            blocks[-1][1][field.group(1)] = field.group(2).strip("`").strip()
    return blocks


def parse_label_sheet(text: str, known_checks: Iterable[str]) -> dict[str, Label]:
    """Labels from a filled sheet; checks with an empty `label:` are skipped. Raises LabelError
    on an unknown check or verdict."""
    known = set(known_checks)
    labels: dict[str, Label] = {}
    for check_id, fields, line in _blocks(text, _CHECK):
        verdict = fields.get("label", "").lower()
        if not verdict:
            continue
        if check_id not in known:
            raise LabelError(f"line {line}: unknown check {check_id}")
        if verdict not in LABEL_VERDICTS:
            allowed = ", ".join(LABEL_VERDICTS)
            raise LabelError(f"line {line}: {check_id}: label must be one of {allowed}")
        path = fields.get("path", "").removeprefix("./") or None
        labels[check_id] = Label.model_validate({"verdict": verdict, "path": path})
    return labels


def dump_labels(repo_id: str, commit: str, labels: Mapping[str, Label]) -> str:
    data = LabelFile(repo=repo_id, commit=commit, labels=dict(sorted(labels.items())))
    return yaml.safe_dump(data.model_dump(mode="json"), sort_keys=False)


def load_labels(path: Path) -> dict[str, Label]:
    """Labels from `eval/labels/<repo>.yaml`; {} when the file doesn't exist. Raises LabelError."""
    if not path.is_file():
        return {}
    try:
        return LabelFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))).labels
    except (yaml.YAMLError, ValueError) as exc:
        raise LabelError(f"{path}: {exc}") from exc


# --- verifier audit ----------------------------------------------------------------------------


def audit_candidates(records: Sequence[VariantRun]) -> list[tuple[str, Finding]]:
    """Every `verified` LLM finding of the runs, keyed `<run name>/<finding id>`."""
    return [
        (f"{run.name}/{finding.id}", finding)
        for run in records
        for finding in [*run.report.findings, *run.report.other_findings]
        if finding.result.origin == "llm" and finding.verification.status == "verified"
    ]


def audit_sample(records: Sequence[VariantRun], size: int, seed: int) -> list[tuple[str, Finding]]:
    """A seeded random sample of `audit_candidates` (all of them when there are fewer)."""
    candidates = audit_candidates(records)
    chosen = candidates if len(candidates) <= size else random.Random(seed).sample(candidates, size)
    return sorted(chosen, key=lambda c: c[0])


def _evidence_block(item: CodeEvidence | ScanEvidence) -> list[str]:
    if isinstance(item, ScanEvidence):
        return [f"Search: {item.tool} `{_one_line(item.query, 120)}` → {item.result_count} results"]
    lines = item.snippet.splitlines()
    shown = lines[:MAX_SNIPPET_LINES]
    more = [f"… {len(lines) - len(shown)} more lines"] if len(lines) > len(shown) else []
    return [f"`{item.path}:{item.start_line}-{item.end_line}`", _FENCE, *shown, *more, _FENCE]


def audit_sheet(run_id: str, sample: Sequence[tuple[str, Finding]]) -> str:
    """Markdown sheet: per sampled finding its claim, evidence and an empty `agree:`."""
    out = [
        f"# Audit sheet: `{run_id}`",
        "",
        "Every finding below passed verification. Read the claim and the evidence; set `agree:` to",
        "yes if the verdict and claim are right for the cited code, no otherwise.",
        f"Then run `archlens eval audit eval/results/{run_id} --read`.",
    ]
    for number, (key, finding) in enumerate(sample, 1):
        result = finding.result
        out += [
            "",
            f"## {number}. {result.check_id} {result.verdict} — {key.split('/', 1)[0]}",
            f"<!-- finding: {key} -->",
            "",
            f"Claim: {_one_line(result.claim, 600)}",
        ]
        for item in result.evidence[:MAX_EVIDENCE]:
            out += ["", *_evidence_block(item)]
        if len(result.evidence) > MAX_EVIDENCE:
            out += ["", f"… {len(result.evidence) - MAX_EVIDENCE} more evidence items"]
        out += ["", "- agree:"]
    return "\n".join(out) + "\n"


def parse_audit_sheet(text: str) -> dict[str, bool]:
    """`agree` answers from a filled audit sheet; empty answers are skipped. Raises LabelError."""
    answers: dict[str, bool] = {}
    for key, fields, line in _blocks(text, _FINDING):
        answer = fields.get("agree", "").lower()
        if not answer:
            continue
        if answer not in AGREE:
            raise LabelError(f"line {line}: {key}: agree must be yes or no")
        answers[key] = AGREE[answer]
    return answers


def dump_audit(run_id: str, answers: Mapping[str, bool]) -> str:
    data = AuditFile(results=run_id, agree=dict(sorted(answers.items())))
    return yaml.safe_dump(data.model_dump(mode="json"), sort_keys=False)


def load_audits(path: Path) -> list[Audit]:
    """Audits from `eval/labels/audit-<run_id>.yaml`; [] when absent. Raises LabelError."""
    if not path.is_file():
        return []
    try:
        data = AuditFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (yaml.YAMLError, ValueError) as exc:
        raise LabelError(f"{path}: {exc}") from exc
    return [Audit(finding_id=key, agree=agree) for key, agree in data.agree.items()]
