"""Install the pinned scanners into the image (docs/AZURE.md §4; run by the Dockerfile).

Versions and sha256 checksums come from `config/tools.yaml` — the one place they are pinned.
Release binaries are downloaded over https, checked against the recorded checksum before anything
is written, and placed in `<dest>/bin`. Python tools (semgrep, checkov) get one virtualenv each
under `<dest>` so their dependencies can't clash with ArchLens's, and a link in `<dest>/bin`.
Semgrep registry rules are deliberately not fetched: their license forbids redistribution, so the
image carries the engine only. linux/amd64 only.

Usage: python scripts/fetch_scanners.py --dest /opt/scanners [--skip-python]
"""

import argparse
import hashlib
import io
import platform
import subprocess
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from archlens.config import ToolConfig, load_config

TIMEOUT_S = 120


@dataclass(frozen=True)
class Release:
    url: str  # `{v}` is replaced by the version
    member: str | None = None  # file inside a .tar.gz; None → the download is the binary


RELEASES = {
    "gitleaks": Release(
        "https://github.com/gitleaks/gitleaks/releases/download/v{v}/gitleaks_{v}_linux_x64.tar.gz",
        member="gitleaks",
    ),
    "osv-scanner": Release(
        "https://github.com/google/osv-scanner/releases/download/v{v}/osv-scanner_linux_amd64"
    ),
    "hadolint": Release(
        "https://github.com/hadolint/hadolint/releases/download/v{v}/hadolint-linux-x86_64"
    ),
    "actionlint": Release(
        "https://github.com/rhysd/actionlint/releases/download/v{v}/actionlint_{v}_linux_amd64.tar.gz",
        member="actionlint",
    ),
}
PYTHON_TOOLS = ("semgrep", "checkov")


class FetchError(RuntimeError):
    pass


def verified(data: bytes, expected: str | None, name: str) -> bytes:
    """`data` if its sha256 is `expected`; raises FetchError on a mismatch or a missing pin."""
    if not expected:
        raise FetchError(f"{name}: no checksum pinned in config/tools.yaml")
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise FetchError(f"{name}: sha256 {actual} != pinned {expected}")
    return data


def binary(data: bytes, member: str | None, name: str) -> bytes:
    """The executable: the download itself, or `member` from a .tar.gz."""
    if member is None:
        return data
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        try:
            handle = archive.extractfile(member)
        except KeyError:
            handle = None
        if handle is None:
            raise FetchError(f"{name}: {member} not found in the archive")
        return handle.read()


def download(url: str) -> bytes:
    if not url.startswith("https://"):
        raise FetchError(f"refusing a non-https URL: {url}")
    with urllib.request.urlopen(url, timeout=TIMEOUT_S) as response:
        return response.read()


def install_binary(name: str, tool: ToolConfig, bin_dir: Path) -> None:
    release = RELEASES[name]
    url = release.url.format(v=tool.version)
    data = verified(download(url), tool.checksum, name)
    target = bin_dir / name
    target.write_bytes(binary(data, release.member, name))
    target.chmod(0o755)
    print(f"{name} {tool.version}: {url} (sha256 ok)")


def install_python_tool(name: str, tool: ToolConfig, dest: Path) -> None:
    venv = dest / name
    subprocess.run(["uv", "venv", "--quiet", str(venv)], check=True)
    subprocess.run(
        ["uv", "pip", "install", "--quiet", "--python", str(venv / "bin" / "python"),
         f"{name}=={tool.version}"],
        check=True,
    )  # fmt: skip
    link = dest / "bin" / name
    link.unlink(missing_ok=True)
    link.symlink_to(venv / "bin" / name)
    print(f"{name} {tool.version}: {venv}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--skip-python", action="store_true", help="binaries only")
    args = parser.parse_args()
    if platform.machine().lower() not in ("x86_64", "amd64") and not args.skip_python:
        raise FetchError(f"linux/amd64 only, not {platform.machine()}")
    tools = load_config().tools.tools
    bin_dir: Path = args.dest / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in RELEASES:
        install_binary(name, tools[name], bin_dir)
    if not args.skip_python:
        for name in PYTHON_TOOLS:
            install_python_tool(name, tools[name], args.dest)


if __name__ == "__main__":
    main()
