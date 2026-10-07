"""Eval set: repos.yaml validation and pinned, cached, limit-respecting fetches (offline)."""

from pathlib import Path

import pytest
import yaml

from archlens.errors import ConfigError, IngestError, IngestLimitExceeded
from archlens.eval.repos import EvalRepo, checkout_path, fetch, load_repos, marker_path
from archlens.models import IngestLimits
from tests.unit.ingest.test_clone import Origin

REPO = Path(__file__).parents[3]


def write(tmp_path: Path, repos: list[dict[str, object]]) -> Path:
    path = tmp_path / "repos.yaml"
    path.write_text(yaml.safe_dump({"repos": repos}))
    return path


def entry(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "primary", "url": "https://github.com/o/r", "commit": "a" * 40,
        "license": "MIT", "langs": ["python"],
    }  # fmt: skip
    return base | changes


def test_shipped_eval_set() -> None:
    repos = load_repos(REPO / "eval" / "repos.yaml")
    assert [r.id for r in repos] == ["primary", "cross"]
    assert repos[0].url == "https://github.com/fastapi/full-stack-fastapi-template"
    assert all(len(r.commit) == 40 for r in repos)


@pytest.mark.parametrize(
    "repos",
    [
        [entry(commit="abc123")],  # not a full SHA
        [entry(), entry()],  # duplicate id
        [entry(stars=5)],  # unknown key
        [entry(id="Primary")],
    ],
)
def test_invalid_eval_sets(tmp_path: Path, repos: list[dict[str, object]]) -> None:
    with pytest.raises(ConfigError):
        load_repos(write(tmp_path, repos))


@pytest.fixture
def origin(tmp_path: Path) -> Origin:
    return Origin(tmp_path / "origin")


def pinned(origin: Origin, commit: str) -> EvalRepo:
    return EvalRepo(id="primary", url=origin.url, commit=commit, license="MIT", langs=["text"])


def test_fetch_clones_the_pinned_commit_then_reuses_it(origin: Origin, tmp_path: Path) -> None:
    repo = pinned(origin, origin.first)
    cache = tmp_path / "cache"
    first = fetch(repo, cache, protocol="file")
    assert not first.cached and first.path == checkout_path(repo, cache)
    assert (first.path / "a.txt").exists() and not (first.path / "b.txt").exists()  # first commit
    assert marker_path(first.path).read_text().strip() == origin.first
    assert not any(p.name.startswith(".archlens") for p in first.path.iterdir())  # tree untouched
    assert sorted(p.name for p in cache.iterdir()) == sorted(
        [first.path.name, marker_path(first.path).name]
    )  # temp dirs cleaned up
    assert fetch(repo, cache, protocol="file").cached


def test_fetch_replaces_a_partial_checkout(origin: Origin, tmp_path: Path) -> None:
    repo = pinned(origin, origin.second)
    stale = checkout_path(repo, tmp_path)
    stale.mkdir(parents=True)
    (stale / "junk").write_text("x")
    fetched = fetch(repo, tmp_path, protocol="file")
    assert not fetched.cached and not (fetched.path / "junk").exists()


def test_fetch_respects_ingest_limits(origin: Origin, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    limits = IngestLimits(max_files=1)
    with pytest.raises(IngestLimitExceeded):
        fetch(pinned(origin, origin.second), cache, limits=limits, protocol="file")
    assert list(cache.iterdir()) == []  # nothing left behind


def test_unknown_commit_fails(origin: Origin, tmp_path: Path) -> None:
    with pytest.raises(IngestError):
        fetch(pinned(origin, "f" * 40), tmp_path, protocol="file")
