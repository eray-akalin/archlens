"""One SQLite file per snapshot: chunk table, FTS5 (BM25) and sqlite-vec (KNN) — ADR-005.

FTS5 uses the porter/unicode61 tokenizer, so identifiers split on `_` and plurals stem. Vectors
are L2-normalized, so sqlite-vec's L2 distance ranks like cosine similarity.
"""

import re
import sqlite3
from contextlib import closing
from pathlib import Path

import sqlite_vec  # pyright: ignore[reportMissingTypeStubs]

from archlens.index.chunker import Chunk, SymbolDef
from archlens.index.embed import pack

_FTS_TOKEN = re.compile(r"\w{2,}")


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)  # pyright: ignore[reportUnknownMemberType]
    conn.enable_load_extension(False)
    return conn


def create(path: Path, *, model: str, dim: int, commit_sha: str) -> None:
    """Create a fresh index file at `path` (replacing an existing one)."""
    path.unlink(missing_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as conn, conn:
        conn.executescript(
            f"""
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY, path TEXT NOT NULL,
                start_line INTEGER NOT NULL, end_line INTEGER NOT NULL,
                symbol TEXT, lang TEXT, sha256 TEXT NOT NULL, text TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                text, symbol, path,
                content='chunks', content_rowid='id', tokenize='porter unicode61'
            );
            CREATE VIRTUAL TABLE chunks_vec USING vec0(embedding float[{int(dim)}]);
            CREATE TABLE symbols (
                path TEXT NOT NULL, name TEXT NOT NULL, qualified TEXT NOT NULL,
                kind TEXT NOT NULL, start_line INTEGER NOT NULL, end_line INTEGER NOT NULL
            );
            """
        )
        conn.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            [("model", model), ("dim", str(dim)), ("commit_sha", commit_sha)],
        )


def insert(path: Path, chunks: list[Chunk], vectors: list[list[float]]) -> None:
    with closing(connect(path)) as conn, conn:
        for chunk, vector in zip(chunks, vectors, strict=True):
            cursor = conn.execute(
                "INSERT INTO chunks (path, start_line, end_line, symbol, lang, sha256, text)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    chunk.path,
                    chunk.start_line,
                    chunk.end_line,
                    chunk.symbol,
                    chunk.lang,
                    chunk.sha256,
                    chunk.text,
                ),
            )
            rowid = cursor.lastrowid
            conn.execute(
                "INSERT INTO chunks_fts (rowid, text, symbol, path) VALUES (?, ?, ?, ?)",
                (rowid, chunk.text, chunk.symbol or "", chunk.path),
            )
            conn.execute(
                "INSERT INTO chunks_vec (rowid, embedding) VALUES (?, ?)", (rowid, pack(vector))
            )


def insert_symbols(path: Path, defs: list[SymbolDef]) -> None:
    with closing(connect(path)) as conn, conn:
        conn.executemany(
            "INSERT INTO symbols (path, name, qualified, kind, start_line, end_line)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [(d.path, d.name, d.qualified, d.kind, d.start_line, d.end_line) for d in defs],
        )


def symbols(path: Path) -> list[SymbolDef]:
    """Every symbol definition, ordered by path and line."""
    with closing(connect(path)) as conn:
        rows = conn.execute(
            "SELECT path, name, qualified, kind, start_line, end_line FROM symbols"
            " ORDER BY path, start_line, qualified"
        ).fetchall()
    return [SymbolDef(r[0], r[1], r[2], r[3], int(r[4]), int(r[5])) for r in rows]


def fts_query(query: str) -> str | None:
    """Free text → an FTS5 OR-query of quoted tokens (no FTS syntax ever reaches MATCH)."""
    words = [w.replace('"', "") for w in _FTS_TOKEN.findall(query)]
    return " OR ".join(f'"{w}"' for w in words) or None


def bm25(path: Path, query: str, limit: int) -> list[int]:
    """Chunk ids by BM25, best first."""
    match = fts_query(query)
    if match is None:
        return []
    with closing(connect(path)) as conn:
        rows = conn.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?"
            " ORDER BY bm25(chunks_fts) LIMIT ?",
            (match, limit),
        ).fetchall()
    return [int(r[0]) for r in rows]


def knn(path: Path, vector: list[float], limit: int) -> list[int]:
    """Chunk ids by vector distance, nearest first."""
    with closing(connect(path)) as conn:
        rows = conn.execute(
            "SELECT rowid FROM chunks_vec WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (pack(vector), limit),
        ).fetchall()
    return [int(r[0]) for r in rows]


def fetch(path: Path, ids: list[int]) -> dict[int, Chunk]:
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    with closing(connect(path)) as conn:
        rows = conn.execute(
            "SELECT id, path, start_line, end_line, symbol, lang, text, sha256 FROM chunks"
            f" WHERE id IN ({marks})",
            ids,
        ).fetchall()
    return {int(r[0]): Chunk(r[1], int(r[2]), int(r[3]), r[4], r[5], r[6], r[7]) for r in rows}


def meta(path: Path) -> dict[str, str]:
    with closing(connect(path)) as conn:
        return {str(k): str(v) for k, v in conn.execute("SELECT key, value FROM meta").fetchall()}
