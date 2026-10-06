"""Test files → `test_file` facts (TEST-01 presence, TEST-05 assertion density as LLM context).

Python (ast): `test_*` functions and methods, `assert` statements plus `self.assert*`,
`pytest.raises`, `mock.assert_*` calls; framework pytest or unittest. Other stacks by pattern:
jest/vitest/mocha (`it`/`test`, `expect`/`assert`), Go testing, xUnit/NUnit/MSTest, JUnit.
"""

import ast
import re

from pydantic import JsonValue

from archlens.facts.base import ScanContext, make_fact
from archlens.facts.extractors.common import (
    code_evidence,
    is_test_path,
    python_ast,
    source_files,
    text,
)
from archlens.models import Fact, FileEntry

SOURCE = "ast:tests"
_PATTERNS: dict[str, tuple[str, re.Pattern[str], re.Pattern[str]]] = {
    "javascript": (
        "js",
        re.compile(r"\b(?:it|test)(?:\.each\([^)]*\))?\s*\(\s*['\"`]"),
        re.compile(r"\bexpect\(|\bassert[.(]"),
    ),
    "typescript": (
        "js",
        re.compile(r"\b(?:it|test)(?:\.each\([^)]*\))?\s*\(\s*['\"`]"),
        re.compile(r"\bexpect\(|\bassert[.(]"),
    ),
    "go": (
        "go-testing",
        re.compile(r"(?m)^func\s+Test\w*\(\s*\w+\s+\*testing\.T\)"),
        re.compile(r"\bt\.(Error|Fatal)f?\(|\b(assert|require)\.\w+\("),
    ),
    "csharp": (
        "dotnet",
        re.compile(r"\[(Fact|Theory|Test|TestMethod|TestCase)\b"),
        re.compile(r"\bAssert\.\w+\(|\.Should\(\)"),
    ),
    "java": (
        "junit",
        re.compile(r"@(Test|ParameterizedTest)\b"),
        re.compile(r"\bassert\w*\(|\bassertThat\("),
    ),
    "kotlin": (
        "junit",
        re.compile(r"@(Test|ParameterizedTest)\b"),
        re.compile(r"\bassert\w*\(|\bassertThat\(|shouldBe"),
    ),
}
_JS_FRAMEWORKS = (
    ("vitest", "vitest"),
    ("@jest", "jest"),
    ("jest", "jest"),
    ("mocha", "mocha"),
    ("node:test", "node-test"),
)


def _python_test(ctx: ScanContext, f: FileEntry) -> tuple[str, int, int, int] | None:
    """(framework, tests, asserts, first test line)."""
    tree = python_ast(ctx, f)
    if tree is None:
        return None
    tests: list[int] = []
    asserts = 0
    unittest = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test"
        ):
            tests.append(node.lineno)
        elif isinstance(node, ast.Assert):
            asserts += 1
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            name = node.func.attr
            if name.startswith("assert") or name in {"raises", "fail"}:
                asserts += 1
        elif isinstance(node, ast.ClassDef):
            unittest = unittest or any(ast.unparse(b).endswith("TestCase") for b in node.bases)
    if not tests:
        return None
    return ("unittest" if unittest else "pytest"), len(tests), asserts, min(tests)


def _pattern_test(ctx: ScanContext, f: FileEntry) -> tuple[str, int, int, int] | None:
    spec = _PATTERNS.get(f.language or "")
    source = text(ctx, f.path)
    if spec is None or source is None:
        return None
    framework, test_re, assert_re = spec
    matches = list(test_re.finditer(source))
    if not matches:
        return None
    if framework == "js":
        framework = next((name for needle, name in _JS_FRAMEWORKS if needle in source), "js")
    first = source.count("\n", 0, matches[0].start()) + 1
    return framework, len(matches), len(assert_re.findall(source)), first


def extract_test_files(ctx: ScanContext) -> list[Fact]:
    facts: list[Fact] = []
    for f in source_files(ctx):
        if not is_test_path(f.path):
            continue
        found = _python_test(ctx, f) if f.language == "python" else _pattern_test(ctx, f)
        if found is None:
            continue
        framework, tests, asserts, line = found
        attributes: dict[str, JsonValue] = {
            "path": f.path,
            "framework": framework,
            "test_count": tests,
            "assert_count": asserts,
        }
        facts.append(make_fact("test_file", SOURCE, attributes, code_evidence(ctx, f.path, line)))
    return facts
