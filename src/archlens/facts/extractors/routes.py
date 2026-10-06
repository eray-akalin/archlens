"""HTTP routes → `route` facts (has_http_api, LOG-05 health endpoint, AUTH-* context).

Python (ast): decorators `@<x>.get/post/…/route/api_route("<path>")` with router prefixes from
`APIRouter(prefix=…)` / `Blueprint(url_prefix=…)`, and Django `path()`/`re_path()` in url modules.
Other stacks by pattern: Express/NestJS, ASP.NET minimal APIs and controllers, Spring, Go (gin,
echo, net/http). Mount prefixes from `include_router`/`app.use(prefix, router)` are not applied.
"""

import ast
import re

from pydantic import JsonValue

from archlens.facts.base import ScanContext, make_fact
from archlens.facts.extractors.common import code_evidence, line_of, python_ast, source_files, text
from archlens.models import Fact, FileEntry

SOURCE = "ast:routes"
_HTTP_METHODS = frozenset({"get", "post", "put", "delete", "patch", "head", "options"})
_ROUTE_DECORATORS = _HTTP_METHODS | {"route", "api_route", "websocket"}
_FRAMEWORK_IMPORTS = {
    "fastapi": "fastapi",
    "flask": "flask",
    "starlette": "starlette",
    "django": "django",
    "sanic": "sanic",
}

_PATTERNS: tuple[tuple[frozenset[str], str, re.Pattern[str]], ...] = (
    (
        frozenset({"javascript", "typescript"}),
        "express",
        re.compile(
            r"""\b(?:app|router|server|api|route)\.(get|post|put|delete|patch|all)\(\s*(['"`])([^'"`]+)\2"""
        ),
    ),
    (
        frozenset({"typescript", "javascript"}),
        "nestjs",
        re.compile(r"""@(Get|Post|Put|Delete|Patch)\(\s*(['"]?)([^'")]*)\2\s*\)"""),
    ),
    (
        frozenset({"csharp"}),
        "aspnetcore",
        re.compile(r"""\.Map(Get|Post|Put|Delete|Patch)\(\s*(")([^"]*)\""""),
    ),
    (
        frozenset({"csharp"}),
        "aspnetcore",
        re.compile(r"""\[Http(Get|Post|Put|Delete|Patch)(?:\(\s*(")([^"]*)"\s*\))?\]"""),
    ),
    (
        frozenset({"java", "kotlin"}),
        "spring",
        re.compile(
            r"""@(Get|Post|Put|Delete|Patch)Mapping\(\s*(?:value\s*=\s*|path\s*=\s*)?(")([^"]*)\""""
        ),
    ),
    (
        frozenset({"go"}),
        "go",
        re.compile(
            r"""\.(GET|POST|PUT|DELETE|PATCH|Get|Post|Put|Delete|Patch)\(\s*(")(/[^"]*)\""""
        ),
    ),
    (frozenset({"go"}), "go", re.compile(r"""\bHandleFunc\(\s*(?P<m>)(")(/[^"]*)\"""")),
)


def _string_arg(call: ast.Call, *keywords: str) -> str | None:
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    for kw in call.keywords:
        if (
            kw.arg in keywords
            and isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, str)
        ):
            return kw.value.value
    return None


def _methods(call: ast.Call, decorator: str) -> list[str]:
    if decorator in _HTTP_METHODS:
        return [decorator.upper()]
    for kw in call.keywords:
        if kw.arg == "methods" and isinstance(kw.value, ast.List | ast.Tuple | ast.Set):
            return [str(e.value).upper() for e in kw.value.elts if isinstance(e, ast.Constant)]
    return (
        ["WEBSOCKET"]
        if decorator == "websocket"
        else (["ANY"] if decorator == "api_route" else ["GET"])
    )


def _prefixes(tree: ast.Module) -> dict[str, str]:
    """Module-level `x = APIRouter(prefix="/p")` / `Blueprint(..., url_prefix="/p")`."""
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            for kw in node.value.keywords:
                if kw.arg in {"prefix", "url_prefix"} and isinstance(kw.value, ast.Constant):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            found[target.id] = str(kw.value.value)
    return found


def _python_framework(tree: ast.Module) -> str:
    for node in ast.walk(tree):
        names = (
            [a.name for a in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else []
        )
        for name in names:
            if (top := name.split(".")[0]) in _FRAMEWORK_IMPORTS:
                return _FRAMEWORK_IMPORTS[top]
    return "python"


def _django_route(ctx: ScanContext, f: FileEntry, node: ast.AST) -> Fact | None:
    """`path("users/", view)` / `re_path(...)` in a Django url module."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
        return None
    route = _string_arg(node)
    if node.func.id not in {"path", "re_path"} or route is None or len(node.args) < 2:
        return None
    attributes: dict[str, JsonValue] = {
        "path": "/" + route.lstrip("^/"),
        "method": "ANY",
        "handler": ast.unparse(node.args[1])[:200],
        "is_async": False,
        "decorators": [],
        "framework": "django",
        "file": f.path,
    }
    return make_fact("route", SOURCE, attributes, code_evidence(ctx, f.path, node.lineno))


def _python_routes(ctx: ScanContext, f: FileEntry) -> list[Fact]:
    tree = python_ast(ctx, f)
    if tree is None:
        return []
    framework, prefixes = _python_framework(tree), _prefixes(tree)
    facts: list[Fact] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for deco in node.decorator_list:
                if not (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)):
                    continue
                name = deco.func.attr
                path = _string_arg(deco, "path", "rule")
                if name not in _ROUTE_DECORATORS or path is None:
                    continue
                owner = deco.func.value.id if isinstance(deco.func.value, ast.Name) else ""
                full = prefixes.get(owner, "") + path
                for method in _methods(deco, name):
                    attributes: dict[str, JsonValue] = {
                        "path": full or "/",
                        "method": method,
                        "handler": node.name,
                        "is_async": isinstance(node, ast.AsyncFunctionDef),
                        "decorators": [ast.unparse(d)[:200] for d in node.decorator_list],
                        "framework": framework,
                        "file": f.path,
                    }
                    facts.append(
                        make_fact(
                            "route", SOURCE, attributes, code_evidence(ctx, f.path, deco.lineno)
                        )
                    )
        elif framework == "django" and (route := _django_route(ctx, f, node)) is not None:
            facts.append(route)
    return facts


def _pattern_routes(ctx: ScanContext, f: FileEntry) -> list[Fact]:
    source = text(ctx, f.path)
    if source is None:
        return []
    facts: list[Fact] = []
    for languages, framework, pattern in _PATTERNS:
        if f.language not in languages:
            continue
        for match in pattern.finditer(source):
            method = (match.group(1) or "ANY").upper()
            attributes: dict[str, JsonValue] = {
                "path": match.group(3) or "",
                "method": "ANY" if method == "ALL" else method,
                "handler": "",
                "is_async": False,
                "decorators": [],
                "framework": framework,
                "file": f.path,
            }
            line = line_of(source, match.start())
            facts.append(make_fact("route", SOURCE, attributes, code_evidence(ctx, f.path, line)))
    return facts


def extract_routes(ctx: ScanContext) -> list[Fact]:
    facts: list[Fact] = []
    for f in source_files(ctx):
        facts += _python_routes(ctx, f) if f.language == "python" else _pattern_routes(ctx, f)
    return facts
