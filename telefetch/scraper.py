"""Discover download links in Telegram messages.

Link support is delegated to the downloaders registry: any URL a
registered downloader matches is collected; everything else is ignored.
"""

import re

from downloaders.registry import get_downloader

_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_TRAILING_PUNCTUATION = ".,;:!?)]}"


def extract_links(text: str | None) -> list[tuple[str, str]]:
    """Extract supported download links from *text*.

    Returns:
        (url, downloader_name) pairs in order of appearance, deduplicated.
    """
    if not text:
        return []
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for url in _URL_RE.findall(text):
        url = url.rstrip(_TRAILING_PUNCTUATION)
        if url in seen:
            continue
        seen.add(url)
        downloader = get_downloader(url)
        if downloader is not None:
            results.append((url, downloader.name))
    return results
