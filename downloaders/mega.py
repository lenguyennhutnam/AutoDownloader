"""MEGA downloader — uses MEGAcmd (mega-get) via subprocess.

Cross-platform: finds mega-get binary with shutil.which().
"""

import re
import shutil
import subprocess
import time
from pathlib import Path

from .base import BaseDownloader
from utils import logger
from utils.settings import settings

_MEGA_RE = re.compile(
    r"https?://mega\.nz/(file|folder)/[^\s\"'<>]+", re.IGNORECASE
)

# How often the stall watchdog checks whether bytes are still arriving.
_POLL_SECONDS = 1.0

# How often to log a "downloaded so far" line while a transfer is active.
_REPORT_SECONDS = 15.0


def _dir_bytes(root: Path) -> int:
    """Total size of all files under *root* (transfer temp files included)."""
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue  # file may vanish mid-scan
    return total


def _find_mega_binary(base: str) -> str | None:
    """Locate a MEGAcmd client binary (e.g. 'mega-get') cross-platform."""
    found = shutil.which(base)
    if found:
        return found

    # Fallback: common Windows install location
    home_local = Path.home() / "AppData" / "Local" / "MEGAcmd"
    for candidate in (f"{base}.bat", f"{base}.exe", base):
        path = home_local / candidate
        if path.is_file():
            return str(path)

    return None


def _find_mega_get() -> str | None:
    """Locate the mega-get binary cross-platform."""
    return _find_mega_binary("mega-get")


def _find_mega_logout() -> str | None:
    """Locate mega-logout for session cleanup."""
    return _find_mega_binary("mega-logout")


# Exit codes worth a hand-written explanation. 11 is by far the most
# common in practice; 0xC000013A is Windows' "killed by Ctrl+C/console close".
_KNOWN_EXIT_CODES = {
    11: (
        "Access denied — MEGA transfer quota/rate-limit exceeded for this IP "
        "or the link was taken down; wait a few hours and rerun to retry"
    ),
    3221225786: "terminated by user (Ctrl+C / console closed)",
}


def _describe_exit_code(code: int) -> str:
    """Human-readable explanation for a mega-get exit code.

    Known codes get a curated message; anything else is looked up via the
    mega-errorcode CLI that ships with MEGAcmd.
    """
    if code in _KNOWN_EXIT_CODES:
        return _KNOWN_EXIT_CODES[code]
    tool = _find_mega_binary("mega-errorcode")
    if tool:
        try:
            result = subprocess.run(
                [tool, str(code)], capture_output=True, text=True, timeout=10
            )
            description = (result.stdout or "").strip()
            if description:
                return description
        except Exception:
            pass
    return "unknown error"


def _ensure_mega_server() -> None:
    """Start mega-cmd-server if not already running (Windows)."""
    server = Path.home() / "AppData" / "Local" / "MEGAcmd" / "mega-cmd-server.exe"
    if not server.is_file():
        return
    try:
        subprocess.Popen(
            [str(server)],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(settings["Mega"]["ServerStartupWaitSeconds"])
    except Exception:
        pass


class MegaDownloader(BaseDownloader):
    name = "MEGA"

    @classmethod
    def match(cls, url: str) -> bool:
        return bool(_MEGA_RE.match(url))

    def check(self, url: str) -> tuple[bool, str]:
        mega_get = _find_mega_get()
        if not mega_get:
            return False, (
                "MEGAcmd is not installed. "
                "Download at: https://mega.io/cmd"
            )
        if not _MEGA_RE.match(url):
            return False, "Invalid MEGA URL format"
        return True, "MEGAcmd is ready"

    def download(self, url: str, output_dir: Path) -> tuple[bool, str]:
        mega_get = _find_mega_get()
        if not mega_get:
            return False, "MEGAcmd is not installed"

        output_dir.mkdir(parents=True, exist_ok=True)

        # Fail fast when the disk is nearly full — a 10+ GB transfer that
        # dies at 99% wastes hours of bandwidth and MEGA quota.
        min_free_gb = settings["Mega"]["MinFreeDiskGB"]
        free_gb = shutil.disk_usage(output_dir).free / 1024**3
        if free_gb < min_free_gb:
            return False, (
                f"insufficient disk space: {free_gb:.1f} GB free "
                f"(minimum {min_free_gb} GB) — free up space or change OutputDirPath"
            )

        before = {p.resolve() for p in output_dir.rglob("*") if p.is_file()}

        # Ensure server is running & logout stale sessions
        _ensure_mega_server()
        mega_logout = _find_mega_logout()
        if mega_logout:
            subprocess.run([mega_logout], capture_output=True, text=True)

        stall_timeout = settings["Mega"]["StallTimeoutSeconds"]
        try:
            # mega-get's own progress display is fully buffered when its
            # stdout is piped (not a real console) — empirically verified
            # to deliver almost nothing in real time, then dump stale
            # output in one burst on exit. Discard it entirely and track
            # real progress from bytes actually written to disk instead.
            process = subprocess.Popen(
                [mega_get, url, str(output_dir)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            last_bytes = _dir_bytes(output_dir)
            last_activity = time.monotonic()
            last_report = time.monotonic()
            while process.poll() is None:
                time.sleep(_POLL_SECONDS)
                current = _dir_bytes(output_dir)
                if current != last_bytes:
                    if time.monotonic() - last_report >= _REPORT_SECONDS:
                        logger.info(f"Downloaded so far: {current / 1024**3:.2f} GB")
                        last_report = time.monotonic()
                    last_bytes = current
                    last_activity = time.monotonic()
                elif time.monotonic() - last_activity > stall_timeout:
                    process.kill()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                    # Keep .getxfer resume data so a retry continues the
                    # transfer instead of starting over from zero.
                    return False, (
                        f"transfer stalled: no data received for {stall_timeout}s "
                        "(quota exceeded or connection lost) — partial kept, rerun to resume"
                    )

            if process.returncode != 0:
                self._cleanup_partial(output_dir, before)
                description = _describe_exit_code(process.returncode)
                return False, (
                    f"mega-get exited with code {process.returncode}: {description}"
                )

        except KeyboardInterrupt:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            self._cleanup_partial(output_dir, before)
            raise
        except Exception as e:
            self._cleanup_partial(output_dir, before)
            return False, f"Download error: {e}"

        after = [p for p in output_dir.rglob("*")
                 if p.is_file()
                 and p.resolve() not in before
                 and not p.name.startswith(".getxfer")]
        if not after:
            return False, "mega-get finished but no new files found"

        names = ", ".join(p.name for p in after[:5])
        suffix = f" (+{len(after) - 5} files)" if len(after) > 5 else ""
        return True, f"Downloaded {len(after)} file(s): {names}{suffix}"

    @staticmethod
    def _cleanup_partial(output_dir: Path, before_set: set) -> None:
        """Remove files that appeared during a failed download."""
        for p in output_dir.rglob("*"):
            if p.is_file() and p.resolve() not in before_set:
                try:
                    p.unlink()
                except OSError:
                    pass
