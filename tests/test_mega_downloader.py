"""Tests for downloaders.mega progress parsing and the stall watchdog."""

import io
import time
from pathlib import Path

import pytest

import downloaders.mega as mega
from downloaders.mega import MegaDownloader, _parse_pct, _stream_progress


# --- progress parsing ---


def test_parse_pct_transferring_line():
    line = "TRANSFERRING ||##########----------||(2048.00/11510.00 MB:  17.80 %)"
    assert _parse_pct(line) == pytest.approx(17.80)


def test_parse_pct_ignores_other_lines():
    assert _parse_pct("Fetching nodes...") is None
    assert _parse_pct("") is None


def test_stream_progress_handles_carriage_returns(monkeypatch: pytest.MonkeyPatch):
    """mega-get rewrites its progress line with \\r — the reader must split on it."""
    logged: list[str] = []
    monkeypatch.setattr(mega.logger, "info", lambda msg: logged.append(msg))
    stream = io.StringIO(
        "TRANSFERRING ||#---||(100.00/1000.00 MB:  10.00 %)\r"
        "TRANSFERRING ||##--||(200.00/1000.00 MB:  20.00 %)\r"
        "TRANSFERRING ||####||(1000.00/1000.00 MB: 100.00 %)\n"
    )
    state = {"last_pct": -1.0}

    _stream_progress(stream, state)

    assert state["last_pct"] == pytest.approx(100.0)
    assert len(logged) == 3  # 10 -> 20 -> 100, all >= 5% steps


# --- stall watchdog ---


class FakePopen:
    """Stand-in for the mega-get process."""

    def __init__(self, returncode: int | None = None):
        self.stdout = io.StringIO("")
        self._rc = returncode
        self.killed = False

    @property
    def returncode(self):
        return self._rc

    def poll(self):
        return self._rc

    def wait(self, timeout=None):
        return self._rc if self._rc is not None else 0

    def kill(self):
        self.killed = True
        self._rc = -9

    def terminate(self):
        self.kill()


def _patch_environment(monkeypatch: pytest.MonkeyPatch, fake: FakePopen):
    monkeypatch.setattr(mega, "_find_mega_get", lambda: "mega-get")
    monkeypatch.setattr(mega, "_find_mega_logout", lambda: None)
    monkeypatch.setattr(mega, "_ensure_mega_server", lambda: None)
    monkeypatch.setattr(mega.subprocess, "Popen", lambda *a, **kw: fake)
    monkeypatch.setattr(mega, "_POLL_SECONDS", 0.01)
    monkeypatch.setitem(mega.settings["Mega"], "StallTimeoutSeconds", 0.05)


def test_stall_watchdog_kills_and_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake = FakePopen(returncode=None)  # never finishes on its own
    _patch_environment(monkeypatch, fake)
    out = tmp_path / "out"
    out.mkdir()
    partial = out / ".getxfer.123.mega"
    partial.write_bytes(b"resume-data")  # pre-existing partial from an earlier run

    start = time.monotonic()
    ok, msg = MegaDownloader().download("https://mega.nz/file/x#k", out)

    assert ok is False
    assert "stalled" in msg.lower()
    assert fake.killed is True
    assert time.monotonic() - start < 5  # watchdog fired, no infinite hang
    assert partial.exists()  # resume data must be preserved on stall


def test_download_success_when_files_appear(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    out = tmp_path / "out"

    class SucceedingPopen(FakePopen):
        def __init__(self):
            super().__init__(returncode=0)
            out.mkdir(exist_ok=True)
            (out / "movie.mp4").write_bytes(b"x" * 100)

    monkeypatch.setattr(mega, "_find_mega_get", lambda: "mega-get")
    monkeypatch.setattr(mega, "_find_mega_logout", lambda: None)
    monkeypatch.setattr(mega, "_ensure_mega_server", lambda: None)
    monkeypatch.setattr(mega.subprocess, "Popen", lambda *a, **kw: SucceedingPopen())
    monkeypatch.setattr(mega, "_POLL_SECONDS", 0.01)
    monkeypatch.setitem(mega.settings["Mega"], "StallTimeoutSeconds", 5)

    ok, msg = MegaDownloader().download("https://mega.nz/file/x#k", out)

    assert ok is True
    assert "movie.mp4" in msg
