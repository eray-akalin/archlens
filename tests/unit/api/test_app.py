"""The API app: `GET /healthz` for the container HEALTHCHECK (in-process client, no socket)."""

from fastapi.testclient import TestClient

from archlens import __version__
from archlens.api import create_app


def test_healthz() -> None:
    response = TestClient(create_app()).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_docs_are_not_served() -> None:
    client = TestClient(create_app())
    assert client.get("/docs").status_code == 404 and client.get("/redoc").status_code == 404
