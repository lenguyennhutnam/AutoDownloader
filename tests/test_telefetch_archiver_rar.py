"""Tests for the RAR branch of telefetch.archiver (subprocess is faked)."""

from pathlib import Path

import pytest

import telefetch.archiver as archiver
from telefetch.archiver import archive_files_rar, find_rar_binary

KB = 1024


def write_file(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


class FakeRun:
    """Records rar invocations and fabricates the expected output archive."""

    def __init__(self, returncode: int = 0):
        self.calls: list[dict] = []
        self.returncode = returncode

    def __call__(self, cmd, cwd=None, capture_output=None, text=None):
        self.calls.append({"cmd": [str(c) for c in cmd], "cwd": Path(cwd)})
        # rar writes the archive given right after the "a" action flags
        out = Path([a for a in cmd if str(a).endswith(".rar")][0])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"Rar!fake")

        class Result:
            returncode = self.returncode
            stdout = ""
            stderr = "boom" if self.returncode > 1 else ""

        return Result()


def test_find_rar_binary_prefers_configured(tmp_path: Path):
    fake = tmp_path / "Rar.exe"
    fake.write_bytes(b"")
    assert find_rar_binary(str(fake)) == str(fake)


def test_find_rar_binary_missing_configured_returns_none(tmp_path: Path):
    assert find_rar_binary(str(tmp_path / "nope.exe")) is None


def test_archive_files_rar_single_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_run = FakeRun()
    monkeypatch.setattr(archiver.subprocess, "run", fake_run)
    base = tmp_path / "work"
    files = [write_file(base / "a.bin", KB), write_file(base / "sub" / "b.bin", KB)]
    out = tmp_path / "archives"

    rars = archive_files_rar(
        files, base, out, "GameX", limit_bytes=100 * KB, compression="stored", rar_binary="rar"
    )

    assert [r.name for r in rars] == ["GameX.rar"]
    assert len(fake_run.calls) == 1
    cmd = fake_run.calls[0]["cmd"]
    assert cmd[:2] == ["rar", "a"]
    assert "-m0" in cmd                      # stored -> no compression
    assert fake_run.calls[0]["cwd"] == base  # relative paths preserved in archive
    assert "a.bin" in cmd
    assert str(Path("sub") / "b.bin") in cmd


def test_archive_files_rar_splits_into_parts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_run = FakeRun()
    monkeypatch.setattr(archiver.subprocess, "run", fake_run)
    base = tmp_path / "work"
    files = [write_file(base / f"f{i}.bin", 40 * KB) for i in range(5)]
    out = tmp_path / "archives"

    rars = archive_files_rar(
        files, base, out, "GameX", limit_bytes=100 * KB, compression="deflated", rar_binary="rar"
    )

    # Independent archives, one per size group — NOT rar multi-volume
    assert [r.name for r in rars] == ["GameX.part001.rar", "GameX.part002.rar", "GameX.part003.rar"]
    assert len(fake_run.calls) == 3
    assert all("-m3" in c["cmd"] for c in fake_run.calls)   # deflated -> normal compression
    assert all("-v2g" not in " ".join(c["cmd"]) for c in fake_run.calls)


def test_archive_files_rar_chunks_oversized_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_run = FakeRun()
    monkeypatch.setattr(archiver.subprocess, "run", fake_run)
    base = tmp_path / "work"
    write_file(base / "huge.bin", 250 * KB)
    out = tmp_path / "archives"

    rars = archive_files_rar(
        [base / "huge.bin"], base, out, "huge", limit_bytes=100 * KB, compression="stored",
        rar_binary="rar",
    )

    assert len(rars) == 3  # one independent rar per raw chunk (+ manifest rides along)
    assert not (base / "_staging").exists()  # staging cleaned up


def test_archive_files_rar_extracted_content_uses_staging_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Extracted archive contents are rar'ed from the staging dir so member
    paths inside the archive start at the source archive's stem."""
    import zipfile

    fake_run = FakeRun()
    monkeypatch.setattr(archiver.subprocess, "run", fake_run)
    base = tmp_path / "work"
    base.mkdir(parents=True)
    inner = tmp_path / "payload"
    files = [write_file(inner / f"d{i}.bin", 40 * KB) for i in range(5)]
    big_zip = base / "Game.zip"
    with zipfile.ZipFile(big_zip, "w", compression=zipfile.ZIP_STORED) as zf:
        for f in files:
            zf.write(f, f.name)

    archive_files_rar(
        [big_zip], base, tmp_path / "out", "Game", limit_bytes=100 * KB,
        compression="stored", rar_binary="rar",
    )

    staging = base / "_staging"
    assert all(c["cwd"] == staging for c in fake_run.calls)
    member_args = [a for c in fake_run.calls for a in c["cmd"] if a.endswith(".bin")]
    assert all(a.replace("\\", "/").startswith("Game/") for a in member_args)


def test_archive_files_rar_error_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(archiver.subprocess, "run", FakeRun(returncode=2))
    base = tmp_path / "work"
    write_file(base / "a.bin", KB)

    with pytest.raises(RuntimeError, match="rar exited with code 2"):
        archive_files_rar(
            [base / "a.bin"], base, tmp_path / "out", "x", limit_bytes=100 * KB,
            compression="stored", rar_binary="rar",
        )
