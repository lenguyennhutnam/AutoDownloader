"""Tests for telefetch.scraper async history scan (fake Telethon client)."""

import asyncio
from pathlib import Path

from telefetch.scraper import scan_history
from telefetch.state import LinkStore


class FakeMessage:
    def __init__(self, msg_id: int, text: str | None):
        self.id = msg_id
        self.text = text


class FakeClient:
    """Mimics the two Telethon methods scan_history uses."""

    def __init__(self, messages: list[FakeMessage]):
        self.messages = messages
        self.requested_channel: str | None = None

    async def get_entity(self, channel: str):
        self.requested_channel = channel
        return object()

    def iter_messages(self, entity):
        async def generator():
            for message in self.messages:
                yield message
        return generator()


def test_scan_history_collects_new_links(tmp_path: Path):
    client = FakeClient([
        FakeMessage(1, "get https://gofile.io/d/abc now"),
        FakeMessage(2, None),
        FakeMessage(3, "dup https://gofile.io/d/abc and https://example.com/x"),
    ])
    store = LinkStore(tmp_path / "state.json")

    new_count = asyncio.run(scan_history(client, "mychannel", store))

    assert client.requested_channel == "mychannel"
    assert new_count == 1
    item = store.links["https://gofile.io/d/abc"]
    assert item.status == "pending"
    assert item.message_id == 1
    assert (tmp_path / "state.json").is_file()  # state persisted after scan


def test_scan_history_skips_known_links(tmp_path: Path):
    store = LinkStore(tmp_path / "state.json")
    store.add("https://gofile.io/d/abc", "GoFile")
    client = FakeClient([FakeMessage(1, "https://gofile.io/d/abc")])

    assert asyncio.run(scan_history(client, "c", store)) == 0
