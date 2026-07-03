"""Tests for telefetch.processor."""

import zipfile
from pathlib import Path

import pytest

import telefetch.processor as processor
from telefetch.config import TelegramConfig
from telefetch.state import LinkStore


class FakeDownloader:
    """Downloader double that writes files or fails on demand."""

    name = "Fake"

    def __init__(self, payload: dict[str, bytes] | None = None, fail: str | None = None):
        self.payload = payload or {"file.bin": b"data" * 100}
        self.fail = fail

    def check(self, url):
        if self.fail == "check":
            return False, "check failed"
        return True, "ok"

    def download(self, url, output_dir: Path):
        if self.fail == "download":
            return False, "download failed"
        if self.fail == "raise":
            raise RuntimeError("unexpected crash")
        for name, data in self.payload.items():
            target = output_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return True, "downloaded"


def make_cfg(
    tmp_path: Path, keep_original: bool = False, archive_format: str = "zip"
) -> TelegramConfig:
    return TelegramConfig(
        api_id=1,
        api_hash="h",
        channel="c",
        output_dir=tmp_path / "out",
        split_size_bytes=1024**3,
        compression="stored",
        archive_format=archive_format,
        rar_path="",
        keep_original=keep_original,
        session_path=tmp_path / "out" / "session",
    )


def make_item(tmp_path: Path):
    store = LinkStore(tmp_path / "state.json")
    item = store.add("https://gofile.io/d/abc123", "Fake")
    return store, item


def test_success_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(processor, "get_downloader", lambda url: FakeDownloader())
    store, item = make_item(tmp_path)
    cfg = make_cfg(tmp_path)

    assert processor.process_link(store, item, cfg) is True
    assert item.status == "done"
    assert item.attempts == 1
    assert len(item.files) == 1
    zip_path = Path(item.files[0])
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as zf:
        assert zf.namelist() == ["file.bin"]
    # temp work dir removed after successful archive
    assert not (cfg.output_dir / "temp" / "abc123").exists()


def test_check_failure_marks_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(processor, "get_downloader", lambda url: FakeDownloader(fail="check"))
    store, item = make_item(tmp_path)

    assert processor.process_link(store, item, make_cfg(tmp_path)) is False
    assert item.status == "failed"
    assert "check failed" in item.error


def test_download_failure_marks_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(processor, "get_downloader", lambda url: FakeDownloader(fail="download"))
    store, item = make_item(tmp_path)

    assert processor.process_link(store, item, make_cfg(tmp_path)) is False
    assert item.status == "failed"


def test_unexpected_exception_marks_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(processor, "get_downloader", lambda url: FakeDownloader(fail="raise"))
    store, item = make_item(tmp_path)

    assert processor.process_link(store, item, make_cfg(tmp_path)) is False
    assert item.status == "failed"
    assert "unexpected crash" in item.error


def test_no_downloader_marks_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(processor, "get_downloader", lambda url: None)
    store, item = make_item(tmp_path)

    assert processor.process_link(store, item, make_cfg(tmp_path)) is False
    assert item.status == "failed"


def test_done_item_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls = []
    monkeypatch.setattr(processor, "get_downloader", lambda url: calls.append(url))
    store, item = make_item(tmp_path)
    store.update(item, "done")

    assert processor.process_link(store, item, make_cfg(tmp_path)) is True
    assert calls == []


def test_keep_original_preserves_temp_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(processor, "get_downloader", lambda url: FakeDownloader())
    store, item = make_item(tmp_path)
    cfg = make_cfg(tmp_path, keep_original=True)

    assert processor.process_link(store, item, cfg) is True
    assert (cfg.output_dir / "temp" / "abc123" / "file.bin").is_file()


# --- archive naming from downloaded content ---


def test_archive_named_after_single_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    payload = {"GameX_v1.2.zip": b"data" * 100}
    monkeypatch.setattr(processor, "get_downloader", lambda url: FakeDownloader(payload))
    store, item = make_item(tmp_path)

    assert processor.process_link(store, item, make_cfg(tmp_path)) is True
    assert Path(item.files[0]).name == "GameX_v1.2.zip"  # file stem + format ext


def test_archive_named_after_single_top_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    payload = {
        "My Game Folder/setup.exe": b"a" * 100,
        "My Game Folder/data/game.pak": b"b" * 100,
    }
    monkeypatch.setattr(processor, "get_downloader", lambda url: FakeDownloader(payload))
    store, item = make_item(tmp_path)

    assert processor.process_link(store, item, make_cfg(tmp_path)) is True
    assert Path(item.files[0]).name == "My_Game_Folder.zip"


def test_archive_named_after_largest_loose_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    payload = {"readme.txt": b"a" * 10, "BigGame.iso": b"b" * 5000}
    monkeypatch.setattr(processor, "get_downloader", lambda url: FakeDownloader(payload))
    store, item = make_item(tmp_path)

    assert processor.process_link(store, item, make_cfg(tmp_path)) is True
    assert Path(item.files[0]).name == "BigGame.zip"


# --- rar format dispatch ---


def test_rar_format_uses_rar_archiver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(processor, "get_downloader", lambda url: FakeDownloader())
    monkeypatch.setattr(processor, "find_rar_binary", lambda configured: "C:/fake/Rar.exe")
    calls = {}

    def fake_rar(files, base_dir, out_dir, base_name, limit_bytes, compression, rar_binary):
        calls["base_name"] = base_name
        calls["rar_binary"] = rar_binary
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{base_name}.rar"
        out.write_bytes(b"Rar!")
        return [out]

    monkeypatch.setattr(processor, "archive_files_rar", fake_rar)
    store, item = make_item(tmp_path)

    assert processor.process_link(store, item, make_cfg(tmp_path, archive_format="rar")) is True
    assert item.status == "done"
    assert calls["rar_binary"] == "C:/fake/Rar.exe"
    assert Path(item.files[0]).name == "file.rar"  # single file payload -> its stem


def test_rar_missing_binary_marks_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(processor, "get_downloader", lambda url: FakeDownloader())
    monkeypatch.setattr(processor, "find_rar_binary", lambda configured: None)
    store, item = make_item(tmp_path)

    assert processor.process_link(store, item, make_cfg(tmp_path, archive_format="rar")) is False
    assert item.status == "failed"
    assert "rar" in item.error.lower()
    assert "WinRAR" in item.error  # install hint present
