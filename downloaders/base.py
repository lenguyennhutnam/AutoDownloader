"""Abstract base class for all downloaders."""

from abc import ABC, abstractmethod
from pathlib import Path


class BaseDownloader(ABC):
    """Base interface that every downloader must implement.

    Subclasses handle a specific hosting service (GoFile, MEGA, Google Drive, etc.).
    """

    name: str  # Human-readable name, e.g. "GoFile", "MEGA", "Google Drive"

    @classmethod
    @abstractmethod
    def match(cls, url: str) -> bool:
        """Return True if *url* belongs to this downloader's service."""
        ...

    @abstractmethod
    def check(self, url: str) -> tuple[bool, str]:
        """Verify that *url* is downloadable without actually downloading.

        Returns:
            (can_download, message) — message explains why it cannot download
            if can_download is False.
        """
        ...

    @abstractmethod
    def download(self, url: str, output_dir: Path) -> tuple[bool, str]:
        """Download file(s) from *url* into *output_dir*.

        Must handle its own exceptions internally and return
        (success, message). Partial / temp files must be cleaned up on failure.

        Returns:
            (success, human-readable result message)
        """
        ...
