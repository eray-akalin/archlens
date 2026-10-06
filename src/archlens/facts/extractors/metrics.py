"""Source files → `file_metrics` facts (STR-02 size, TEST-02 test ratio, DOC-04 docstrings).

Public/documented function counts: Python via `ast` (module- and class-level functions not
starting with `_`, documented = has a docstring); JS/TS exported functions preceded by a `/** */`
block; Go exported funcs preceded by `//`; C#/Java/Kotlin public methods preceded by `///` or
`*/`. Facts carry no evidence: they describe a whole file.
"""

import ast
import re

from pydantic import JsonValue

from archlens.facts.base import ScanContext, make_fact
from archlens.facts.extractors.common import is_test_path, python_ast, source_files
from archlens.models import Fact, FileEntry

SOURCE = "ast:metrics"
_PUBLIC: dict[str, re.Pattern[str]] = {
    "javascript": re.compile(
        r"^\s*export\s+(?:default\s+)?(?:async\s+)?(?:function\b|const\s+\w+\s*=\s*(?:async\s*)?\()"
    ),
    "typescript": re.compile(
        r"^\s*export\s+(?:default\s+)?(?:async\s+)?(?:function\b|const\s+\w+\s*=\s*(?:async\s*)?\()"
    ),
    "go": re.compile(r"^func\s+(?:\([^)]*\)\s*)?[A-Z]\w*\s*\("),
    "csharp": re.compile(
        r"^\s*public\s+(?:static\s+|async\s+|override\s+|virtual\s+)*[\w<>\[\],.?]+\s+\w+\s*\("
    ),
    "java": re.compile(
        r"^\s*public\s+(?:static\s+|final\s+|synchronized\s+)*[\w<>\[\],.?]+\s+\w+\s*\("
    ),
    "kotlin": re.compile(r"^\s*(?:public\s+)?fun\s+\w+"),
}


def _python_counts(ctx: ScanContext, f: FileEntry) -> tuple[int, int]:
    tree = python_ast(ctx, f)
    if tree is None:
        return 0, 0
    bodies = [tree.body] + [n.body for n in tree.body if isinstance(n, ast.ClassDef)]
    functions = [
        n
        for body in bodies
        for n in body
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and not n.name.startswith("_")
    ]
    return len(functions), sum(1 for n in functions if ast.get_docstring(n))


def _pattern_counts(lines: list[str], pattern: re.Pattern[str]) -> tuple[int, int]:
    public = documented = 0
    for index, line in enumerate(lines):
        if pattern.search(line):
            public += 1
            previous = next(
                (lines[j].strip() for j in range(index - 1, -1, -1) if lines[j].strip()), ""
            )
            documented += previous.endswith("*/") or previous.startswith(("///", "//"))
    return public, documented


def extract_file_metrics(ctx: ScanContext) -> list[Fact]:
    facts: list[Fact] = []
    for f in source_files(ctx, include_generated=True):
        if f.language == "python":
            public, documented = _python_counts(ctx, f)
        elif (pattern := _PUBLIC.get(f.language or "")) is not None:
            public, documented = _pattern_counts(ctx.reader.lines(f.path) or [], pattern)
        else:
            public = documented = 0
        attributes: dict[str, JsonValue] = {
            "path": f.path,
            "language": f.language,
            "loc": f.loc,
            "is_test": is_test_path(f.path),
            "is_generated": f.is_generated,
            "public_functions": public,
            "documented_functions": documented,
        }
        facts.append(make_fact("file_metrics", SOURCE, attributes, []))
    return facts
