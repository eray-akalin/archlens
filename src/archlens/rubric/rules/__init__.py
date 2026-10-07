"""Built-in deterministic rules, one family per module; importing this package registers them.

Module names follow the family (`ast` lives in `ast_.py` so it can't shadow the stdlib module).
"""

from archlens.rubric.rules import (
    ast_,
    ci,
    complexity,
    data,
    deploy,
    deps,
    docker,
    docs,
    files,
    iac,
    imports,
    observability,
    sast,
    secrets,
    size,
    tests,
    vulns,
)

__all__ = [
    "ast_", "ci", "complexity", "data", "deploy", "deps", "docker", "docs", "files", "iac",
    "imports", "observability", "sast", "secrets", "size", "tests", "vulns",
]  # fmt: skip
