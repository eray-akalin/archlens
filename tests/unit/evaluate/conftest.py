"""tiny_service with facts, profile, index and repo tools, for evaluator tests."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from archlens.config import ToolsConfig
from archlens.evidence import SnippetReader
from archlens.facts.runner import collect_facts
from archlens.index import build_index
from archlens.index.embed import FakeEmbedder
from archlens.ingest.snapshot import build_snapshot
from archlens.llm.untrusted import new_boundary
from archlens.models import FactSet, IngestLimits, RepoProfile
from archlens.profile import build_profile
from archlens.tools.repo_tools import RepoTools
from tests.fixture_repos import MaterializedRepo


@dataclass
class TinyEnv:
    root: Path
    facts: FactSet
    profile: RepoProfile
    tools: RepoTools


@pytest.fixture
async def tiny_env(tiny_service: MaterializedRepo, tmp_path: Path) -> TinyEnv:
    root = tiny_service.root
    snapshot = build_snapshot(root, limits=IngestLimits())
    found = collect_facts(
        root,
        snapshot,
        workdir=tmp_path / "work",
        tools=ToolsConfig(tools={}),
        tools_dir=tmp_path / "tools",
        scanners=False,
    )
    profile = build_profile(snapshot, found.facts, SnippetReader(root))
    embedder = FakeEmbedder()
    index_path = tmp_path / "index.sqlite"
    await build_index(index_path, snapshot, SnippetReader(root, found.redactor), embedder)
    tools = RepoTools(
        root,
        snapshot.files,
        found.facts,
        boundary=new_boundary(),
        redactor=found.redactor,
        index_path=index_path,
        embedder=embedder,
    )
    return TinyEnv(root, found.facts, profile, tools)
