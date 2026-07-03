"""MEGA downloader — uses MEGAcmd (mega-get) via subprocess.

Cross-platform: finds mega-get binary with shutil.which().
"""

import re
import shutil
import subprocess
import threading
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


def _parse_pct(line: str) -> float | None:
    """Extract the percent value from a mega-get TRANSFERRING progress line."""
    if "TRANSFERRING" not in line or "%" not in line:
        return None
    try:
        return float(line.split("(")[1].split("%")[0].strip().split()[-1])
    except (IndexError, ValueError):
        return None


def _stream_progress(stream, state: dict) -> None:
    """Log download progress from mega-get output.

    mega-get redraws its progress line with carriage returns and only
    prints a newline at the end, so a line-based read would block until
    the download finishes — read char-wise and split on both \\r and \\n.
    """
    buffer: list[str] = []
    while True:
        char = stream.read(1)
        if not char:
            break
        if char not in "\r\n":
            buffer.append(char)
            continue
        line = "".join(buffer).strip()
        buffer.clear()
        pct = _parse_pct(line)
        if pct is not None and pct - state["last_pct"] >= 5.0:
            logger.info(f"Progress: {pct:.0f}%")
            state["last_pct"] = pct


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


def _find_mega_get() -> str | None:
    """Locate the mega-get binary cross-platform."""
    found = shutil.which("mega-get")
    if found:
        return found

    # Fallback: common Windows install location
    home_local = Path.home() / "AppData" / "Local" / "MEGAcmd"
    for candidate in ("mega-get.bat", "mega-get.exe", "mega-get"):
        path = home_local / candidate
        if path.is_file():
            return str(path)

    return None


def _find_mega_logout() -> str | None:
    """Locate mega-logout for session cleanup."""
    found = shutil.which("mega-logout")
    if found:
        return found
    home_local = Path.home() / "AppData" / "Local" / "MEGAcmd"
    for candidate in ("mega-logout.bat", "mega-logout.exe", "mega-logout"):
        path = home_local / candidate
        if path.is_file():
            return str(path)
    return None


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
        before = {p.resolve() for p in output_dir.rglob("*") if p.is_file()}

        # Ensure server is running & logout stale sessions
        _ensure_mega_server()
        mega_logout = _find_mega_logout()
        if mega_logout:
            subprocess.run([mega_logout], capture_output=True, text=True)

        stall_timeout = settings["Mega"]["StallTimeoutSeconds"]
        try:
            process = subprocess.Popen(
                [mega_get, url, str(output_dir)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            state = {"last_pct": -1.0}
            reader = threading.Thread(
                target=_stream_progress, args=(process.stdout, state), daemon=True
            )
            reader.start()

            # Stall watchdog: fail the link when no bytes arrive for
            # stall_timeout seconds (quota exceeded, dead connection...)
            # instead of sitting on a frozen transfer forever.
            last_bytes = _dir_bytes(output_dir)
            last_activity = time.monotonic()
            while process.poll() is None:
                time.sleep(_POLL_SECONDS)
                current = _dir_bytes(output_dir)
                if current != last_bytes:
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
                return False, f"mega-get exited with code {process.returncode}"

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
