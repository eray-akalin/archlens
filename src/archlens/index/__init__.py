"""Stage 3: code index — chunk, embed (cached), store; hybrid search with RRF (ARCHITECTURE §2.3).

Search fuses BM25 and vector rankings with reciprocal rank fusion: score = Σ 1 / (RRF_K + rank).
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from wcmatch import glob

from archlens.evidence import SnippetReader
from archlens.index import store
from archlens.index.chunker import Chunk, chunk_file
from archlens.index.embed import Embedder
from archlens.models import RepoSnapshot

__all__ = ["IndexStats", "SearchHit", "build_index", "search"]

RRF_K = 60
SearchMode = Literal["hybrid", "bm25", "vector"]
GLOB_FLAGS = glob.GLOBSTAR | glob.BRACE | glob.DOTGLOB


@dataclass(frozen=True)
class IndexStats:
    files: int
    chunks: int


@dataclass(frozen=True)
class SearchHit:
    path: str
    start_line: int
    end_line: int
    symbol: str | None
    preview: str  # first 3 lines of the (redacted) chunk
    score: float
    matched_by: tuple[str, ...]  # "bm25" and/or "vector"


def _chunks(snapshot: RepoSnapshot, reader: SnippetReader) -> list[Chunk]:
    chunks: list[Chunk] = []
    for f in snapshot.files:
        if not f.readable or f.is_vendored or f.is_generated or f.language is None:
            continue
        lines = reader.lines(f.path)
        if lines:
            chunks += chunk_file(f.path, f.language, "\n".join(lines) + "\n", reader.redactor)
    return chunks


async def build_index(
    path: Path, snapshot: RepoSnapshot, reader: SnippetReader, embedder: Embedder
) -> IndexStats:
    """(Re)build the index file at `path`. `reader` carries the run's Redactor, so chunk text and
    everything sent to the embedder are redacted."""
    chunks = await asyncio.to_thread(_chunks, snapshot, reader)
    vectors = await embedder.embed([c.text for c in chunks]) if chunks else []
    await asyncio.to_thread(
        store.create, path, model=embedder.model, dim=embedder.dim, commit_sha=snapshot.commit_sha
    )
    await asyncio.to_thread(store.insert, path, chunks, vectors)
    return IndexStats(files=len({c.path for c in chunks}), chunks=len(chunks))


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
        if path_glob is not None and not glob.globmatch(chunk.path, path_glob, flags=GLOB_FLAGS):
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
