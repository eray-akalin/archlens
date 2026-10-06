"""Stage 3: code index — chunk, embed (cached), store; hybrid search with RRF (ARCHITECTURE §2.3).

Search fuses BM25 and vector rankings with reciprocal rank fusion: score = Σ 1 / (RRF_K + rank).
"""

import asyncio
import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from archlens.evidence import SnippetReader
from archlens.globs import glob_match
from archlens.index import store
from archlens.index.chunker import Chunk, SymbolDef, chunk_file, symbol_defs
from archlens.index.embed import Embedder
from archlens.models import RepoSnapshot

__all__ = ["IndexStats", "SearchHit", "SymbolDef", "build_index", "find_symbols", "search"]

RRF_K = 60
SearchMode = Literal["hybrid", "bm25", "vector"]


@dataclass(frozen=True)
class IndexStats:
    files: int
    chunks: int
    symbols: int = 0


@dataclass(frozen=True)
class SearchHit:
    path: str
    start_line: int
    end_line: int
    symbol: str | None
    preview: str  # first 3 lines of the (redacted) chunk
    score: float
    matched_by: tuple[str, ...]  # "bm25" and/or "vector"


def _parse(snapshot: RepoSnapshot, reader: SnippetReader) -> tuple[list[Chunk], list[SymbolDef]]:
    chunks: list[Chunk] = []
    defs: list[SymbolDef] = []
    for f in snapshot.files:
        if not f.readable or f.is_vendored or f.is_generated or f.language is None:
            continue
        lines = reader.lines(f.path)
        if lines:
            text = "\n".join(lines) + "\n"
            chunks += chunk_file(f.path, f.language, text, reader.redactor)
            defs += symbol_defs(f.path, f.language, text)
    return chunks, defs


async def build_index(
    path: Path, snapshot: RepoSnapshot, reader: SnippetReader, embedder: Embedder
) -> IndexStats:
    """(Re)build the index file at `path`. `reader` carries the run's Redactor, so chunk text and
    everything sent to the embedder are redacted."""
    chunks, defs = await asyncio.to_thread(_parse, snapshot, reader)
    vectors = await embedder.embed([c.text for c in chunks]) if chunks else []
    await asyncio.to_thread(
        store.create, path, model=embedder.model, dim=embedder.dim, commit_sha=snapshot.commit_sha
    )
    await asyncio.to_thread(store.insert, path, chunks, vectors)
    await asyncio.to_thread(store.insert_symbols, path, defs)
    return IndexStats(files=len({c.path for c in chunks}), chunks=len(chunks), symbols=len(defs))


async def search(
    path: Path,
    query: str,
    embedder: Embedder,
    *,
    mode: SearchMode = "hybrid",
    top_k: int = 8,
    path_glob: str | None = None,
) -> list[SearchHit]:
    """Best `top_k` chunks for `query`; `path_glob` (GLOBSTAR|BRACE|DOTGLOB) filters by path."""
    pool = max(top_k * 6, 50) if path_glob is None else 500
    rankings: dict[str, list[int]] = {}
    if mode in {"hybrid", "bm25"}:
        rankings["bm25"] = await asyncio.to_thread(store.bm25, path, query, pool)
    if mode in {"hybrid", "vector"}:
        vector = (await embedder.embed([query]))[0]
        rankings["vector"] = await asyncio.to_thread(store.knn, path, vector, pool)
    scores: dict[int, float] = {}
    sources: dict[int, list[str]] = {}
    for source, ids in rankings.items():
        for rank, chunk_id in enumerate(ids, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
            sources.setdefault(chunk_id, []).append(source)
    chunks = await asyncio.to_thread(store.fetch, path, list(scores))
    ordered = sorted(scores, key=lambda i: (-scores[i], chunks[i].path, chunks[i].start_line))
    hits: list[SearchHit] = []
    for chunk_id in ordered:
        chunk = chunks[chunk_id]
        if path_glob is not None and not glob_match(chunk.path, path_glob):
            continue
        preview = "\n".join(chunk.text.splitlines()[:3])
        hits.append(
            SearchHit(
                chunk.path,
                chunk.start_line,
                chunk.end_line,
                chunk.symbol,
                preview,
                round(scores[chunk_id], 6),
                tuple(sources[chunk_id]),
            )
        )
        if len(hits) == top_k:
            break
    return hits


async def find_symbols(path: Path, pattern: str, limit: int = 20) -> tuple[list[SymbolDef], int]:
    """Definitions whose name or qualified name matches the glob `pattern` (case-insensitive;
    `*`, `?`, `[...]`), ordered by path and line: (first `limit`, total match count)."""
    defs = await asyncio.to_thread(store.symbols, path)
    wanted = pattern.lower()
    found = [
        d
        for d in defs
        if fnmatch.fnmatchcase(d.name.lower(), wanted)
        or fnmatch.fnmatchcase(d.qualified.lower(), wanted)
    ]
    return found[:limit], len(found)
