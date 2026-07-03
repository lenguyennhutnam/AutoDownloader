"""Tests for telefetch.state."""

import json
from pathlib import Path

from telefetch.state import LinkState, LinkStore


def make_store(tmp_path: Path) -> LinkStore:
    return LinkStore(tmp_path / "state.json")


def test_add_new_link(tmp_path: Path):
    store = make_store(tmp_path)
    item = store.add("https://gofile.io/d/abc", "GoFile", message_id=7)
    assert isinstance(item, LinkState)
    assert item.status == "pending"
    assert item.message_id == 7
    assert store.links["https://gofile.io/d/abc"] is item


def test_add_duplicate_returns_none(tmp_path: Path):
    store = make_store(tmp_path)
    store.add("https://gofile.io/d/abc", "GoFile")
    assert store.add("https://gofile.io/d/abc", "GoFile") is None


def test_update_persists_to_disk(tmp_path: Path):
    store = make_store(tmp_path)
    item = store.add("https://gofile.io/d/abc", "GoFile")
    store.update(item, "failed", error="boom")

    raw = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    saved = raw["links"]["https://gofile.io/d/abc"]
    assert saved["status"] == "failed"
    assert saved["error"] == "boom"


def test_load_round_trip(tmp_path: Path):
    store = make_store(tmp_path)
    item = store.add("https://mega.nz/file/x#k", "MEGA")
    store.update(item, "done")

    fresh = make_store(tmp_path)
    fresh.load()
    assert fresh.links["https://mega.nz/file/x#k"].status == "done"


def test_load_missing_file_is_empty(tmp_path: Path):
    store = make_store(tmp_path)
    store.load()
    assert store.links == {}


def test_load_corrupt_file_is_empty(tmp_path: Path):
    (tmp_path / "state.json").write_text("{not json", encoding="utf-8")
    store = make_store(tmp_path)
    store.load()
    assert store.links == {}


def test_pending_excludes_done_and_orders_by_discovery(tmp_path: Path):
    store = make_store(tmp_path)
    a = store.add("https://gofile.io/d/a", "GoFile")
    b = store.add("https://gofile.io/d/b", "GoFile")
    c = store.add("https://gofile.io/d/c", "GoFile")
    a.discovered_at, b.discovered_at, c.discovered_at = 3.0, 1.0, 2.0
    store.update(c, "done")
    store.update(b, "failed", error="x")

    assert [i.url for i in store.pending()] == ["https://gofile.io/d/b", "https://gofile.io/d/a"]
    assert [i.url for i in store.pending(include_failed=False)] == ["https://gofile.io/d/a"]
