"""Tests for downloaders.mega: the stall watchdog and disk-based progress.

mega-get's own stdout progress display was found (empirically, via a raw
capture during a real transfer) to be fully buffered when piped — it
delivers almost nothing in real time and can dump stale output in one
burst on exit. Progress is tracked from bytes written to disk instead
(the same signal the stall watchdog already relies on), and mega-get's
stdout/stderr are discarded entirely (DEVNULL).
"""

import time
from pathlib import Path

import pytest

import downloaders.mega as mega
from downloaders.mega import MegaDownloader, _dir_bytes


def test_dir_bytes_sums_all_files(tmp_path: Path):
    (tmp_path / "a.bin").write_bytes(b"x" * 100)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.bin").write_bytes(b"y" * 50)
    assert _dir_bytes(tmp_path) == 150


class FakePopen:
    """Stand-in for the mega-get process."""

    def __init__(self, returncode: int | None = None):
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


def test_popen_never_pipes_stdout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """mega-get's stdout must be discarded, not captured/parsed."""
    fake = FakePopen(returncode=0)
    calls = []
    monkeypatch.setattr(mega, "_find_mega_get", lambda: "mega-get")
    monkeypatch.setattr(mega, "_find_mega_logout", lambda: None)
    monkeypatch.setattr(mega, "_ensure_mega_server", lambda: None)

    def fake_popen(*args, **kwargs):
        calls.append(kwargs)
        return fake

    monkeypatch.setattr(mega.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(mega, "_POLL_SECONDS", 0.01)
    monkeypatch.setitem(mega.settings["Mega"], "StallTimeoutSeconds", 5)

    out = tmp_path / "out"
    out.mkdir()
    (out / "movie.mp4").write_bytes(b"x" * 100)

    MegaDownloader().download("https://mega.nz/file/x#k", out)

    assert calls[0]["stdout"] is mega.subprocess.DEVNULL
    assert calls[0]["stderr"] is mega.subprocess.DEVNULL


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


def test_progress_report_logs_growth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """While bytes keep growing, a periodic 'downloaded so far' line is logged."""
    out = tmp_path / "out"
    out.mkdir()
    partial = out / ".getxfer.1.mega"
    growth_steps = iter([10, 20, 30, 30, 30])  # last two ticks: no growth, then poll() ends it

    class GrowingPopen(FakePopen):
        def __init__(self):
            super().__init__(returncode=None)
            self._left = 4  # finish after a few polls

        def poll(self):
            partial.write_bytes(b"x" * next(growth_steps, 30))
            self._left -= 1
            if self._left <= 0:
                self._rc = 0
            return self._rc

    logged: list[str] = []
    monkeypatch.setattr(mega.logger, "info", lambda msg: logged.append(msg))
    monkeypatch.setattr(mega, "_find_mega_get", lambda: "mega-get")
    monkeypatch.setattr(mega, "_find_mega_logout", lambda: None)
    monkeypatch.setattr(mega, "_ensure_mega_server", lambda: None)
    monkeypatch.setattr(mega.subprocess, "Popen", lambda *a, **kw: GrowingPopen())
    monkeypatch.setattr(mega, "_POLL_SECONDS", 0.01)
    monkeypatch.setattr(mega, "_REPORT_SECONDS", 0.0)  # report on every growth tick
    monkeypatch.setitem(mega.settings["Mega"], "StallTimeoutSeconds", 5)

    MegaDownloader().download("https://mega.nz/file/x#k", out)

    assert any("Downloaded so far" in msg for msg in logged)
