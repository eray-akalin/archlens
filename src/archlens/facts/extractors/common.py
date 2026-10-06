"""Helpers shared by the extractors: file selection, cached parsing, test-path detection.

Extractors read files through the context's SnippetReader (jailed, size-capped) and parse them
statically — Python with the stdlib `ast` (never imported or executed), YAML with `safe_load`,
other languages with line-oriented patterns. Parsed YAML/JSON is untyped data; `as_dict` and
`as_list` narrow it without `Any`.
"""

import ast
import re
from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import cast

import yaml

from archlens.facts.base import ScanContext
from archlens.models import Evidence, FileEntry

CODE_LANGUAGES = frozenset(
    {"python", "javascript", "typescript", "java", "kotlin", "scala", "go", "csharp", "ruby",
     "php", "rust", "c", "cpp", "swift"}
)  # fmt: skip
_TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec|specs)/"
    r"|(^|/)test_[^/]+\.py$|_test\.(py|go)$"
    r"|\.(test|spec)\.[jt]sx?$"
    r"|(^|/)src/test/"
    r"|Tests?\.(cs|java|kt)$"
)


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(path))


def source_files(
    ctx: ScanContext, *languages: str, include_generated: bool = False
) -> Iterator[FileEntry]:
    """Readable, non-vendored files in `languages` (default: all code languages)."""
    wanted = frozenset(languages) or CODE_LANGUAGES
    for f in ctx.snapshot.files:
        if (
            f.readable
            and not f.is_vendored
            and f.language in wanted
            and (include_generated or not f.is_generated)
        ):
            yield f


def text(ctx: ScanContext, path: str) -> str | None:
    lines = ctx.reader.lines(path)
    return None if lines is None else "\n".join(lines) + "\n"


_AST_CACHE: dict[tuple[str, str, str], ast.Module | None] = {}


def python_ast(ctx: ScanContext, f: FileEntry) -> ast.Module | None:
    """Parsed module (cached by content hash); None on syntax errors or pathological input."""
    key = (str(ctx.root), f.path, f.sha256)
    if key not in _AST_CACHE:
        source = text(ctx, f.path)
        try:
            _AST_CACHE[key] = None if source is None else ast.parse(source, filename=f.path)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            _AST_CACHE[key] = None
    return _AST_CACHE[key]


def python_module_name(path: str) -> str:
    """`src/pkg/a/b.py` → `pkg.a.b`; `pkg/__init__.py` → `pkg`."""
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def is_mapping(value: object) -> bool:
    """isinstance check that deliberately doesn't narrow (keeps parsed data typed as `object`)."""
    return isinstance(value, dict)


def is_sequence(value: object) -> bool:
    return isinstance(value, list)


def as_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in cast(dict[object, object], value).items()}


def as_list(value: object) -> list[object]:
    return list(cast(list[object], value)) if isinstance(value, list) else []


def yaml_documents(ctx: ScanContext, path: str, max_bytes: int = 500_000) -> list[object]:
    """All YAML documents in `path`; [] if unreadable, too big or invalid (e.g. Helm templates)."""
    source = text(ctx, path)
    if source is None or len(source) > max_bytes:
        return []
    try:
        return [d for d in yaml.safe_load_all(source) if d is not None]
    except yaml.YAMLError:
        return []


def find_line(ctx: ScanContext, path: str, pattern: re.Pattern[str], default: int = 1) -> int:
    for number, line in enumerate(ctx.reader.lines(path) or [], start=1):
        if pattern.search(line):
            return number
    return default


def line_of(source: str, offset: int) -> int:
    """1-based line number of a character offset."""
    return source.count("\n", 0, offset) + 1


def code_evidence(ctx: ScanContext, path: str, line: int) -> list[Evidence]:
    """Single-line evidence list (empty if the line can't be read)."""
    evidence = ctx.reader.evidence(path, line, line)
    return [evidence] if evidence is not None else []
