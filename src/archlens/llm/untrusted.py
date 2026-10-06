"""Delimiting untrusted repository content (docs/LLM.md §3, SECURITY.md §6).

Repository text enters prompts only through `wrap`, inside a `<repo_data>` block whose boundary is
random per run, so content can't close the block early: any literal occurrence of the boundary in
the content (or its source label) is removed first.
"""

import re
import secrets

PLACEHOLDER = "BOUNDARY"
_BOUNDARY = re.compile(r"^b-[0-9a-f]{12}$")


def new_boundary() -> str:
    return f"b-{secrets.token_hex(6)}"


def wrap(text: str, source: str, boundary: str) -> str:
    """`text` from `source` (a path or tool name) as a delimited data block."""
    if not _BOUNDARY.match(boundary):
        raise ValueError(f"invalid boundary: {boundary!r}")
    clean = text.replace(boundary, "")
    label = source.replace(boundary, "").replace('"', "'").replace("\n", " ")
    opening = f'<repo_data boundary="{boundary}" source="{label}">'
    return f'{opening}\n{clean}\n</repo_data boundary="{boundary}">'
