"""scripts/fetch_scanners.py: checksum gate and archive handling (offline, no downloads)."""

import hashlib
import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

from archlens.config import load_config

_SPEC = importlib.util.spec_from_file_location(
    "fetch_scanners", Path(__file__).parents[2] / "scripts" / "fetch_scanners.py"
)
assert _SPEC is not None and _SPEC.loader is not None
fs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fs)


def tarball(name: str, data: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_checksum_gate() -> None:
    data = b"binary"
    good = hashlib.sha256(data).hexdigest()
    assert fs.verified(data, good, "x") == data
    with pytest.raises(fs.FetchError, match="sha256"):
        fs.verified(data, "0" * 64, "x")
    with pytest.raises(fs.FetchError, match="no checksum pinned"):
        fs.verified(data, None, "x")


def test_binary_from_archive_or_download() -> None:
    assert fs.binary(b"raw", None, "x") == b"raw"
    assert fs.binary(tarball("gitleaks", b"elf"), "gitleaks", "gitleaks") == b"elf"
    with pytest.raises(fs.FetchError, match="not found"):
        fs.binary(tarball("other", b"elf"), "gitleaks", "gitleaks")


def test_only_https(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(fs.FetchError, match="non-https"):
        fs.download("http://example.com/x")


def test_install_binary_writes_an_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = (
        load_config()
        .tools.tools["osv-scanner"]
        .model_copy(update={"checksum": hashlib.sha256(b"elf").hexdigest()})
    )
    monkeypatch.setattr(fs, "download", lambda url: b"elf")
    fs.install_binary("osv-scanner", tool, tmp_path)
    target = tmp_path / "osv-scanner"
    assert target.read_bytes() == b"elf" and target.stat().st_mode & 0o111


def test_every_binary_is_pinned() -> None:
    tools = load_config().tools.tools
    for name in fs.RELEASES:
        checksum = tools[name].checksum
        assert checksum is not None and len(checksum) == 64, name
    assert set(fs.PYTHON_TOOLS) <= set(tools)
