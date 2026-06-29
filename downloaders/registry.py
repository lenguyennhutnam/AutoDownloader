"""Registry — dispatches a URL to the correct downloader instance."""

from .base import BaseDownloader
from .gdrive import GDriveDownloader
from .gofile import GoFileDownloader
from .mega import MegaDownloader

# Order matters: first match wins
DOWNLOADERS: list[type[BaseDownloader]] = [
    GoFileDownloader,
    MegaDownloader,
    GDriveDownloader,
]


def get_downloader(url: str) -> BaseDownloader | None:
    """Return an appropriate downloader for *url*, or None if unsupported."""
    for cls in DOWNLOADERS:
        if cls.match(url):
            return cls()
    return None
