"""Symbol-aware chunking (docs/ARCHITECTURE.md §2.3).

tree-sitter (grammars bundled in pinned wheels, never downloaded at runtime — ADR-015) yields one
chunk per function/method/class; classes too big for one chunk are split into their members plus
a header chunk; anything longer than MAX_CHUNK_LINES, code between symbols, and files without a
grammar fall back to WINDOW-line windows with OVERLAP lines of overlap. Chunk text is redacted
before it is stored or embedded.
"""

import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import tree_sitter_c_sharp
import tree_sitter_go
import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from archlens.security.redact import Redactor

MAX_CHUNK_LINES = 80
WINDOW = 60
OVERLAP = 10

_GRAMMARS: dict[str, Callable[[], object]] = {
    "python": tree_sitter_python.language,
    "javascript": tree_sitter_javascript.language,
    "typescript": tree_sitter_typescript.language_typescript,
    "tsx": tree_sitter_typescript.language_tsx,
    "java": tree_sitter_java.language,
    "csharp": tree_sitter_c_sharp.language,
    "go": tree_sitter_go.language,
}
_TYPES = ("class_declaration", "interface_declaration", "enum_declaration", "record_declaration")
_SYMBOLS: dict[str, frozenset[str]] = {
    "python": frozenset({"function_definition", "class_definition", "decorated_definition"}),
    "javascript": frozenset(
        {
            "function_declaration",
            "generator_function_declaration",
            "class_declaration",
            "method_definition",
        }
    ),
    "java": frozenset({*_TYPES, "method_declaration", "constructor_declaration"}),
    "csharp": frozenset(
        {*_TYPES, "struct_declaration", "method_declaration", "constructor_declaration"}
    ),
    "go": frozenset({"function_declaration", "method_declaration", "type_declaration"}),
}
_SYMBOLS["typescript"] = _SYMBOLS["javascript"] | {
    "interface_declaration",
    "enum_declaration",
    "abstract_class_declaration",
}
_SYMBOLS["tsx"] = _SYMBOLS["typescript"]
_CONTAINERS = frozenset(
    {"class_definition", "class_declaration", "abstract_class_declaration", "interface_declaration",
     "struct_declaration", "record_declaration", "decorated_definition"}
)  # fmt: skip
_PARSERS: dict[str, Parser] = {}


@dataclass(frozen=True)
class Chunk:
    path: str
    start_line: int  # 1-based inclusive
    end_line: int
    symbol: str | None
    lang: str | None
    text: str  # redacted
    sha256: str


def _parser(grammar: str) -> Parser:
    if grammar not in _PARSERS:
        _PARSERS[grammar] = Parser(Language(_GRAMMARS[grammar]()))
    return _PARSERS[grammar]


def _grammar_for(language: str | None, path: str) -> str | None:
    if language == "typescript" and path.endswith(".tsx"):
        return "tsx"
    if language == "javascript" and path.endswith(".jsx"):
        return "tsx"  # the TSX grammar parses JSX
    return language if language in _GRAMMARS else None


def _name(node: Node) -> str | None:
    if node.type == "decorated_definition":
        inner = node.child_by_field_name("definition")
        return _name(inner) if inner is not None else None
    name = node.child_by_field_name("name")
    if name is None and node.type == "type_declaration":
        spec = next((c for c in node.named_children if c.type == "type_spec"), None)
        name = spec.child_by_field_name("name") if spec is not None else None
    return (
        name.text.decode("utf-8", "replace") if name is not None and name.text is not None else None
    )


def _is_arrow_const(node: Node) -> bool:
    """`const f = (…) => …` / `const f = function …` in JS/TS."""
    if node.type != "lexical_declaration":
        return False
    return any(
        (value := d.child_by_field_name("value")) is not None
        and value.type in {"arrow_function", "function_expression", "function"}
        for d in node.named_children
        if d.type == "variable_declarator"
    )


def _arrow_name(node: Node) -> str | None:
    declarator = next((d for d in node.named_children if d.type == "variable_declarator"), None)
    name = declarator.child_by_field_name("name") if declarator is not None else None
    return (
        name.text.decode("utf-8", "replace") if name is not None and name.text is not None else None
    )


def _symbol_spans(root: Node, grammar: str) -> list[tuple[int, int, str | None]]:
    """(start_line, end_line, symbol) for symbol nodes, 1-based inclusive, sorted."""
    kinds = _SYMBOLS[grammar]
    spans: list[tuple[int, int, str | None]] = []

    def visit(node: Node, owner: str | None) -> None:
        for child in node.named_children:
            is_symbol = child.type in kinds or (
                grammar in {"javascript", "typescript", "tsx"} and _is_arrow_const(child)
            )
            if not is_symbol:
                visit(child, owner)
                continue
            start, end = child.start_point.row + 1, child.end_point.row + 1
            name = _arrow_name(child) if child.type == "lexical_declaration" else _name(child)
            qualified = f"{owner}.{name}" if owner and name else name
            if child.type in _CONTAINERS and end - start + 1 > MAX_CHUNK_LINES:
                members_before = len(spans)
                visit(child, qualified)
                first_member = min((s for s, _, _ in spans[members_before:]), default=end + 1)
                if first_member > start:
                    spans.append((start, first_member - 1, qualified))  # class header
            else:
                spans.append((start, end, qualified))

    visit(root, None)
    return sorted(spans)


_KINDS = {
    "function_definition": "function", "function_declaration": "function",
    "generator_function_declaration": "function", "lexical_declaration": "function",
    "method_definition": "method", "method_declaration": "method",
    "constructor_declaration": "constructor",
    "class_definition": "class", "class_declaration": "class",
    "abstract_class_declaration": "class", "interface_declaration": "interface",
    "enum_declaration": "enum", "record_declaration": "record", "struct_declaration": "struct",
    "type_declaration": "type",
}  # fmt: skip


@dataclass(frozen=True)
class SymbolDef:
    """A symbol definition: `name` as written, `qualified` with its enclosing symbols."""

    path: str
    name: str
    qualified: str
    kind: str  # function | method | constructor | class | interface | enum | record | struct | type
    start_line: int  # 1-based inclusive (decorators included)
    end_line: int


def symbol_defs(path: str, language: str | None, text: str) -> list[SymbolDef]:
    """Every symbol definition in `text`, nested ones included (unlike chunks, which keep a
    small class whole); empty for languages without a bundled grammar."""
    grammar = _grammar_for(language, path)
    if grammar is None or not text.strip():
        return []
    kinds = _SYMBOLS[grammar]
    js = grammar in {"javascript", "typescript", "tsx"}
    defs: list[SymbolDef] = []

    def visit(node: Node, owner: str | None, in_class: bool) -> None:
        for child in node.named_children:
            if not (child.type in kinds or (js and _is_arrow_const(child))):
                visit(child, owner, in_class)
                continue
            target = child
            if child.type == "decorated_definition":
                target = child.child_by_field_name("definition") or child
            name = _arrow_name(child) if child.type == "lexical_declaration" else _name(target)
            kind = _KINDS.get(target.type, "symbol")
            if kind == "function" and in_class:
                kind = "method"
            qualified = f"{owner}.{name}" if owner and name else name
            if name and qualified:
                start, end = child.start_point.row + 1, child.end_point.row + 1
                defs.append(SymbolDef(path, name, qualified, kind, start, end))
            is_type = kind in {"class", "interface", "enum", "record", "struct", "type"}
            visit(target, qualified or owner, is_type)

    tree = _parser(grammar).parse(text.encode("utf-8", "replace"))
    visit(tree.root_node, None, False)
    return sorted(defs, key=lambda d: (d.start_line, d.end_line, d.qualified))


def _windows(start: int, end: int) -> Iterator[tuple[int, int]]:
    step = WINDOW - OVERLAP
    line = start
    while line <= end:
        stop = min(line + WINDOW - 1, end)
        yield line, stop
        if stop == end:
            return
        line += step


def chunk_file(path: str, language: str | None, text: str, redactor: Redactor) -> list[Chunk]:
    """Chunks covering every non-blank line of `text` (a file's full content)."""
    lines = text.splitlines()
    if not lines:
        return []
    grammar = _grammar_for(language, path)
    spans: list[tuple[int, int, str | None]] = []
    if grammar is not None:
        tree = _parser(grammar).parse(text.encode("utf-8", "replace"))
        spans = _symbol_spans(tree.root_node, grammar)
    pieces: list[tuple[int, int, str | None]] = []
    cursor = 1
    for start, end, symbol in spans:
        if start < cursor:  # nested inside a span already taken
            continue
        pieces += [(a, b, None) for a, b in _windows(cursor, start - 1)]
        if end - start + 1 > MAX_CHUNK_LINES:
            pieces += [(a, b, symbol) for a, b in _windows(start, end)]
        else:
            pieces.append((start, end, symbol))
        cursor = end + 1
    pieces += [(a, b, None) for a, b in _windows(cursor, len(lines))]

    chunks: list[Chunk] = []
    for first, last, symbol in pieces:
        start, end = first, last
        while start <= end and not lines[start - 1].strip():  # trim blank edges
            start += 1
        while end >= start and not lines[end - 1].strip():
            end -= 1
        if start > end:
            continue
        body = "\n".join(lines[start - 1 : end])
        redacted = redactor.redact_excerpt(path, body + "\n", first_line=start).rstrip("\n")
        digest = hashlib.sha256(redacted.encode("utf-8", "replace")).hexdigest()
        chunks.append(Chunk(path, start, end, symbol, language, redacted, digest))
    return chunks
