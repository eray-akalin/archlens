"""Read-only repository tools for LLM sessions (docs/LLM.md §4, SECURITY.md §4-6).

`RepoTools` is shared by a run (snapshot listing, facts, redactor, optional index, the run's
`repo_data` boundary and the seen-lines ledger); `RepoTools.session(session_id)` gives the object
an evaluator/skeptic loop talks to. Each tool is a pure function returning a `ToolOutput` that
names exactly the file lines it shows; `ToolSession.call` alone does the bookkeeping — ledger,
`SearchRecord` log, redaction and `untrusted.wrap` — so no tool result can bypass it.

Files are read only through the snapshot listing (symlinks, binaries and oversized files are
listed, never read) after `resolve_in_snapshot`; text is redacted before anything is matched or
shown, so a regex can't probe a secret. Outputs are capped in lines and bytes; out-of-range
arguments are clamped rather than rejected. Nothing here raises for bad model input: problems
come back to the model as the tool result.
"""

import asyncio
import json
import posixpath
import time
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import regex
from pydantic import BaseModel, JsonValue, ValidationError

from archlens import index
from archlens.errors import PathOutsideSnapshot, ToolArgumentError
from archlens.globs import glob_match
from archlens.index.embed import Embedder
from archlens.llm.types import ToolSpec
from archlens.llm.untrusted import wrap
from archlens.models import CodeEvidence, Fact, FactSet, FileEntry, SearchRecord
from archlens.security.redact import Redactor, redact
from archlens.tools.paths import resolve_in_snapshot
from archlens.tools.seen import SeenLines
from archlens.tools.specs import (
    TOOL_SPECS,
    FindSymbolArgs,
    GetFactsArgs,
    ListDirArgs,
    ReadFileArgs,
    SearchCodeArgs,
)

LIST_DIR_MAX_ENTRIES = 200
LIST_DIR_MAX_DEPTH = 5
READ_MAX_LINES = 200
OUTPUT_MAX_BYTES = 24 * 1024
LINE_MAX_CHARS = 2000  # longer lines (minified code) are cut in read_file
PREVIEW_MAX_CHARS = 300  # per line in search previews and fact snippets
PREVIEW_LINES = 3
SEARCH_DEFAULT_K = 8
SEARCH_MAX_K = 15
SYMBOL_MAX = 20
FACTS_DEFAULT = 50
FACTS_MAX = 100
REGEX_TIMEOUT_S = 0.1  # per file (SECURITY.md §4)
REGEX_TOTAL_S = 10.0  # whole regex search; results are marked partial past this
LOG_MAX_PATHS = 20
_CACHE_FILES = 64


@dataclass(frozen=True)
class ToolOutput:
    text: str
    result_count: int
    paths: tuple[str, ...] = ()
    shown: tuple[tuple[str, int, int], ...] = ()  # (path, start, end): file lines in `text`
    source: str | None = None  # wrap label; default is the tool name


# --- shared state ------------------------------------------------------------------------------


class RepoTools:
    def __init__(
        self,
        root: Path,
        files: list[FileEntry],
        facts: FactSet,
        *,
        boundary: str,
        ledger: SeenLines | None = None,
        redactor: Redactor | None = None,
        index_path: Path | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self.root = root
        self.facts = facts
        self.boundary = boundary
        self.ledger = ledger if ledger is not None else SeenLines()
        self.redactor = redactor or Redactor()
        self.index_path = index_path
        self.embedder = embedder
        self._entries = {f.path: f for f in files}
        self._dir_files: dict[str, int] = {}  # directory → files below it (any depth)
        for f in files:
            parts = f.path.split("/")[:-1]
            for k in range(1, len(parts) + 1):
                d = "/".join(parts[:k])
                self._dir_files[d] = self._dir_files.get(d, 0) + 1
        self._cache: OrderedDict[str, list[str] | None] = OrderedDict()

    def session(self, session_id: str) -> "ToolSession":
        return ToolSession(self, session_id)

    # --- reading ---

    def _path(self, raw: str | None) -> str:
        """Repo-relative, lexically normalized path; jailed. Raises PathOutsideSnapshot."""
        text = (raw or "").strip()
        rel = posixpath.normpath(text.replace("\\", "/")) if text else "."
        entry = self._entries.get(rel)
        if entry is not None and entry.symlink_target is not None:  # listed, never followed
            raise ToolArgumentError(f"{rel} is a symlink (to {entry.symlink_target}); not followed")
        resolve_in_snapshot(self.root, text)
        if rel == ".." or rel.startswith("../"):
            raise PathOutsideSnapshot(f"path escapes the snapshot: {raw!r}")
        return "" if rel == "." else rel

    def _file(self, rel: str) -> FileEntry:
        entry = self._entries.get(rel)
        if entry is None:
            if rel in self._dir_files or rel == "":
                raise ToolArgumentError(f"{rel or '.'} is a directory; use list_dir")
            raise ToolArgumentError(f"no such file: {rel}")
        if entry.symlink_target is not None:
            raise ToolArgumentError(f"{rel} is a symlink (to {entry.symlink_target}); not followed")
        if entry.too_large:
            raise ToolArgumentError(f"{rel} is too large to read ({entry.size} bytes)")
        if entry.is_binary:
            raise ToolArgumentError(f"{rel} is a binary file")
        return entry

    def redacted_lines(self, rel: str) -> list[str] | None:
        """Lines of a listed, readable file after redaction (gitleaks spans + patterns); split
        like `SnippetReader` so line numbers match evidence. None if it can't be read."""
        if rel in self._cache:
            self._cache.move_to_end(rel)
            return self._cache[rel]
        lines: list[str] | None = None
        entry = self._entries.get(rel)
        if entry is not None and entry.readable:
            try:
                raw = resolve_in_snapshot(self.root, rel).read_bytes().decode("utf-8", "replace")
            except (PathOutsideSnapshot, OSError):
                raw = None
            if raw is not None:
                body = "\n".join(raw.splitlines()) + "\n" if raw else ""
                lines = self.redactor.redact_excerpt(rel, body).splitlines() if body else []
        self._cache[rel] = lines
        if len(self._cache) > _CACHE_FILES:
            self._cache.popitem(last=False)
        return lines

    # --- tools ---

    def list_dir(self, args: ListDirArgs) -> ToolOutput:
        rel = self._path(args.path)
        if rel and rel not in self._dir_files:
            if rel in self._entries:
                raise ToolArgumentError(f"{rel} is a file; use read_file")
            raise ToolArgumentError(f"no such directory: {rel}")
        depth = _clamp(args.depth, 1, LIST_DIR_MAX_DEPTH, 1)
        prefix = f"{rel}/" if rel else ""
        dirs: set[str] = set()
        files: list[FileEntry] = []
        for path, entry in self._entries.items():
            if not path.startswith(prefix):
                continue
            parts = path[len(prefix) :].split("/")
            dirs.update(prefix + "/".join(parts[:k]) for k in range(1, min(len(parts), depth + 1)))
            if len(parts) <= depth:
                files.append(entry)
        rows = sorted(
            [(d, f"{d}/  ({self._dir_files[d]} files)") for d in dirs]
            + [(f.path, _describe_file(f)) for f in files]
        )
        shown = rows[:LIST_DIR_MAX_ENTRIES]
        lines = [f"{rel or '.'}/ — {len(rows)} entries (depth {depth})", *(r for _, r in shown)]
        if len(rows) > len(shown):
            lines.append(f"[showing {len(shown)} of {len(rows)}; list a subdirectory]")
        return ToolOutput("\n".join(lines), len(rows), tuple(p for p, _ in shown[:LOG_MAX_PATHS]))

    def read_file(self, args: ReadFileArgs) -> ToolOutput:
        rel = self._path(args.path)
        self._file(rel)
        lines = self.redacted_lines(rel)
        if lines is None:
            raise ToolArgumentError(f"{rel} could not be read")
        total = len(lines)
        if total == 0:
            return ToolOutput(f"{rel} is empty", 0, (rel,), source=rel)
        start = max(1, args.start_line or 1)
        if start > total:
            raise ToolArgumentError(f"start_line {start} is past the end of {rel} ({total} lines)")
        wanted_end = min(args.end_line or total, total)
        if wanted_end < start:
            raise ToolArgumentError(f"end_line {args.end_line} is before start_line {start}")
        end = min(wanted_end, start + READ_MAX_LINES - 1)
        width = max(4, len(str(end)))
        body: list[str] = []
        size = 0
        last = start - 1
        for number in range(start, end + 1):
            row = _numbered(number, lines[number - 1], width, LINE_MAX_CHARS)
            size += len(row.encode()) + 1
            if body and size > OUTPUT_MAX_BYTES:
                break
            body.append(row)
            last = number
        out = [f"{rel} (lines {start}-{last} of {total})", *body]
        if last < wanted_end:
            out.append(f"[truncated; continue with start_line={last + 1}]")
        return ToolOutput("\n".join(out), last - start + 1, (rel,), ((rel, start, last),), rel)

    async def search_code(self, args: SearchCodeArgs) -> ToolOutput:
        if not args.query.strip():
            raise ToolArgumentError("query is empty")
        top_k = _clamp(args.top_k, 1, SEARCH_MAX_K, SEARCH_DEFAULT_K)
        if args.mode == "regex":
            return await asyncio.to_thread(self._regex_search, args.query, args.path_glob, top_k)
        if self.index_path is None or self.embedder is None:
            raise ToolArgumentError('hybrid search is unavailable here; use mode="regex"')
        hits = await index.search(
            self.index_path, args.query, self.embedder, top_k=top_k, path_glob=args.path_glob
        )
        blocks: list[str] = []
        shown: list[tuple[str, int, int]] = []
        for i, hit in enumerate(hits, start=1):
            preview = hit.preview.split("\n")[:PREVIEW_LINES]
            last = hit.start_line + len(preview) - 1
            symbol = f" ({hit.symbol})" if hit.symbol else ""
            width = max(4, len(str(last)))
            rows = [
                _numbered(hit.start_line + j, line, width, PREVIEW_MAX_CHARS)
                for j, line in enumerate(preview)
            ]
            blocks.append(
                "\n".join([f"{i}. {hit.path}:{hit.start_line}-{hit.end_line}{symbol}", *rows])
            )
            shown.append((hit.path, hit.start_line, last))
        header = f"{len(hits)} hits for {args.query!r} (hybrid)"
        paths = _unique(h.path for h in hits)
        return ToolOutput("\n\n".join([header, *blocks]), len(hits), paths, tuple(shown))

    def _regex_search(self, query: str, path_glob: str | None, top_k: int) -> ToolOutput:
        try:
            pattern = regex.compile(query, regex.MULTILINE)
        except regex.error as exc:
            raise ToolArgumentError(f"invalid regex: {exc}") from exc
        deadline = time.monotonic() + REGEX_TOTAL_S
        hits: list[tuple[str, int]] = []
        total = files_hit = timeouts = 0
        stopped = False
        for path in sorted(self._entries):
            entry = self._entries[path]
            if not entry.readable or entry.is_vendored:
                continue
            if path_glob and not glob_match(path, path_glob):
                continue
            if time.monotonic() > deadline:
                stopped = True
                break
            lines = self.redacted_lines(path)
            if not lines:
                continue
            text = "\n".join(lines)
            try:
                offsets = [m.start() for m in pattern.finditer(text, timeout=REGEX_TIMEOUT_S)]
            except TimeoutError:
                timeouts += 1
                continue
            matched = sorted({text.count("\n", 0, offset) + 1 for offset in offsets})
            if matched:
                files_hit += 1
                total += len(matched)
                hits += [(path, line) for line in matched[: max(0, top_k - len(hits))]]
        blocks: list[str] = []
        shown: list[tuple[str, int, int]] = []
        for i, (path, line) in enumerate(hits, start=1):
            lines = self.redacted_lines(path) or []
            first, last = max(1, line - 1), min(len(lines), line + 1)
            width = max(4, len(str(last)))
            rows = [
                _numbered(n, lines[n - 1], width, PREVIEW_MAX_CHARS) for n in range(first, last + 1)
            ]
            blocks.append("\n".join([f"{i}. {path}:{line} (lines {first}-{last})", *rows]))
            shown.append((path, first, last))
        header = f"{total} matching lines in {files_hit} files for /{query}/; showing {len(hits)}"
        notes: list[str] = []
        if timeouts:
            notes.append(f"[{timeouts} files skipped: regex timed out]")
        if stopped:
            notes.append(f"[search stopped after {REGEX_TOTAL_S:.0f}s; results are partial]")
        text = "\n\n".join([header, *blocks, *notes])
        return ToolOutput(text, total, _unique(p for p, _ in hits), tuple(shown))

    async def find_symbol(self, args: FindSymbolArgs) -> ToolOutput:
        name = args.name.strip()
        if not name:
            raise ToolArgumentError("name is empty")
        if self.index_path is None:
            raise ToolArgumentError("symbol lookup is unavailable here; use search_code")
        defs, total = await index.find_symbols(self.index_path, name, SYMBOL_MAX)
        rows = [f"{d.path}:{d.start_line}-{d.end_line}  {d.kind}  {d.qualified}" for d in defs]
        lines = [f"{total} definitions match {name!r}", *rows]
        if total > len(defs):
            lines.append(f"[showing {len(defs)} of {total}; use a more specific name]")
        return ToolOutput("\n".join(lines), total, _unique(d.path for d in defs))

    def get_facts(self, args: GetFactsArgs) -> ToolOutput:
        limit = _clamp(args.limit, 1, FACTS_MAX, FACTS_DEFAULT)
        of_kind = self.facts.by_kind(args.kind)
        if not of_kind:
            counts: dict[str, int] = {}
            for f in self.facts.facts:
                counts[f.kind] = counts.get(f.kind, 0) + 1
            available = ", ".join(f"{k} ({n})" for k, n in sorted(counts.items()))
            return ToolOutput(f"no facts of kind {args.kind!r}; available: {available}", 0)
        matching = [
            f
            for f in of_kind
            if not args.path_glob or glob_match(fact_path(f) or "", args.path_glob)
        ]
        rendered = self.render_facts(matching[:limit], max_bytes=OUTPUT_MAX_BYTES)
        header = f"{len(matching)} facts of kind {args.kind!r}"
        if args.path_glob:
            header += f" matching {args.path_glob!r}"
        text = "\n".join([f"{header}; showing {rendered.result_count}", rendered.text])
        if rendered.result_count < len(matching):
            text += "\n[more facts exist; narrow with path_glob or raise limit (max 100)]"
        return ToolOutput(text, len(matching), rendered.paths, rendered.shown)

    def render_facts(self, facts: Sequence[Fact], max_bytes: int | None = None) -> ToolOutput:
        """One JSON line per fact with numbered evidence snippets; `shown` holds every evidence
        line rendered. Stops before `max_bytes`. `result_count` = facts rendered."""
        rows: list[str] = []
        shown: list[tuple[str, int, int]] = []
        size = 0
        for fact in facts:
            evidence: list[JsonValue] = []
            fact_shown: list[tuple[str, int, int]] = []
            for item in fact.evidence:
                if isinstance(item, CodeEvidence):
                    snippet = item.snippet.split("\n")
                    width = max(4, len(str(item.start_line + len(snippet) - 1)))
                    rows_ = [
                        _numbered(item.start_line + j, line, width, PREVIEW_MAX_CHARS)
                        for j, line in enumerate(snippet)
                    ]
                    evidence.append({"path": item.path, "snippet": "\n".join(rows_)})
                    fact_shown.append(
                        (item.path, item.start_line, item.start_line + len(snippet) - 1)
                    )
                else:
                    evidence.append(
                        {"tool": item.tool, "query": item.query, "result_count": item.result_count}
                    )
            row = json.dumps(
                {
                    "id": fact.id,
                    "kind": fact.kind,
                    "severity": fact.severity,
                    "attributes": fact.attributes,
                    "evidence": evidence,
                },
                ensure_ascii=False,
            )
            size += len(row.encode()) + 1
            if max_bytes is not None and rows and size > max_bytes:
                break
            rows.append(row)
            shown += fact_shown
        paths = _unique(p for f in facts[: len(rows)] if (p := fact_path(f)) is not None)
        return ToolOutput("\n".join(rows), len(rows), paths, tuple(shown))


# --- per-session façade ------------------------------------------------------------------------


class ToolSession:
    """What one LLM session (evaluator, consistency rerun, skeptic) calls. Records every call in
    `search_log` and every shown line in the ledger under `session_id`."""

    def __init__(self, tools: RepoTools, session_id: str) -> None:
        self.tools = tools
        self.session_id = session_id
        self.search_log: list[SearchRecord] = []

    @staticmethod
    def specs() -> tuple[ToolSpec, ...]:
        return TOOL_SPECS

    async def call(self, name: str, arguments: str) -> str:
        """Run one tool call; returns the wrapped result text. Never raises for bad input."""
        record_args: dict[str, JsonValue] = {"raw": redact(arguments[:500])}
        try:
            args, output = await self._dispatch(name, arguments or "{}")
            record_args = args.model_dump(mode="json")
        except ValidationError as exc:
            output = _error(f"invalid arguments: {_describe(exc)}")
        except (ToolArgumentError, PathOutsideSnapshot) as exc:
            output = _error(str(exc))
        self.search_log.append(
            SearchRecord(
                tool=name,
                args=record_args,
                result_count=output.result_count,
                paths=list(output.paths[:LOG_MAX_PATHS]),
            )
        )
        self._mark(output)
        return wrap(redact(output.text), output.source or name, self.tools.boundary)

    async def _dispatch(self, name: str, arguments: str) -> tuple[BaseModel, ToolOutput]:
        tools = self.tools
        match name:
            case "list_dir":
                a = ListDirArgs.model_validate_json(arguments)
                return a, await asyncio.to_thread(tools.list_dir, a)
            case "read_file":
                a = ReadFileArgs.model_validate_json(arguments)
                return a, await asyncio.to_thread(tools.read_file, a)
            case "search_code":
                a = SearchCodeArgs.model_validate_json(arguments)
                return a, await tools.search_code(a)
            case "find_symbol":
                a = FindSymbolArgs.model_validate_json(arguments)
                return a, await tools.find_symbol(a)
            case "get_facts":
                a = GetFactsArgs.model_validate_json(arguments)
                return a, await asyncio.to_thread(tools.get_facts, a)
            case _:
                names = ", ".join(spec.name for spec in TOOL_SPECS)
                raise ToolArgumentError(f"unknown tool {name!r}; available: {names}")

    def show_facts(self, facts: Sequence[Fact]) -> str:
        """Facts for the initial prompt: rendered like `get_facts`, wrapped, and their evidence
        lines added to the ledger before the first turn."""
        output = self.tools.render_facts(facts)
        self._mark(output)
        return wrap(redact(output.text), "facts", self.tools.boundary)

    def _mark(self, output: ToolOutput) -> None:
        for path, start, end in output.shown:
            self.tools.ledger.add(self.session_id, path, start, end)


# --- helpers -----------------------------------------------------------------------------------


def fact_path(fact: Fact) -> str | None:
    """The file a fact is about: its first code evidence, else the `file`, `manifest` or `path`
    attribute (`route.path` is a URL path, so evidence and `file` come first)."""
    code = next((e.path for e in fact.evidence if isinstance(e, CodeEvidence)), None)
    if code is not None:
        return code
    for key in ("file", "manifest", "path"):
        value = fact.attributes.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _error(message: str) -> ToolOutput:
    return ToolOutput(f"error: {message}", 0)


def _describe(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or 'arguments'}: {err['msg']}"
        for err in exc.errors()
    )


def _clamp(value: int | None, low: int, high: int, default: int) -> int:
    return default if value is None else max(low, min(high, value))


def _numbered(number: int, line: str, width: int, max_chars: int) -> str:
    if len(line) > max_chars:
        line = line[:max_chars] + " …[line truncated]"
    return f"{number:>{width}}│ {line}"


def _unique(paths: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(paths))


def _describe_file(f: FileEntry) -> str:
    if f.symlink_target is not None:
        return f"{f.path} -> {f.symlink_target}  (symlink)"
    flags = [
        name
        for name, on in (
            ("binary", f.is_binary),
            ("generated", f.is_generated),
            ("vendored", f.is_vendored),
            ("too large to read", f.too_large),
        )
        if on
    ]
    details = ", ".join([*([f.language] if f.language else []), *flags])
    return f"{f.path}  {_size(f.size)}" + (f"  ({details})" if details else "")


def _size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"
