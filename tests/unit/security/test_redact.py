"""Secret redaction — docs/SECURITY.md §5."""

import hashlib
import re
import time

import pytest
from hypothesis import given
from hypothesis import strategies as st

from archlens.security.redact import Redactor, SecretSpan, redact, redaction_token, shannon_entropy
from tests.fixture_repos import MaterializedRepo

TOKEN = re.compile(r"«redacted:([a-z0-9-]+):([0-9a-f]{4})»")
PRIVATE_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEA0Z3VS5JJcds3xfn/ygWyF8PbnGy0AHB7MhgHcTz6sE2I2yPB\n"
    "aFDrBz9vFqU4yQ1Q5bJvX0G5rL8YQjF9w0c8W7xY2bGvJ3QeqP1zH5p7m3n4r6t\n"
    "-----END RSA PRIVATE KEY-----"
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:4]


@pytest.mark.parametrize(
    ("text", "secret", "rule"),
    [
        ('key = "AKIAQ3EXAMPLE7KEYXZ2"', "AKIAQ3EXAMPLE7KEYXZ2", "aws-access-key-id"),
        (
            "token: ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
            "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
            "github-token",
        ),
        ("SLACK=xoxb-123456789012-abcdefABCDEF", "xoxb-123456789012-abcdefABCDEF", "slack-token"),
        (
            "k = AIza" + "SyD3x9Q1k2L3m4N5o6P7q8R9s0T1u2V3w4X",
            "AIza" + "SyD3x9Q1k2L3m4N5o6P7q8R9s0T1u2V3w4X",
            "gcp-api-key",
        ),
        (
            "OPENAI_KEY=sk-proj-Ab12Cd34Ef56Gh78Ij90Kl",
            "sk-proj-Ab12Cd34Ef56Gh78Ij90Kl",
            "openai-key",
        ),
        (
            "DefaultEndpointsProtocol=https;AccountName=a;AccountKey=" + "Zm9v" * 11 + "==;",
            "Zm9v" * 11 + "==",
            "azure-storage-key",
        ),
        ('db_password = "q7Lp2Zx9Vb4Nm8Kd3Rt6"', "q7Lp2Zx9Vb4Nm8Kd3Rt6", "generic-secret"),
        (
            "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYzEXAMPLEKEY",
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYzEXAMPLEKEY",
            "generic-secret",
        ),
    ],
)
def test_fallback_patterns(text: str, secret: str, rule: str) -> None:
    out = redact(text)
    assert secret not in out
    match = TOKEN.search(out)
    assert match is not None and match.groups() == (rule, _digest(secret))


@pytest.mark.parametrize(
    "text",
    [
        'password = "hunter2"',  # short
        "token = aaaaaaaaaaaaaaaaaaaaaaaa",  # long but low entropy
        "token_count = 1234567890123456",  # digits only: below the entropy threshold
        "def get_token(self): return self._token",  # no assignment of a value
        "POSTGRES_PASSWORD: app",
    ],
)
def test_ordinary_code_is_left_alone(text: str) -> None:
    assert redact(text) == text


def test_private_key_block_keeps_line_numbers() -> None:
    text = f"line1\n{PRIVATE_KEY}\nline after\n"
    out = redact(text)
    assert "MIIEowIBAAKCAQEA" not in out
    assert out.count("\n") == text.count("\n")
    assert out.splitlines()[5] == "line after"  # same line number as before (1 + 4 key lines)


def test_token_format_and_newline_preservation() -> None:
    assert redaction_token("x", "ab") == f"«redacted:x:{_digest('ab')}»"
    assert redaction_token("x", "a\nb\nc").endswith("»\n\n")


def test_entropy() -> None:
    assert shannon_entropy("") == 0
    assert shannon_entropy("aaaa") == 0
    assert shannon_entropy("abcd") == 2


@given(st.text(max_size=200))
def test_redaction_is_idempotent_on_any_text(text: str) -> None:
    once = redact(text)
    assert redact(once) == once
    assert once.count("\n") == text.count("\n")


@given(
    st.sampled_from(['api_key = "%s"', "AKIA%s", "Bearer sk-%s"]),
    st.text("ABCDEFGHJKLMNPQRSTUVWXYZ234567abcdefgh", min_size=16, max_size=40),
)
def test_redaction_is_idempotent_on_secret_like_text(template: str, filler: str) -> None:
    once = redact(template % filler)
    assert redact(once) == once


# --- spans from gitleaks ------------------------------------------------------------------------

FILE = "line one\nconfig = zz-custom-token-1234 # trailing\nline three\nline four\n"
SPAN = SecretSpan(
    path="cfg.py", start_line=2, start_col=10, end_line=2, end_col=29, rule_id="custom"
)


def test_span_redacts_exact_columns_without_any_pattern() -> None:
    out = Redactor([SPAN]).redact_excerpt("cfg.py", FILE)
    assert (
        out.splitlines()[1]
        == f"config = «redacted:custom:{_digest('zz-custom-token-1234')}» # trailing"
    )
    assert out.count("\n") == FILE.count("\n")


def test_span_in_an_excerpt_uses_file_line_numbers() -> None:
    excerpt = "".join(FILE.splitlines(keepends=True)[1:3])  # lines 2-3
    out = Redactor([SPAN]).redact_excerpt("cfg.py", excerpt, first_line=2)
    assert "zz-custom-token-1234" not in out and out.endswith("line three\n")


def test_spans_of_other_files_or_lines_are_ignored() -> None:
    redactor = Redactor([SPAN])
    assert redactor.redact_excerpt("other.py", FILE) == FILE
    tail = "line three\nline four\n"
    assert redactor.redact_excerpt("cfg.py", tail, first_line=3) == tail


def test_multiline_span_partially_visible() -> None:
    text = "a = 1\n" + PRIVATE_KEY.replace("-----", "=====") + "\nz = 2\n"  # no pattern match
    span = SecretSpan(
        path="k.pem", start_line=2, start_col=1, end_line=5, end_col=29, rule_id="private-key"
    )
    middle = "".join(text.splitlines(keepends=True)[2:4])  # lines 3-4: inside the key
    out = Redactor([span]).redact_excerpt("k.pem", middle, first_line=3)
    assert out.startswith("«redacted:private-key:") and out.count("\n") == middle.count("\n")


def test_overlapping_spans_become_one_redaction() -> None:
    spans = [SPAN, SecretSpan("cfg.py", 2, 15, 2, 35, "other")]
    out = Redactor(spans).redact_excerpt("cfg.py", FILE)
    assert len(TOKEN.findall(out)) == 1 and "tok" not in out.splitlines()[1]


def test_injected_fixture_secrets_never_survive(tiny_service: MaterializedRepo) -> None:
    text = (tiny_service.root / "app" / "settings_local.py").read_text()
    out = Redactor().redact_excerpt("app/settings_local.py", text)
    for value in tiny_service.secret_values:
        assert value not in out


def test_generic_pattern_is_linear_on_long_identifier_runs() -> None:
    """A quadratic pattern took ~0.7 s per 12 KB line (hours for a 2 MB one-line file)."""
    hostile = ("a" * 30_000 + "\n") + ("password" * 4_000 + "\n")
    started = time.monotonic()
    assert redact(hostile) == hostile
    assert time.monotonic() - started < 0.2
