"""Versioned prompt templates (docs/LLM.md §3).

A prompt file is `prompts/<id>.md`: YAML front matter (`id`, `version`, `role`) between `---`
lines, then a Jinja2 body. `prompt_version = "{id}@{version}+{sha256(body)[:8]}"`.
`prompts/prompts.lock` pins `id → {version, sha256}`; `lock_problems` reports bodies that changed
without a version bump and versions bumped without relocking, and `update_lock` refuses to pin a
changed body under an unchanged version. Repository content is only ever passed as template
variables (never as template source), so it can't run template code.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined
from pydantic import BaseModel, ConfigDict, ValidationError

from archlens.errors import ConfigError

DEFAULT_PROMPTS_DIR = Path("prompts")
LOCK_FILE = "prompts.lock"
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)
_ENV = Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=False)


class _FrontMatter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    version: str
    role: str


@dataclass(frozen=True)
class Prompt:
    id: str
    version: str
    role: str
    body: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()

    @property
    def prompt_version(self) -> str:
        return f"{self.id}@{self.version}+{self.sha256[:8]}"

    def render(self, **variables: object) -> str:
        """Render the body. Raises jinja2.UndefinedError for a missing variable."""
        return _ENV.from_string(self.body).render(**variables).strip()


def load_prompt(path: Path) -> Prompt:
    """Parse one prompt file. Raises ConfigError (bad front matter, id ≠ file stem)."""
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    match = _FRONT_MATTER.match(text)
    if match is None:
        raise ConfigError(str(path), "front matter", "expected '---' YAML '---' then the body")
    try:
        meta = _FrontMatter.model_validate(yaml.safe_load(match.group(1)))
    except (yaml.YAMLError, ValidationError) as exc:
        raise ConfigError(str(path), "front matter", str(exc)) from exc
    if meta.id != path.name.removesuffix(".md"):
        raise ConfigError(str(path), "id", f"{meta.id!r} must equal the file name")
    if not re.fullmatch(r"\d+\.\d+\.\d+", meta.version):
        raise ConfigError(str(path), "version", f"{meta.version!r} is not semver")
    return Prompt(meta.id, meta.version, meta.role, match.group(2))


def load_prompts(directory: Path = DEFAULT_PROMPTS_DIR) -> dict[str, Prompt]:
    """Every `*.md` prompt in `directory`, keyed by id."""
    return {p.id: p for p in map(load_prompt, sorted(directory.glob("*.md")))}


def read_lock(directory: Path = DEFAULT_PROMPTS_DIR) -> dict[str, dict[str, str]]:
    path = directory / LOCK_FILE
    if not path.exists():
        return {}
    data: dict[str, dict[str, str]] = json.loads(path.read_text(encoding="utf-8"))
    return data


def lock_problems(prompts: dict[str, Prompt], lock: dict[str, dict[str, str]]) -> list[str]:
    """Why `lock` doesn't match `prompts`; empty when in sync."""
    problems: list[str] = []
    for prompt_id, prompt in sorted(prompts.items()):
        entry = lock.get(prompt_id)
        if entry is None:
            problems.append(f"{prompt_id}: not in {LOCK_FILE}; run `archlens prompts lock`")
        elif entry["sha256"] != prompt.sha256 and entry["version"] == prompt.version:
            problems.append(f"{prompt_id}: body changed without a version bump ({prompt.version})")
        elif entry["sha256"] != prompt.sha256 or entry["version"] != prompt.version:
            problems.append(f"{prompt_id}: version bumped; run `archlens prompts lock`")
    problems += [
        f"{i}: locked but the prompt file is gone" for i in sorted(set(lock) - set(prompts))
    ]
    return problems


def update_lock(directory: Path = DEFAULT_PROMPTS_DIR) -> dict[str, dict[str, str]]:
    """Rewrite the lock from the prompt files. Raises ConfigError if a body changed while its
    version stayed the same (bump the version first)."""
    prompts = load_prompts(directory)
    old = read_lock(directory)
    for prompt_id, prompt in prompts.items():
        entry = old.get(prompt_id)
        if entry and entry["sha256"] != prompt.sha256 and entry["version"] == prompt.version:
            raise ConfigError(
                f"{directory / prompt_id}.md", "version", "body changed: bump the version first"
            )
    lock = {
        p.id: {"version": p.version, "sha256": p.sha256}
        for p in sorted(prompts.values(), key=lambda p: p.id)
    }
    (directory / LOCK_FILE).write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    return lock
