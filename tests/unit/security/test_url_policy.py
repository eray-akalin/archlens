"""Repository URL policy (SECURITY.md §7)."""

import pytest

from archlens.security.url_policy import UrlPolicyError, check_repo_url

HOSTS = ["github.com"]


@pytest.mark.parametrize(
    ("url", "normalized"),
    [
        ("https://github.com/fastapi/full-stack-fastapi-template", "https://github.com/fastapi/full-stack-fastapi-template"),
        ("https://github.com/NimblePros/eShopOnWeb.git", "https://github.com/NimblePros/eShopOnWeb"),
        ("https://GitHub.com/o/r/", "https://github.com/o/r"),
        ("https://github.com:443/o/r", "https://github.com/o/r"),
        ("https://github.com./o/r.js", "https://github.com/o/r.js"),
    ],
)  # fmt: skip
def test_allowed(url: str, normalized: str) -> None:
    assert check_repo_url(url, HOSTS) == normalized


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://github.com/o/r", "only https"),
        ("git@github.com:o/r.git", "only https"),
        ("ssh://github.com/o/r", "only https"),
        ("file:///etc/passwd", "only https"),
        ("https://user:token@github.com/o/r", "credentials"),
        ("https://token@github.com/o/r", "credentials"),
        ("https://github.com:8443/o/r", "default https port"),
        ("https://github.com:99999/o/r", "malformed"),
        ("https://140.82.112.3/o/r", "IP addresses"),
        ("https://[::1]/o/r", "IP addresses"),
        ("https://gitlab.com/o/r", "not allowed"),
        ("https://github.com.evil.example/o/r", "not allowed"),
        ("https://evil.example/github.com/o", "not allowed"),
        ("https://github.com/o", "the path must be"),
        ("https://github.com/o/r/tree/main", "the path must be"),
        ("https://github.com/../r", "the path must be"),
        ("https://github.com/o/.git", "the path must be"),
        ("https://github.com/o/r?x=1", "no query"),
        ("https://github.com/o/r#readme", "no query"),
        ("https://github.com/o/r\n", "whitespace"),
        ("https://github.com/" + "a" * 400, "too long"),
    ],
)
def test_rejected(url: str, message: str) -> None:
    with pytest.raises(UrlPolicyError, match=message):
        check_repo_url(url, HOSTS)


def test_credentials_are_never_echoed() -> None:
    with pytest.raises(UrlPolicyError) as info:
        check_repo_url("https://user:s3cret@github.com/o/r", HOSTS)
    assert "s3cret" not in str(info.value)


def test_hosts_are_configurable() -> None:
    assert check_repo_url("https://gitlab.com/o/r", ["github.com", "GitLab.com"]) == (
        "https://gitlab.com/o/r"
    )
