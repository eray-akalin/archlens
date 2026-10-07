"""Chunker, embeddings cache and hybrid search (M1.7)."""

import sqlite3
from importlib.metadata import version
from pathlib import Path

import pytest

from archlens.evidence import SnippetReader
from archlens.index import build_index, find_symbols, search, store
from archlens.index.chunker import MAX_CHUNK_LINES, OVERLAP, WINDOW, chunk_file, symbol_defs
from archlens.index.embed import CachedEmbedder, FakeEmbedder, pack, tokens, unpack
from archlens.ingest.snapshot import build_snapshot
from archlens.models import IngestLimits
from archlens.security.redact import Redactor
from archlens.storage import open_local_storage
from tests.fixture_repos import MaterializedRepo

NO_REDACTION = Redactor()


def spans(text: str, language: str, path: str = "f") -> list[tuple[int, int, str | None]]:
    return [
        (c.start_line, c.end_line, c.symbol) for c in chunk_file(path, language, text, NO_REDACTION)
    ]


# --- chunker ------------------------------------------------------------------------------------


def test_python_symbols_and_module_code() -> None:
    source = (
        "import os\n"  # 1
        "\n"
        "@decorator\n"  # 3
        "def a():\n"
        "    return 1\n"  # 5
        "\n"
        "class B:\n"  # 7
        "    def m(self):\n"
        "        pass\n"  # 9
        "\n"
        "X = 1\n"  # 11
    )
    assert spans(source, "python") == [(1, 1, None), (3, 5, "a"), (7, 9, "B"), (11, 11, None)]


def test_big_class_is_split_into_header_and_methods() -> None:
    methods = "".join(f"    def m{i}(self):\n" + "        x = 1\n" * 8 for i in range(12))
    source = 'class Big:\n    """Doc."""\n' + methods
    found = spans(source, "python")
    assert found[0] == (1, 2, "Big")  # header up to the first method
    assert found[1] == (3, 11, "Big.m0")
    assert len(found) == 13


def test_long_function_falls_back_to_windows() -> None:
    body = "".join(f"    x{i} = {i}\n" for i in range(MAX_CHUNK_LINES + 50))
    found = spans("def long():\n" + body, "python")
    assert all(symbol == "long" for _, _, symbol in found)
    assert found[0][:2] == (1, WINDOW)
    assert found[1][0] == WINDOW - OVERLAP + 1  # windows overlap
    assert found[-1][1] == MAX_CHUNK_LINES + 51


def test_files_without_grammar_use_windows() -> None:
    text = "".join(f"line {i}\n" for i in range(1, 131))
    assert spans(text, "markdown") == [(1, 60, None), (51, 110, None), (101, 130, None)]


@pytest.mark.parametrize(
    ("language", "path", "source", "symbols"),
    [
        (
            "typescript",
            "a.ts",
            "export function add(a: number) {\n  return a\n}\nexport const sub = (a: number) => {\n  return -a\n}\n",
            ["add", "sub"],
        ),
        ("typescript", "c.tsx", "export const App = () => {\n  return <div/>\n}\n", ["App"]),
        ("javascript", "s.js", "class S {\n  get(x) {\n    return x\n  }\n}\n", ["S"]),
        (
            "go",
            "m.go",
            "package m\n\nfunc (s *S) Run() error {\n\treturn nil\n}\n\ntype S struct{}\n",
            ["Run", "S"],
        ),
        ("java", "A.java", "class A {\n  void run() {\n  }\n}\n", ["A"]),
        ("csharp", "A.cs", "class A {\n  public void Run() {\n  }\n}\n", ["A"]),
    ],
)
def test_other_grammars(language: str, path: str, source: str, symbols: list[str]) -> None:
    assert [s for _, _, s in spans(source, language, path) if s] == symbols


def test_chunk_text_is_redacted() -> None:
    source = 'KEY = "AKIAQ3ZXCV7BNM2LKJ5H"\n'
    (chunk,) = chunk_file("cfg.py", "python", source, Redactor())
    assert "AKIA" not in chunk.text and "«redacted:aws-access-key-id:" in chunk.text


# --- embeddings ---------------------------------------------------------------------------------


async def test_fake_embedder_is_deterministic_and_normalized() -> None:
    first, second = FakeEmbedder(), FakeEmbedder()
    (a,) = await first.embed(["get_user session"])
    (b,) = await second.embed(["get_user session"])
    assert a == b and abs(sum(v * v for v in a) - 1) < 1e-6
    assert tokens("getUserById snake_case HTTP2") == [
        "get",
        "user",
        "by",
        "id",
        "snake",
        "case",
        "http",
        "2",
    ]


def test_vector_round_trip() -> None:
    assert unpack(pack([0.5, -1.0, 2.0])) == [0.5, -1.0, 2.0]


async def test_cache_makes_repeat_embeddings_free(tmp_path: Path) -> None:
    storage = open_local_storage(tmp_path)
    inner = FakeEmbedder()
    cached = CachedEmbedder(inner, storage.cache, batch_size=2)
    first = await cached.embed(["a", "b", "c"])
    assert (cached.provider_calls, inner.calls) == (2, 2)  # batches of 2
    again = CachedEmbedder(FakeEmbedder(), storage.cache)
    assert await again.embed(["c", "a", "b"]) == [first[2], first[0], first[1]]
    assert (again.provider_calls, again.cache_hits) == (0, 3)


# --- index and search on tiny_service -----------------------------------------------------------


@pytest.fixture
async def indexed(
    tiny_service: MaterializedRepo, tmp_path: Path
) -> tuple[Path, CachedEmbedder, MaterializedRepo]:
    snapshot = build_snapshot(tiny_service.root, limits=IngestLimits())
    embedder = CachedEmbedder(FakeEmbedder(), open_local_storage(tmp_path / "data").cache)
    path = tmp_path / "index.sqlite"
    stats = await build_index(path, snapshot, SnippetReader(tiny_service.root), embedder)
    assert stats.chunks > 20 and stats.files > 10 and stats.symbols > stats.chunks / 2
    return path, embedder, tiny_service


KNOWN_QUERIES = [
    ("SQL query built from the name parameter", "app/api/users.py", "search_users"),
    ("CORS origins credentials", "app/main.py", None),
    ("database engine session", "app/db.py", None),
    ("health endpoint", "app/main.py", "health"),
    ("password stored for a new user", "app/api/users.py", "create_user"),
    ("receipt amount formatting", "app/services/notify.py", "send_receipt"),
]


@pytest.mark.parametrize(("query", "path", "symbol"), KNOWN_QUERIES)
async def test_known_queries_hit_top_3(
    indexed: tuple[Path, CachedEmbedder, MaterializedRepo],
    query: str,
    path: str,
    symbol: str | None,
) -> None:
    index, embedder, _ = indexed
    hits = await search(index, query, embedder, top_k=3)
    assert any(h.path == path and (symbol is None or h.symbol == symbol) for h in hits), hits


async def test_reindexing_unchanged_files_makes_zero_embedding_calls(
    indexed: tuple[Path, CachedEmbedder, MaterializedRepo], tmp_path: Path
) -> None:
    _, embedder, repo = indexed
    again = CachedEmbedder(FakeEmbedder(), embedder._cache)  # pyright: ignore[reportPrivateUsage]
    snapshot = build_snapshot(repo.root, limits=IngestLimits())
    await build_index(tmp_path / "again.sqlite", snapshot, SnippetReader(repo.root), again)
    assert again.provider_calls == 0 and again.cache_hits > 0


async def test_modes_and_path_glob(indexed: tuple[Path, CachedEmbedder, MaterializedRepo]) -> None:
    index, embedder, _ = indexed
    bm25 = await search(index, "session", embedder, mode="bm25", top_k=5)
    assert bm25 and all(h.matched_by == ("bm25",) for h in bm25)
    vector = await search(index, "session", embedder, mode="vector", top_k=5)
    assert all(h.matched_by == ("vector",) for h in vector)
    only_tests = await search(index, "user", embedder, path_glob="tests/**", top_k=5)
    assert only_tests and all(h.path.startswith("tests/") for h in only_tests)


async def test_fts_syntax_in_queries_is_harmless(
    indexed: tuple[Path, CachedEmbedder, MaterializedRepo],
) -> None:
    index, embedder, _ = indexed
    assert store.fts_query('x" OR NEAR(') == '"OR" OR "NEAR"'  # operators become plain words
    await search(index, 'users" AND ( NEAR NOT *', embedder, mode="bm25")  # must not raise
    assert await search(index, "!!! ???", embedder, mode="bm25") == []


async def test_index_never_stores_a_secret(
    indexed: tuple[Path, CachedEmbedder, MaterializedRepo],
) -> None:
    index, _, repo = indexed
    data = index.read_bytes()
    assert not any(value.encode() in data for value in repo.secret_values)
    assert store.meta(index)["model"] == "fake-hash-256"
    with sqlite3.connect(index) as conn:
        assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] > 0


# --- symbol definitions (find_symbol, symbol absence probes) -----------------------------------


def defs(text: str, language: str, path: str = "f") -> list[tuple[str, str, int, int]]:
    return [
        (d.qualified, d.kind, d.start_line, d.end_line) for d in symbol_defs(path, language, text)
    ]


def test_symbol_defs_include_nested_and_decorated_python() -> None:
    source = (
        "@app.get('/')\n"  # 1
        "async def root():\n"
        "    def inner():\n"  # 3
        "        pass\n"
        "    return 1\n"  # 5
        "class Svc:\n"
        "    @staticmethod\n"  # 7
        "    def get(x):\n"
        "        return x\n"  # 9
    )
    assert defs(source, "python") == [
        ("root", "function", 1, 5),
        ("root.inner", "function", 3, 4),
        ("Svc", "class", 6, 9),
        ("Svc.get", "method", 7, 9),
    ]


def test_symbol_defs_other_languages() -> None:
    ts = "export class AuthMiddleware {\n  handle(req) { return 1; }\n}\nexport const helper = (a) => a;\n"
    assert defs(ts, "typescript", "a.ts") == [
        ("AuthMiddleware", "class", 1, 3),
        ("AuthMiddleware.handle", "method", 2, 2),
        ("helper", "function", 4, 4),
    ]
    go = "package main\ntype Server struct{}\nfunc (s *Server) Run() {}\n"
    assert defs(go, "go", "m.go") == [("Server", "type", 2, 2), ("Run", "method", 3, 3)]
    java = "class A {\n  A() {}\n  void run() {}\n}\n"
    assert defs(java, "java", "A.java") == [
        ("A", "class", 1, 4),
        ("A.A", "constructor", 2, 2),
        ("A.run", "method", 3, 3),
    ]
    assert defs("x = 1\n", "ruby", "a.rb") == []


async def test_find_symbols_globs_are_case_insensitive(
    indexed: tuple[Path, CachedEmbedder, MaterializedRepo],
) -> None:
    path, _, _ = indexed
    found, total = await find_symbols(path, "*USER*", limit=3)
    assert total > 3 and len(found) == 3
    assert [d.path for d in found] == sorted(d.path for d in found)
    exact, _ = await find_symbols(path, "search_users")
    assert [(d.path, d.kind) for d in exact] == [("app/api/users.py", "function")]
    assert (await find_symbols(path, "NoSuchThing*"))[1] == 0


def test_tree_sitter_is_not_the_broken_release() -> None:
    """0.26.0 corrupts memory reading Point rows > 256 (py-tree-sitter#472); see pyproject."""
    assert version("tree-sitter") != "0.26.0"


def test_large_files_keep_spans_inside_the_file() -> None:
    source = "".join(
        f"@decorator\ndef handler_{i}(value):\n    if value:\n        return {i}\n    return None\n\n\n"
        for i in range(120)
    )  # 840 lines: row numbers well above 256
    total = len(source.splitlines())
    for _ in range(5):
        chunks = chunk_file("big.py", "python", source, NO_REDACTION)
        defs = symbol_defs("big.py", "python", source)
        assert all(1 <= c.start_line <= c.end_line <= total for c in chunks)
        assert len(defs) == 120 and defs[-1].end_line <= total
