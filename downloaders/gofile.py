"""GoFile downloader — reuses the existing gofile-downloader module."""

import importlib.util
import re
from pathlib import Path
from typing import Any

from .base import BaseDownloader

_GOFILE_RE = re.compile(r"https?://(?:www\.)?gofile\.io/d/\w+", re.IGNORECASE)


def _load_gofile_manager() -> Any:
    """Dynamically import the Manager class from gofile-downloader.py."""
    script = Path(__file__).resolve().parent.parent / "gofile-downloader" / "gofile-downloader.py"
    spec = importlib.util.spec_from_file_location("gofile_downloader", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load GoFile downloader from {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Manager


class GoFileDownloader(BaseDownloader):
    name = "GoFile"

    @classmethod
    def match(cls, url: str) -> bool:
        return bool(_GOFILE_RE.match(url))

    def check(self, url: str) -> tuple[bool, str]:
        # GoFile API requires account token + website token just to query content.
        # Rather than duplicating that logic, we let download() handle errors.
        # check() only validates URL format here.
        if not _GOFILE_RE.match(url):
            return False, "Invalid GoFile URL format"
        return True, "Valid URL"

    def download(self, url: str, output_dir: Path) -> tuple[bool, str]:
        try:
            ManagerCls = _load_gofile_manager()
        except RuntimeError as e:
            return False, f"Failed to load GoFile downloader: {e}"

        output_dir.mkdir(parents=True, exist_ok=True)
        before = {p.resolve() for p in output_dir.rglob("*") if p.is_file()}

        try:
            manager = ManagerCls(url_or_file=url, password=None)
            manager._root_dir = str(output_dir)
            manager.run()
            # Manager hijacks SIGINT with its own handler — if stop_event is set,
            # the user pressed Ctrl+C; re-raise so the main loop can exit cleanly.
            if manager._stop_event.is_set():
                raise KeyboardInterrupt
        except KeyboardInterrupt:
            self._cleanup_partial(output_dir, before)
            raise
        except SystemExit:
            # gofile-downloader calls sys.exit on some errors — catch it
            pass
        except Exception as e:
            # Cleanup partial files
            self._cleanup_partial(output_dir, before)
            return False, f"Download error: {e}"

        after = [p for p in output_dir.rglob("*") if p.is_file() and p.resolve() not in before]
        if not after:
            return False, "Download completed but no new files found"

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
        # Remove empty subdirectories left behind
        for d in sorted(output_dir.rglob("*"), reverse=True):
            if d.is_dir():
                try:
                    d.rmdir()
                except OSError:
                    pass
