"""Discover download links in Telegram messages.

Link support is delegated to the downloaders registry: any URL a
registered downloader matches is collected; everything else is ignored.
"""

import asyncio
import re
from typing import Callable

from downloaders.registry import get_downloader
from telefetch.state import LinkState, LinkStore
from utils import logger

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


async def scan_history(client, channel: str, store: LinkStore) -> int:
    """Scan the full channel history and register every supported link.

    Args:
        client: Connected Telethon TelegramClient (or compatible double).
        channel: Channel username or ID.
        store: Link store to register discoveries in.

    Returns:
        Number of newly discovered links.
    """
    entity = await client.get_entity(channel)
    new_count = 0
    async for message in client.iter_messages(entity):
        for url, kind in extract_links(message.text):
            if store.add(url, kind, message_id=message.id) is not None:
                new_count += 1
    store.save()
    return new_count


async def listen(
    client,
    channel: str,
    store: LinkStore,
    on_new: Callable[[LinkState], None],
) -> None:
    """Process new channel messages as they arrive, until disconnect.

    Downloads run in a worker thread (asyncio.to_thread) so the Telethon
    event loop keeps receiving updates while a download is in progress.
    """
    from telethon import events  # imported here so tests never need Telethon

    entity = await client.get_entity(channel)

    @client.on(events.NewMessage(chats=entity))
    async def handler(event):
        for url, kind in extract_links(event.text):
            item = store.add(url, kind, message_id=event.id)
            if item is not None:
                logger.info(f"New link discovered: {url}")
                await asyncio.to_thread(on_new, item)

    await client.run_until_disconnected()
