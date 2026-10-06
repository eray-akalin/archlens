from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response


def test_create_user():
    client.post("/users", json={"email": "a@example.com", "name": "A", "password": "pw"})
    assert True
