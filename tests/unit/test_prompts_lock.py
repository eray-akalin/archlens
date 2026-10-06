"""Prompt versioning: the lock must match the prompt files (docs/LLM.md §3)."""

import shutil
from pathlib import Path

import pytest
from jinja2 import UndefinedError
from typer.testing import CliRunner

from archlens.cli import app
from archlens.errors import ConfigError
from archlens.llm.prompts import (
    LOCK_FILE,
    load_prompt,
    load_prompts,
    lock_problems,
    read_lock,
    update_lock,
)

PROMPTS = Path(__file__).parents[2] / "prompts"
PROMPT = "---\nid: demo\nversion: 1.0.0\nrole: evaluator\n---\nHello {{ name }}.\n"


def test_shipped_prompts_match_the_lock() -> None:
    """Fails when a prompt body changes without a version bump (or a bump wasn't relocked)."""
    assert lock_problems(load_prompts(PROMPTS), read_lock(PROMPTS)) == []


def make_dir(tmp_path: Path, text: str = PROMPT) -> Path:
    (tmp_path / "demo.md").write_text(text)
    return tmp_path


def test_prompt_version_and_render(tmp_path: Path) -> None:
    prompt = load_prompt(make_dir(tmp_path) / "demo.md")
    assert prompt.prompt_version == f"demo@1.0.0+{prompt.sha256[:8]}"
    assert prompt.render(name="repo {{ 7 * 7 }}") == "Hello repo {{ 7 * 7 }}."  # data, not code
    with pytest.raises(UndefinedError):
        prompt.render()


def test_body_change_without_bump_is_caught(tmp_path: Path) -> None:
    directory = make_dir(tmp_path)
    update_lock(directory)
    (directory / "demo.md").write_text(PROMPT.replace("Hello", "Hi"))
    assert lock_problems(load_prompts(directory), read_lock(directory)) == [
        "demo: body changed without a version bump (1.0.0)"
    ]
    with pytest.raises(ConfigError, match="bump the version first"):
        update_lock(directory)


def test_bump_then_relock(tmp_path: Path) -> None:
    directory = make_dir(tmp_path)
    update_lock(directory)
    (directory / "demo.md").write_text(PROMPT.replace("Hello", "Hi").replace("1.0.0", "1.1.0"))
    assert lock_problems(load_prompts(directory), read_lock(directory)) == [
        "demo: version bumped; run `archlens prompts lock`"
    ]
    update_lock(directory)
    assert lock_problems(load_prompts(directory), read_lock(directory)) == []


def test_new_and_removed_prompts(tmp_path: Path) -> None:
    directory = make_dir(tmp_path)
    assert lock_problems(load_prompts(directory), {}) == [
        "demo: not in prompts.lock; run `archlens prompts lock`"
    ]
    stale = {"gone": {"version": "1.0.0", "sha256": "0" * 64}}
    assert lock_problems({}, stale) == ["gone: locked but the prompt file is gone"]


@pytest.mark.parametrize(
    ("text", "field"),
    [
        ("no front matter\n", "front matter"),
        ("---\nid: demo\nversion: 1.0.0\n---\nbody\n", "front matter"),  # role missing
        ("---\nid: other\nversion: 1.0.0\nrole: x\n---\nbody\n", "id"),
        ("---\nid: demo\nversion: v1\nrole: x\n---\nbody\n", "version"),
    ],
)
def test_bad_front_matter(tmp_path: Path, text: str, field: str) -> None:
    with pytest.raises(ConfigError) as info:
        load_prompt(make_dir(tmp_path, text) / "demo.md")
    assert info.value.field == field


def test_cli_lock(tmp_path: Path) -> None:
    directory = tmp_path / "prompts"
    shutil.copytree(PROMPTS, directory)
    (directory / LOCK_FILE).unlink()
    result = CliRunner().invoke(app, ["prompts", "lock", "--directory", str(directory)])
    assert result.exit_code == 0, result.output
    assert "evaluator.system@" in result.output
    assert read_lock(directory) == read_lock(PROMPTS)
    (directory / "evaluator.repo.md").write_text(
        (directory / "evaluator.repo.md").read_text() + "\nchanged\n"
    )
    failed = CliRunner().invoke(app, ["prompts", "lock", "--directory", str(directory)])
    assert failed.exit_code == 1 and "bump the version first" in failed.output
