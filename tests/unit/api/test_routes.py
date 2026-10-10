"""Assessment routes with the local storage backend (M4.3 AC): auth, URL policy, quotas, owner
scoping, report and artifacts (404 for anything a run didn't produce)."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from archlens.api import ApiState, create_app
from archlens.api.auth import ApiKeys, key_id
from archlens.models import RunState
from archlens.storage import Storage, open_local_storage

KEY, OTHER_KEY = "key-alpha-0123456789", "key-beta-9876543210"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 10, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Env:
    client: TestClient
    storage: Storage
    clock: Clock


def make_env(tmp_path: Path, *, runs_per_day: int = 3, max_concurrent: int = 1) -> Env:
    storage = open_local_storage(tmp_path / "data")
    clock = Clock()
    state = ApiState(
        storage=storage, keys=ApiKeys((KEY, OTHER_KEY)), allowed_hosts=("github.com",),
        max_concurrent=max_concurrent, runs_per_day=runs_per_day,
        metrics=frozenset({"security", "testing"}), clock=clock,
    )  # fmt: skip
    return Env(TestClient(create_app(state)), storage, clock)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return make_env(tmp_path)


def post(
    env: Env, key: str | None = KEY, body: dict[str, object] | None = None, **fields: object
) -> tuple[int, dict[str, object]]:
    payload = {"repo_url": "https://github.com/o/r", **(body or {}), **fields}
    headers = {"X-API-Key": key} if key is not None else {}
    response = env.client.post("/assessments", json=payload, headers=headers)
    return response.status_code, response.json()


def get(env: Env, path: str, key: str = KEY):
    return env.client.get(path, headers={"X-API-Key": key})


async def finish(env: Env, run_id: str) -> None:
    state = await env.storage.run_state.load(run_id)
    assert state is not None
    state.status = "done"
    await env.storage.run_state.save(state)


# --- auth ---------------------------------------------------------------------------------------


def test_healthz_is_the_only_unauthenticated_route(env: Env) -> None:
    assert env.client.get("/healthz").status_code == 200
    assert post(env, key=None)[0] == 401
    assert post(env, key="wrong")[0] == 401
    assert env.client.get("/assessments/01J0000000000000000000000").status_code == 401


def test_no_configured_keys_means_no_access(tmp_path: Path) -> None:
    storage = open_local_storage(tmp_path / "data")
    state = ApiState(storage, ApiKeys(()), ("github.com",), 1, 3, frozenset(), Clock())
    client = TestClient(create_app(state))
    assert client.post("/assessments", json={"repo_url": "https://github.com/o/r"},
                       headers={"X-API-Key": ""}).status_code == 401  # fmt: skip


# --- create -------------------------------------------------------------------------------------


async def test_create_queues_a_job(env: Env) -> None:
    code, body = post(env, repo_url="https://GitHub.com/o/r.git", metrics=["testing", "security"])
    assert code == 202
    run_id = str(body["run_id"])
    job = await env.storage.jobs.get(run_id)
    assert job is not None and job.key_id == key_id(KEY) and KEY not in job.model_dump_json()
    assert job.repo_url == "https://github.com/o/r" and job.metrics == ["security", "testing"]
    state = await env.storage.run_state.load(run_id)
    assert state is not None and state.status == "queued"
    assert {s.status for s in state.stages} == {"pending"}
    message = await env.storage.queue.receive(60)
    assert message is not None and message.run_id == run_id


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"repo_url": "http://github.com/o/r"}, "only https"),
        ({"repo_url": "https://user:pw@github.com/o/r"}, "credentials"),
        ({"repo_url": "https://10.0.0.1/o/r"}, "IP addresses"),
        ({"repo_url": "https://gitlab.com/o/r"}, "not allowed"),
        ({"ref": "--upload-pack=evil"}, "invalid ref"),
        ({"metrics": ["security", "astrology"]}, "unknown metrics"),
    ],
)
async def test_bad_requests_are_400_and_queue_nothing(
    env: Env, body: dict[str, object], message: str
) -> None:
    code, detail = post(env, body=body)
    assert code == 400 and message in str(detail["detail"])
    assert await env.storage.queue.receive(60) is None


async def test_one_run_at_a_time_per_key(env: Env) -> None:
    code, first = post(env)
    assert code == 202
    code, body = post(env)
    assert code == 429 and "still in progress" in str(body["detail"])
    assert post(env, key=OTHER_KEY)[0] == 202  # another key has its own quota
    await finish(env, str(first["run_id"]))
    assert post(env)[0] == 202


async def test_daily_quota(env: Env) -> None:
    for _ in range(3):
        code, body = post(env)
        assert code == 202
        await finish(env, str(body["run_id"]))
    code, body = post(env)
    assert code == 429 and "daily quota of 3" in str(body["detail"])
    env.clock.now += timedelta(hours=25)
    assert post(env)[0] == 202


# --- read ---------------------------------------------------------------------------------------


def test_status_is_scoped_to_the_owner(env: Env) -> None:
    run_id = str(post(env)[1]["run_id"])
    response = get(env, f"/assessments/{run_id}")
    assert response.status_code == 200
    assert RunState.model_validate(response.json()).status == "queued"
    assert get(env, f"/assessments/{run_id}", key=OTHER_KEY).status_code == 404
    assert get(env, "/assessments/01J0000000000000000000000").status_code == 404
    assert get(env, "/assessments/not..valid").status_code == 404


async def test_report_and_artifacts(env: Env) -> None:
    run_id = str(post(env)[1]["run_id"])
    base = f"/assessments/{run_id}"
    assert get(env, f"{base}/report").status_code == 404  # not produced yet
    for name in ("report.md", "report.html", "assessment.json", "state.json", "secrets.txt"):
        assert get(env, f"{base}/artifacts/{name}").status_code == 404, name
    await env.storage.artifacts.put(run_id, "assessment.json", b'{"run_id": "x"}')
    await env.storage.artifacts.put(run_id, "report.html", b"<h1>r</h1>")
    report = get(env, f"{base}/report")
    assert report.status_code == 200 and report.headers["content-type"] == "application/json"
    html = get(env, f"{base}/artifacts/report.html")
    assert html.status_code == 200 and html.text == "<h1>r</h1>"
    assert "default-src 'none'" in html.headers["content-security-policy"]
    assert html.headers["x-content-type-options"] == "nosniff"
    assert get(env, f"{base}/artifacts/report.md").status_code == 404  # still not produced
    assert get(env, f"{base}/artifacts/report.html", key=OTHER_KEY).status_code == 404
    assert get(env, f"{base}/artifacts/..%2Fstate.json").status_code == 404
