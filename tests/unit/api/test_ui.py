"""The web UI (M4.7): a static page and two assets, served without a key, under a CSP that allows
only this origin's script."""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from archlens.api import ApiState, create_app
from archlens.api.auth import ApiKeys
from archlens.api.ui import ASSETS, STATIC
from archlens.storage import open_local_storage


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    state = ApiState(
        storage=open_local_storage(tmp_path / "data"), keys=ApiKeys(("key-0123456789",)),
        allowed_hosts=("github.com",), max_concurrent=1, runs_per_day=3,
        metrics=frozenset({"security"}), clock=lambda: datetime.now(UTC),
    )  # fmt: skip
    return TestClient(create_app(state))


def test_page_is_served_without_a_key_under_a_strict_csp(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "script-src 'self';" in csp
    assert "connect-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("name", sorted(ASSETS))
def test_assets(client: TestClient, name: str) -> None:
    response = client.get(f"/ui/{name}")
    assert response.status_code == 200
    assert response.headers["content-type"] == ASSETS[name]
    assert response.content == (STATIC / name).read_bytes()


@pytest.mark.parametrize("name", ["index.html", "missing.js", "..%2Fui.py", "%2E%2E%2Fapp.py"])
def test_anything_else_under_ui_is_404(client: TestClient, name: str) -> None:
    assert client.get(f"/ui/{name}").status_code == 404


def test_page_loads_only_its_own_assets() -> None:
    """No inline script (the CSP would block it anyway) and nothing from another origin."""
    html = (STATIC / "index.html").read_text()
    scripts = re.findall(r"<script\b[^>]*>", html)
    assert scripts and all('src="/ui/' in tag for tag in scripts)
    assert not re.search(r"""(src|href)=["'](https?:)?//""", html)
    assert set(re.findall(r'(?:src|href)="/ui/([^"]+)"', html)) == set(ASSETS)


def test_report_frame_is_sandboxed_without_scripts() -> None:
    frame = re.search(r"<iframe\b[^>]*>", (STATIC / "index.html").read_text())
    assert frame is not None and re.search(r"\bsandbox\b(?!=)", frame.group(0))


def test_ui_needs_a_configured_app() -> None:
    assert TestClient(create_app()).get("/").status_code == 404  # healthz-only mode
