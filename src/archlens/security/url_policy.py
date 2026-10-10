"""Repository URL policy for the hosted API (SECURITY.md §7).

Only `https://<allowed host>/<owner>/<repo>[.git]`: no userinfo, no port other than the default,
no IP literal, no query or fragment, and owner/repo names from a conservative character set.
The clone layer has its own checks (`archlens.ingest.clone.validate_url`); this one decides what
the API accepts at all, before anything is queued.
"""

import ipaddress
import re
from collections.abc import Sequence
from urllib.parse import urlsplit

from archlens.errors import ArchLensError

_NAME = r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99})"
_PATH = re.compile(rf"^/({_NAME})/({_NAME})/?$")
MAX_URL_CHARS = 300


class UrlPolicyError(ArchLensError):
    """The repository URL is not allowed; the message never echoes credentials."""


def check_repo_url(url: str, allowed_hosts: Sequence[str]) -> str:
    """The normalized `https://host/owner/repo` for an allowed URL; raises UrlPolicyError."""
    if len(url) > MAX_URL_CHARS or any(c.isspace() or ord(c) < 32 for c in url):
        raise UrlPolicyError("repository URL is too long or contains whitespace/control characters")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UrlPolicyError("repository URL is malformed") from exc
    if parts.scheme != "https":
        raise UrlPolicyError("only https:// repository URLs are allowed")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise UrlPolicyError("credentials in repository URLs are not allowed")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise UrlPolicyError("repository URL has no host")
    if _is_ip(host):
        raise UrlPolicyError("IP addresses are not allowed as repository hosts")
    if port is not None and port != 443:
        raise UrlPolicyError("only the default https port is allowed")
    allowed = {h.lower().rstrip(".") for h in allowed_hosts}
    if host not in allowed:
        raise UrlPolicyError(f"host {host!r} is not allowed (allowed: {sorted(allowed)})")
    if parts.query or parts.fragment:
        raise UrlPolicyError("repository URLs take no query or fragment")
    match = _PATH.match(parts.path)
    if match is None:
        raise UrlPolicyError("the path must be /<owner>/<repo> or /<owner>/<repo>.git")
    owner, repo = match.group(1), match.group(2).removesuffix(".git")
    if not repo or owner in (".", "..") or repo in (".", ".."):
        raise UrlPolicyError("the path must be /<owner>/<repo> or /<owner>/<repo>.git")
    return f"https://{host}/{owner}/{repo}"


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True
