"""MEGA downloader — uses MEGAcmd (mega-get) via subprocess.

Cross-platform: finds mega-get binary with shutil.which().
"""

import re
import shutil
import subprocess
from pathlib import Path

from .base import BaseDownloader

_MEGA_RE = re.compile(
    r"https?://mega\.nz/(file|folder)/[^\s\"'<>]+", re.IGNORECASE
)

# Connection timeout for subprocess (seconds)
_TIMEOUT_SECONDS = 600  # 10 minutes — MEGA files can be large


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
        import time
        time.sleep(3)
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

        try:
            process = subprocess.Popen(
                [mega_get, url, str(output_dir)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            # Stream output for progress info
            last_pct = -1.0
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                if "TRANSFERRING" in line and "%" in line:
                    try:
                        pct = float(line.split("(")[1].split("%")[0].strip().split()[-1])
                        if pct - last_pct >= 5.0:
                            from utils import logger
                            logger.info(f"Progress: {pct:.0f}%")
                            last_pct = pct
                    except Exception:
                        pass

            process.wait()
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
        except subprocess.TimeoutExpired:
            process.kill()
            self._cleanup_partial(output_dir, before)
            return False, f"Timed out after {_TIMEOUT_SECONDS}s"
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
