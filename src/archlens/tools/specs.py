"""Arguments and strict tool definitions of the repository tools (docs/LLM.md §4).

Strict function tools require every field: optional ones are nullable here and their defaults are
applied by the tool (`archlens.tools.repo_tools`), so no non-null `default` reaches the schema.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from archlens.llm.types import ToolSpec


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListDirArgs(_Args):
    path: str | None = Field(
        default=None, description="Directory relative to the repository root; null for the root."
    )
    depth: int | None = Field(
        default=None, description="Levels to expand below the directory (1-5); null for 1."
    )


class ReadFileArgs(_Args):
    path: str = Field(description="File path relative to the repository root.")
    start_line: int | None = Field(default=None, description="First line (1-based); null for 1.")
    end_line: int | None = Field(
        default=None, description="Last line (inclusive); null to read up to the per-call limit."
    )


class SearchCodeArgs(_Args):
    query: str = Field(
        description="Keywords/description (hybrid) or a regular expression (regex mode)."
    )
    mode: Literal["hybrid", "regex"] | None = Field(
        default=None, description="hybrid (default): keyword + semantic; regex: pattern match."
    )
    path_glob: str | None = Field(
        default=None, description="Only paths matching this glob, e.g. 'app/**/*.py'."
    )
    top_k: int | None = Field(default=None, description="Number of hits (1-15); null for 8.")


class FindSymbolArgs(_Args):
    name: str = Field(
        description="Symbol name or glob (case-insensitive), plain or qualified: 'get_user', "
        "'*Middleware', 'UserService.*'."
    )


class GetFactsArgs(_Args):
    kind: str = Field(description="Fact kind, e.g. route, ci_step, dependency, sast_finding.")
    path_glob: str | None = Field(
        default=None, description="Only facts about paths matching this glob."
    )
    limit: int | None = Field(default=None, description="Maximum facts (1-100); null for 50.")


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "list_dir",
        "List files and subdirectories of a repository directory with sizes, languages and "
        "flags (binary, generated, vendored, symlink). At most 200 entries.",
        ListDirArgs,
    ),
    ToolSpec(
        "read_file",
        "Read lines of a repository file, line-numbered as `  42│ code`. At most 200 lines and "
        "24 KB per call; continue with start_line for more.",
        ReadFileArgs,
    ),
    ToolSpec(
        "search_code",
        "Search the repository. mode=hybrid: keyword + semantic search over functions/classes "
        "and file windows; mode=regex: regular expression (Python syntax, ^/$ per line, "
        "case-sensitive unless (?i)). Each hit: path, line range, 3-line numbered preview.",
        SearchCodeArgs,
    ),
    ToolSpec(
        "find_symbol",
        "Find definitions (functions, methods, classes, interfaces, ...) by name or glob. "
        "Returns path, line range and kind; read_file shows the code. At most 20.",
        FindSymbolArgs,
    ),
    ToolSpec(
        "get_facts",
        "Deterministic facts extracted from the repository (routes, CI steps, dependencies, "
        "scanner findings, ...) as JSON lines with numbered evidence snippets. At most 100.",
        GetFactsArgs,
    ),
)
