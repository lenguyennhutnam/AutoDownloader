"""Tests for telefetch.download_cli."""

from pathlib import Path

import pytest

import telefetch.download_cli as download_cli
from telefetch.config import DownloadConfig
from telefetch.state import LinkStore


def test_parser_defaults():
    args = download_cli.build_parser().parse_args([])
    assert args.skip_failed is False
    assert args.keep_original is False


def test_parser_flags():
    args = download_cli.build_parser().parse_args(["--skip-failed", "--keep-original"])
    assert args.skip_failed is True
    assert args.keep_original is True


class FakeDownloader:
    """Downloader double — writes one small file, no network involved."""

    name = "Fake"

    def check(self, url):
        return True, "ok"

    def download(self, url, output_dir: Path):
        (output_dir / "file.bin").write_bytes(b"data" * 50)
        return True, "ok"


def make_cfg(tmp_path: Path) -> DownloadConfig:
    return DownloadConfig(
        output_dir=tmp_path / "out",
        split_size_bytes=1024**3,
        compression="stored",
        archive_format="zip",
        rar_path="",
        keep_original=False,
    )


def test_process_pending_downloads_all_and_skips_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(download_cli.processor, "get_downloader", lambda url: FakeDownloader())
    store = LinkStore(tmp_path / "state.json")
    a = store.add("https://gofile.io/d/aaa", "GoFile")
    b = store.add("https://gofile.io/d/bbb", "GoFile")
    store.update(b, "done")  # already done -> must be skipped, not re-downloaded

    download_cli.process_pending(store, make_cfg(tmp_path), skip_failed=False)

    assert store.links["https://gofile.io/d/aaa"].status == "done"
    assert len(a.files) == 1


def test_process_pending_skip_failed_excludes_failed_links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls = []
    monkeypatch.setattr(
        download_cli.processor, "get_downloader",
        lambda url: calls.append(url) or FakeDownloader(),
    )
    store = LinkStore(tmp_path / "state.json")
    failed = store.add("https://gofile.io/d/ccc", "GoFile")
    store.update(failed, "failed", error="boom")

    download_cli.process_pending(store, make_cfg(tmp_path), skip_failed=True)

    assert calls == []
    assert store.links["https://gofile.io/d/ccc"].status == "failed"


def test_process_pending_empty_state_does_nothing(tmp_path: Path):
    store = LinkStore(tmp_path / "state.json")
    download_cli.process_pending(store, make_cfg(tmp_path), skip_failed=False)  # must not raise
