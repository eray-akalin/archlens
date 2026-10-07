"""Dependency manifests → `manifest` and `dependency` facts (STR-06 lockfile, context for SEC-*).

Manifests are read as data (TOML/JSON/text); no package manager is ever invoked. `has_lockfile`:
a lockfile of the manifest's ecosystem sits in its directory or an ancestor (a workspace root, e.g.
`uv.lock` or `bun.lock` locking `backend/` and `frontend/`), or the manifest pins every dependency
exactly (requirements files with only `==`, pom.xml with explicit versions).
"""

import json
import re
import tomllib
from collections.abc import Callable
from pathlib import PurePosixPath

from pydantic import JsonValue

from archlens.facts.base import ScanContext, make_fact
from archlens.facts.extractors.common import as_dict, as_list, code_evidence, find_line, text
from archlens.models import Fact

SOURCE = "ast:manifests"
_PEP508_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_LOCKS = {
    "pyproject": ("uv.lock", "poetry.lock", "pdm.lock", "Pipfile.lock"),
    "package.json": (
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lock",
        "bun.lockb",
    ),
    "go.mod": ("go.sum",),
    "Cargo.toml": ("Cargo.lock",),
    "Gemfile": ("Gemfile.lock",),
    "composer.json": ("composer.lock",),
    "csproj": ("packages.lock.json",),
    "Pipfile": ("Pipfile.lock",),
}

Dep = tuple[str, str, bool]  # name, version_spec, dev


def manifest_type(path: str) -> str | None:
    name = PurePosixPath(path).name
    if name == "pyproject.toml":
        return "pyproject"
    if name.startswith("requirements") and name.endswith(".txt"):
        return "requirements"
    if name.endswith(".csproj"):
        return "csproj"
    if name in {
        "package.json",
        "go.mod",
        "Cargo.toml",
        "Gemfile",
        "composer.json",
        "pom.xml",
        "Pipfile",
    }:
        return name
    return None


def _pep508(spec: str) -> tuple[str, str] | None:
    match = _PEP508_NAME.match(spec)
    if not match:
        return None
    rest = re.sub(r"^\[[^\]]*\]", "", spec[match.end() :].strip()).split(";")[0].strip()
    return match.group(1), rest


def _pyproject(source: str) -> list[Dep]:
    data = as_dict(tomllib.loads(source))
    deps: list[Dep] = []
    project = as_dict(data.get("project"))
    for spec in as_list(project.get("dependencies")):
        if (parsed := _pep508(str(spec))) is not None:
            deps.append((*parsed, False))
    for group in (
        as_dict(project.get("optional-dependencies")),
        as_dict(data.get("dependency-groups")),
    ):
        for specs in group.values():
            for spec in as_list(specs):
                if isinstance(spec, str) and (parsed := _pep508(spec)) is not None:
                    deps.append((*parsed, True))
    poetry = as_dict(as_dict(data.get("tool")).get("poetry"))
    for key, dev in (("dependencies", False), ("dev-dependencies", True)):
        for name, spec in as_dict(poetry.get(key)).items():
            if name != "python":
                deps.append((name, str(spec), dev))
    return deps


def _requirements(source: str) -> list[Dep]:
    deps: list[Dep] = []
    for raw in source.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and not line.startswith("-") and (parsed := _pep508(line)) is not None:
            deps.append((*parsed, False))
    return deps


def _package_json(source: str) -> list[Dep]:
    data = as_dict(json.loads(source))
    return [
        (name, str(spec), dev)
        for key, dev in (("dependencies", False), ("devDependencies", True))
        for name, spec in as_dict(data.get(key)).items()
    ]


def _regex_deps(source: str, pattern: str) -> list[Dep]:
    return [(m.group(1), m.group(2) or "", False) for m in re.finditer(pattern, source)]


def _go_mod(source: str) -> list[Dep]:
    return _regex_deps(source, r"(?m)^\s*(?:require\s+)?([\w.\-]+(?:/[\w.\-]+)+)\s+(v[\w.\-+]+)")


def _csproj(source: str) -> list[Dep]:
    return _regex_deps(source, r'<PackageReference\s+Include="([^"]+)"(?:\s+Version="([^"]*)")?')


def _pom(source: str) -> list[Dep]:
    return _regex_deps(
        source, r"<artifactId>([^<]+)</artifactId>\s*(?:<version>([^<]*)</version>)?"
    )


def _gemfile(source: str) -> list[Dep]:
    return _regex_deps(source, r"""(?m)^\s*gem\s+['"]([^'"]+)['"](?:\s*,\s*['"]([^'"]+)['"])?""")


def _toml_table(source: str, table: str) -> list[Dep]:
    return [
        (name, str(spec), False)
        for name, spec in as_dict(as_dict(tomllib.loads(source)).get(table)).items()
    ]


def _composer(source: str) -> list[Dep]:
    return [
        (name, str(spec), False)
        for name, spec in as_dict(as_dict(json.loads(source)).get("require")).items()
    ]


_PARSERS: dict[str, Callable[[str], list[Dep]]] = {
    "pyproject": _pyproject,
    "requirements": _requirements,
    "package.json": _package_json,
    "go.mod": _go_mod,
    "csproj": _csproj,
    "pom.xml": _pom,
    "Cargo.toml": lambda source: _toml_table(source, "dependencies"),
    "Gemfile": _gemfile,
    "composer.json": _composer,
    "Pipfile": lambda source: _toml_table(source, "packages"),
}


def _has_lockfile(kind: str, path: str, deps: list[Dep], present: set[str]) -> bool:
    names = _LOCKS.get(kind, ())
    for folder in PurePosixPath(path).parents:  # own directory first, then up to the root
        prefix = "" if str(folder) == "." else f"{folder}/"
        if any(prefix + name in present for name in names):
            return True
    if kind == "requirements":
        return bool(deps) and all(spec.startswith("==") for _, spec, _ in deps)
    if kind == "pom.xml":
        return all(spec for _, spec, _ in deps)
    return False


def extract_manifests(ctx: ScanContext) -> list[Fact]:
    present = {f.path for f in ctx.snapshot.files}
    facts: list[Fact] = []
    for f in ctx.snapshot.files:
        kind = manifest_type(f.path)
        if kind is None or not f.readable or f.is_vendored:
            continue
        source = text(ctx, f.path)
        if source is None:
            continue
        try:
            deps = _PARSERS[kind](source)
        except (ValueError, tomllib.TOMLDecodeError):
            deps = []
        attributes: dict[str, JsonValue] = {
            "path": f.path,
            "type": kind,
            "has_lockfile": _has_lockfile(kind, f.path, deps, present),
            "dependency_count": len(deps),
        }
        facts.append(make_fact("manifest", SOURCE, attributes, code_evidence(ctx, f.path, 1)))
        for name, spec, dev in deps:
            line = find_line(ctx, f.path, re.compile(re.escape(name), re.IGNORECASE))
            dep_attrs: dict[str, JsonValue] = {
                "name": name,
                "version_spec": spec,
                "dev": dev,
                "manifest": f.path,
            }
            facts.append(
                make_fact("dependency", SOURCE, dep_attrs, code_evidence(ctx, f.path, line))
            )
    return facts
