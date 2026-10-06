"""Path globs with the project-wide semantics (RUBRICS.md §1): wcmatch GLOBSTAR | BRACE | DOTGLOB,
matched against repo-relative POSIX paths.
"""

from collections.abc import Sequence

from wcmatch import glob

GLOB_FLAGS = glob.GLOBSTAR | glob.BRACE | glob.DOTGLOB


def glob_match(path: str, patterns: str | Sequence[str]) -> bool:
    """True if `path` matches any of `patterns`."""
    return glob.globmatch(path, patterns, flags=GLOB_FLAGS)
