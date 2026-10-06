"""Materialize fixture repos into a temp dir, adding what must never be committed.

The committed fixture holds no secret and no vulnerable pin, so this repository stays clean for
gitleaks, push protection and Dependabot alerts; both are injected into the copy instead.
"""

import re
import secrets
import shutil
import string
from dataclasses import dataclass
from pathlib import Path

FIXTURE_REPOS = Path(__file__).parent / "fixtures" / "repos"
TINY_SERVICE = FIXTURE_REPOS / "tiny_service"
ANSWER_KEY = "DEFECTS.md"


@dataclass(frozen=True)
class MaterializedRepo:
    root: Path
    secret_values: tuple[str, ...]  # injected secrets, for asserting redaction later


def materialize_tiny_service(dest: Path) -> MaterializedRepo:
    """Copy tiny_service to `dest` without its answer key and inject DEFECTS.md D01 and D02."""
    shutil.copytree(TINY_SERVICE, dest, ignore=shutil.ignore_patterns(ANSWER_KEY))
    key_id = "AKIA" + "".join(secrets.choice(string.ascii_uppercase + "234567") for _ in range(16))
    secret_key = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(40))
    (dest / "app" / "settings_local.py").write_text(
        f'AWS_ACCESS_KEY_ID = "{key_id}"\nAWS_SECRET_ACCESS_KEY = "{secret_key}"\n'
    )
    (dest / "requirements.txt").write_text("PyYAML==5.3\n")
    return MaterializedRepo(root=dest, secret_values=(key_id, secret_key))


@dataclass(frozen=True)
class AnswerKeyRow:
    id: str  # D01 (defect) or C01 (control)
    check: str
    expected: str  # expected verdict
    location: str
    marker: str


def answer_key() -> list[AnswerKeyRow]:
    """Rows of tiny_service/DEFECTS.md (defects and controls) in file order."""
    rows: list[AnswerKeyRow] = []
    for line in (TINY_SERVICE / ANSWER_KEY).read_text().splitlines():
        if re.match(r"^\| [DC]\d{2} \|", line):
            cells = [c.strip() for c in line.strip("|").split("|")]
            rows.append(AnswerKeyRow(*cells[:5]))
    return rows
