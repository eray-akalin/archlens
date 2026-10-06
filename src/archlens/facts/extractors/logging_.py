"""Output calls → `log_call` and `print_call` facts (LOG-02 debug output ratio, LOG-04 context).

Python (ast): `print(...)`; `<x>.debug/info/warning/error/exception/critical(...)` where `<x>`
names a logger (`logging`, `logger`, `log`, `self.logger`, structlog/loguru). Other stacks by
pattern: console.*, Console.Write*, System.out.print*, fmt.Print* vs. their logging libraries.
"""

import ast
import re

from pydantic import JsonValue

from archlens.facts.base import ScanContext, make_fact
from archlens.facts.extractors.common import (
    code_evidence,
    is_test_path,
    line_of,
    python_ast,
    source_files,
    text,
)
from archlens.models import Fact, FileEntry

SOURCE = "ast:logging"
_LEVELS = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "fatal", "log"}
)
_PRINT_PATTERNS: dict[str, re.Pattern[str]] = {
    "javascript": re.compile(r"\bconsole\.(log|info|warn|error|debug|trace)\("),
    "typescript": re.compile(r"\bconsole\.(log|info|warn|error|debug|trace)\("),
    "csharp": re.compile(r"\bConsole\.(Write|WriteLine)\("),
    "java": re.compile(r"\bSystem\.(out|err)\.print(ln|f)?\("),
    "kotlin": re.compile(r"(?<![\w.])print(ln)?\("),
    "go": re.compile(r"\bfmt\.Print(ln|f)?\("),
}
_LOG_PATTERNS: dict[str, re.Pattern[str]] = {
    "javascript": re.compile(
        r"\b(logger|log|winston|pino|bunyan)\.(info|warn|error|debug|trace|fatal)\("
    ),
    "typescript": re.compile(
        r"\b(logger|log|winston|pino|bunyan|this\.logger)\.(info|warn|error|debug|trace|fatal|log)\("
    ),
    "csharp": re.compile(
        r"\b(_?logger|_log|Log)\.(Log\w*|Information|Warning|Error|Debug|Critical)\("
    ),
    "java": re.compile(r"\b(log|logger|LOG|LOGGER)\.(info|warn|error|debug|trace)\("),
    "kotlin": re.compile(r"\b(log|logger)\.(info|warn|error|debug|trace)\b"),
    "go": re.compile(
        r"\b(log|slog|logger|zap\.\w+\(\))\.(Print\w*|Info|Warn|Error|Debug|Fatal\w*)\("
    ),
}


def _receiver(node: ast.expr) -> str:
    return ast.unparse(node)[:60]


def _python_calls(ctx: ScanContext, f: FileEntry, in_test: bool) -> list[Fact]:
    tree = python_ast(ctx, f)
    if tree is None:
        return []
    facts: list[Fact] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "print":
            attributes: dict[str, JsonValue] = {"path": f.path, "in_test": in_test, "call": "print"}
            facts.append(
                make_fact("print_call", SOURCE, attributes, code_evidence(ctx, f.path, node.lineno))
            )
        elif isinstance(node.func, ast.Attribute) and node.func.attr in _LEVELS:
            receiver = _receiver(node.func.value)
            if "log" in receiver.lower():
                attributes = {
                    "path": f.path,
                    "logger": receiver,
                    "level": node.func.attr,
                    "in_test": in_test,
                }
                facts.append(
                    make_fact(
                        "log_call", SOURCE, attributes, code_evidence(ctx, f.path, node.lineno)
                    )
                )
    return facts


def _pattern_calls(ctx: ScanContext, f: FileEntry, in_test: bool) -> list[Fact]:
    source = text(ctx, f.path)
    language = f.language or ""
    if source is None:
        return []
    facts: list[Fact] = []
    if (printer := _PRINT_PATTERNS.get(language)) is not None:
        for match in printer.finditer(source):
            attributes: dict[str, JsonValue] = {
                "path": f.path,
                "in_test": in_test,
                "call": match.group(0).rstrip("("),
            }
            facts.append(
                make_fact(
                    "print_call",
                    SOURCE,
                    attributes,
                    code_evidence(ctx, f.path, line_of(source, match.start())),
                )
            )
    if (logger := _LOG_PATTERNS.get(language)) is not None:
        for match in logger.finditer(source):
            attributes = {
                "path": f.path,
                "logger": match.group(1),
                "level": match.group(2).lower(),
                "in_test": in_test,
            }
            facts.append(
                make_fact(
                    "log_call",
                    SOURCE,
                    attributes,
                    code_evidence(ctx, f.path, line_of(source, match.start())),
                )
            )
    return facts


def extract_log_calls(ctx: ScanContext) -> list[Fact]:
    facts: list[Fact] = []
    for f in source_files(ctx):
        in_test = is_test_path(f.path)
        facts += (
            _python_calls(ctx, f, in_test)
            if f.language == "python"
            else _pattern_calls(ctx, f, in_test)
        )
    return facts
