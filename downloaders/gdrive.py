"""Google Drive downloader — thin wrapper around gdown."""

import re
from pathlib import Path

from .base import BaseDownloader

_GDRIVE_RE = re.compile(
    r"https?://drive\.google\.com/"
    r"(?:file/d/[a-zA-Z0-9_-]+|(?:uc|open)\?[^\s]*id=[a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)


class GDriveDownloader(BaseDownloader):
    name = "Google Drive"

    @classmethod
    def match(cls, url: str) -> bool:
        return bool(_GDRIVE_RE.match(url))

    def check(self, url: str) -> tuple[bool, str]:
        try:
            import gdown  # noqa: F401
        except ImportError:
            return False, "gdown is not installed. Run: pip install gdown"

        if not _GDRIVE_RE.match(url):
            return False, "Invalid Google Drive URL format"

        return True, "gdown is ready"

    def download(self, url: str, output_dir: Path) -> tuple[bool, str]:
        try:
            import gdown
        except ImportError:
            return False, "gdown is not installed. Run: pip install gdown"

        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            # gdown.download returns the output filepath on success, None on failure
            # fuzzy=True lets gdown parse various Google Drive URL formats
            result = gdown.download(
                url=url,
                output=str(output_dir) + "/",  # trailing slash = use original filename
                quiet=False,
                fuzzy=True,
            )

            if result is None:
                return False, "gdown failed to download (file may be private or deleted)"

            downloaded = Path(result)
            if not downloaded.is_file():
                return False, f"gdown reported success but file not found: {result}"

            size_mb = downloaded.stat().st_size / (1024 * 1024)
            return True, f"Downloaded: {downloaded.name} ({size_mb:.1f} MB)"

        except Exception as e:
            # Cleanup any partial .part files gdown may have left
            for p in output_dir.glob("*.part"):
                try:
                    p.unlink()
                except OSError:
                    pass
            return False, f"Download error: {e}"
