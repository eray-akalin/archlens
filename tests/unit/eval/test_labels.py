"""Label and audit sheets (EVALUATION.md §3, §4): generation, filled-sheet parsing, round trips."""

import re
from pathlib import Path

import pytest

from archlens.eval.config import VariantRun
from archlens.eval.labels import (
    LabelError,
    audit_candidates,
    audit_sample,
    audit_sheet,
    dump_audit,
    dump_labels,
    label_sheet,
    load_audits,
    load_labels,
    parse_audit_sheet,
    parse_label_sheet,
    read_report,
)
from archlens.eval.metrics import Audit, Label
from archlens.evidence import snippet_sha256
from archlens.models import CodeEvidence, ScanEvidence
from archlens.rubric import load_rubrics
from tests.unit.eval.helpers import code, finding, run

REPO = Path(__file__).parents[3]
RUBRICS = load_rubrics(REPO / "rubrics")
CHECKS = {c.id for r in RUBRICS.values() for c in r.checks if not c.retired}


def fill(sheet: str, answers: dict[str, tuple[str, str]]) -> str:
    """Fill `- label:` / `- path:` under the given check headings."""
    out: list[str] = []
    current = ""
    for line in sheet.splitlines():
        if match := re.match(r"^### ([A-Z]+-\d{2})", line):
            current = match.group(1)
        if current in answers and line == "- label:":
            line = f"- label: {answers[current][0]}"
        if current in answers and line == "- path:":
            line = f"- path: {answers[current][1]}"
        out.append(line)
    return "\n".join(out) + "\n"


# --- label sheet -------------------------------------------------------------------------------


def test_sheet_lists_every_active_check_unlabeled() -> None:
    sheet = label_sheet("primary", "f" * 40, RUBRICS)
    assert sheet.startswith("# Label sheet: `primary` @ `ffffffffffff`")
    assert set(re.findall(r"^### ([A-Z]+-\d{2})", sheet, re.M)) == CHECKS
    assert sheet.count("- label:\n") == len(CHECKS)
    assert "Current:" not in sheet  # no report given
    assert parse_label_sheet(sheet, CHECKS) == {}


def test_sheet_shows_current_findings() -> None:
    report = run(
        "base",
        [
            finding("SEC-05", "fail", evidence=[code("app/items.py", 3, 9)], claim="f-string\nSQL"),
            finding("DOC-01", "pass", "rejected"),
        ],
    ).report
    sheet = label_sheet("primary", "f" * 40, RUBRICS, report)
    assert "Current: **fail** (verified, scored) — f-string SQL" in sheet
    assert "Evidence: `app/items.py:3-9`" in sheet
    assert "Current: **pass** (rejected) — c" in sheet
    assert "Current: no finding." in sheet


def test_filled_sheet_round_trip(tmp_path: Path) -> None:
    sheet = label_sheet("primary", "f" * 40, RUBRICS)
    filled = fill(
        sheet,
        {
            "SEC-01": ("pass", ""),
            "CTR-01": ("`fail`", "./backend/Dockerfile"),
            "DOC-01": ("Partial", "README.md"),
        },
    )
    labels = parse_label_sheet(filled, CHECKS)
    assert labels == {
        "SEC-01": Label(verdict="pass", path=None),
        "CTR-01": Label(verdict="fail", path="backend/Dockerfile"),
        "DOC-01": Label(verdict="partial", path="README.md"),
    }
    path = tmp_path / "primary.yaml"
    path.write_text(dump_labels("primary", "f" * 40, labels))
    assert load_labels(path) == labels
    assert load_labels(tmp_path / "missing.yaml") == {}


@pytest.mark.parametrize(
    ("answers", "message"),
    [
        ({"SEC-01": ("maybe", "")}, "label must be one of"),
        ({"SEC-01": ("unknown", "")}, "label must be one of"),
    ],
)
def test_bad_labels_are_rejected(answers: dict[str, tuple[str, str]], message: str) -> None:
    filled = fill(label_sheet("primary", "f" * 40, RUBRICS), answers)
    with pytest.raises(LabelError, match=message):
        parse_label_sheet(filled, CHECKS)


def test_unknown_check_and_bad_file(tmp_path: Path) -> None:
    with pytest.raises(LabelError, match="line 1: unknown check ZZZ-01"):
        parse_label_sheet("### ZZZ-01 — nope\n- label: pass\n", CHECKS)
    bad = tmp_path / "primary.yaml"
    bad.write_text("repo: primary\nlabels: {SEC-01: {verdict: maybe}}\n")
    with pytest.raises(LabelError, match=r"primary\.yaml"):
        load_labels(bad)


def test_read_report_accepts_reports_and_run_records(tmp_path: Path) -> None:
    record = run("base", [finding("SEC-05", "fail")])
    (tmp_path / "run.json").write_text(record.model_dump_json())
    (tmp_path / "report.json").write_text(record.report.model_dump_json())
    assert read_report(tmp_path / "run.json") == read_report(tmp_path / "report.json")
    (tmp_path / "junk.json").write_text("{}")
    with pytest.raises(LabelError, match="not a readable report"):
        read_report(tmp_path / "junk.json")


# --- audit -------------------------------------------------------------------------------------


def records() -> list[VariantRun]:
    return [
        run(
            "base",
            [
                finding("SEC-05", "fail", evidence=[code("a.py", 1, 2)]),
                finding("SEC-01", "pass", origin="deterministic"),  # not the verifier's work
                finding("AUTH-01", "fail", "rejected"),
            ],
        ),
        run(
            "V1",
            [
                finding("AUTH-01", "fail", evidence=[code("u.py", 5, 5)]),
                finding("DOC-01", "not_applicable"),  # verified, not scored: still audited
            ],
        ),
    ]


def test_audit_candidates_are_verified_llm_findings() -> None:
    keys = [key for key, _ in audit_candidates(records())]
    assert keys == ["primary.base.0/SEC-05@x", "primary.V1.0/AUTH-01@x", "primary.V1.0/DOC-01@x"]


def test_audit_sample_is_seeded_and_bounded() -> None:
    assert len(audit_sample(records(), 30, seed=0)) == 3
    two = audit_sample(records(), 2, seed=5)
    assert len(two) == 2 and two == audit_sample(records(), 2, seed=5)


def test_audit_sheet_round_trip(tmp_path: Path) -> None:
    text = "\n".join(f"line {i}" for i in range(59)) + "\n- agree: yes"
    snippet = CodeEvidence(
        path="a.py", start_line=1, end_line=60, snippet=text, snippet_sha256=snippet_sha256(text)
    )
    scan = ScanEvidence(tool="search", tool_version="1", query="AuthMiddleware", result_count=0)
    sample = [
        ("primary.V1.0/AUTH-01@x", finding("AUTH-01", "fail", evidence=[scan])),
        ("primary.base.0/SEC-05@x", finding("SEC-05", "fail", evidence=[snippet])),
    ]
    sheet = audit_sheet("full-1", sample)
    assert "## 1. AUTH-01 fail — primary.V1.0" in sheet
    assert "Search: search `AuthMiddleware` → 0 results" in sheet
    assert "… 20 more lines" in sheet
    assert parse_audit_sheet(sheet) == {}
    filled = sheet.replace("- agree:\n", "- agree: yes\n", 1).replace(
        "- agree:\n", "- agree: No\n", 1
    )
    answers = parse_audit_sheet(filled)
    assert answers == {"primary.V1.0/AUTH-01@x": True, "primary.base.0/SEC-05@x": False}
    path = tmp_path / "audit-full-1.yaml"
    path.write_text(dump_audit("full-1", answers))
    assert load_audits(path) == [
        Audit("primary.V1.0/AUTH-01@x", True),
        Audit("primary.base.0/SEC-05@x", False),
    ]
    with pytest.raises(LabelError, match="agree must be yes or no"):
        parse_audit_sheet(sheet.replace("- agree:\n", "- agree: sure\n", 1))
