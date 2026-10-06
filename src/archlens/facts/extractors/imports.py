"""Imports → `import_edge` facts (STR-04 cycles, LOG-01 logging framework, LOG-05 telemetry).

Python: stdlib `ast`, relative imports resolved, `from pkg import sub` points at the submodule when
it is a repo module. JS/TS: `import … from`, `export … from`, `require()` and `import()` patterns;
relative specifiers are resolved to repo paths (without extension) and marked internal.
"""

import ast
import posixpath
import re

from pydantic import JsonValue

from archlens.facts.base import ScanContext, make_fact
from archlens.facts.extractors.common import (
    code_evidence,
    line_of,
    python_ast,
    python_module_name,
    source_files,
    text,
)
from archlens.models import Fact

SOURCE = "ast:imports"
_JS_IMPORT = re.compile(
    r"""(?:\bimport\s+(?:[\w*{}\s,$]+?\s+from\s+)?|\bexport\s+[\w*{}\s,$]+?\s+from\s+|\brequire\(\s*|\bimport\(\s*)['"]([^'"]+)['"]"""
)
_JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def resolve_relative(here: str, is_package: bool, module: str | None, level: int) -> str | None:
    """Absolute module for `from <level dots><module> import …` inside module `here`."""
    if level == 0:
        return module
    package = here.split(".") if is_package else here.split(".")[:-1]
    if level - 1 > len(package):
        return None
    parts = package[: len(package) - (level - 1)] + (module.split(".") if module else [])
    return ".".join(parts) or None


def _is_internal(module: str, internal: set[str], prefixes: set[str]) -> bool:
    parts = module.split(".")
    return module in prefixes or any(
        ".".join(parts[:i]) in internal for i in range(1, len(parts) + 1)
    )


def _python_edges(ctx: ScanContext) -> list[Fact]:
    files = list(source_files(ctx, "python"))
    internal = {python_module_name(f.path) for f in files}
    prefixes = {".".join(m.split(".")[:i]) for m in internal for i in range(1, m.count(".") + 2)}
    facts: list[Fact] = []
    for f in files:
        tree = python_ast(ctx, f)
        if tree is None:
            continue
        here = python_module_name(f.path)
        seen: set[str] = set()
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = resolve_relative(
                    here, f.path.endswith("__init__.py"), node.module, node.level
                )
                if base is None:
                    continue
                subs = [f"{base}.{a.name}" for a in node.names if f"{base}.{a.name}" in internal]
                targets = subs or [base]
            else:
                continue
            for target in targets:
                if target in seen or target == here:
                    continue
                seen.add(target)
                attributes: dict[str, JsonValue] = {
                    "from_module": here,
                    "to_module": target,
                    "internal": _is_internal(target, internal, prefixes),
                    "language": "python",
                }
                facts.append(
                    make_fact(
                        "import_edge", SOURCE, attributes, code_evidence(ctx, f.path, node.lineno)
                    )
                )
    return facts


def _js_edges(ctx: ScanContext) -> list[Fact]:
    facts: list[Fact] = []
    for f in source_files(ctx, "javascript", "typescript"):
        source = text(ctx, f.path)
        if source is None:
            continue
        here = f.path.rsplit(".", 1)[0]
        seen: set[str] = set()
        for match in _JS_IMPORT.finditer(source):
            spec = match.group(1)
            if spec.startswith("."):
                target = posixpath.normpath(posixpath.join(posixpath.dirname(f.path), spec))
                target = target.removesuffix("/index")
                for ext in _JS_EXTENSIONS:
                    target = target.removesuffix(ext)
                internal = True
            else:
                internal = spec.startswith(("@/", "~/"))
                target = (
                    spec
                    if internal
                    else "/".join(spec.split("/")[: 2 if spec.startswith("@") else 1])
                )
            if target in seen:
                continue
            seen.add(target)
            attributes: dict[str, JsonValue] = {
                "from_module": here,
                "to_module": target,
                "internal": internal,
                "language": f.language or "javascript",
            }
            line = line_of(source, match.start(1))
            facts.append(
                make_fact("import_edge", SOURCE, attributes, code_evidence(ctx, f.path, line))
            )
    return facts


def extract_imports(ctx: ScanContext) -> list[Fact]:
    return _python_edges(ctx) + _js_edges(ctx)
