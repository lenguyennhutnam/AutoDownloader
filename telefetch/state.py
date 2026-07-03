"""Per-link download state persisted as JSON with atomic writes."""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LinkState:
    """Lifecycle record for a single discovered link.

    Status flow: pending -> downloading -> compressing -> done | failed.
    """

    url: str
    kind: str
    status: str = "pending"
    attempts: int = 0
    message_id: int | None = None
    discovered_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    files: list[str] = field(default_factory=list)
    error: str | None = None


class LinkStore:
    """In-memory link map backed by a JSON file (atomic replace on save)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.links: dict[str, LinkState] = {}

    def load(self) -> None:
        """Load state from disk; corrupt or missing files yield an empty store."""
        self.links = {}
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        for url, item in raw.get("links", {}).items():
            try:
                self.links[url] = LinkState(**item)
            except TypeError:
                continue  # skip records from incompatible old versions

    def save(self) -> None:
        """Write state atomically (tmp file + replace)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        data = {"links": {url: vars(item) for url, item in sorted(self.links.items())}}
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, url: str, kind: str, message_id: int | None = None) -> LinkState | None:
        """Register a newly discovered URL; return None if already known."""
        if url in self.links:
            return None
        item = LinkState(url=url, kind=kind, message_id=message_id)
        self.links[url] = item
        return item

    def update(self, item: LinkState, status: str, error: str | None = None) -> None:
        """Set status/error, bump updated_at, and persist immediately."""
        item.status = status
        item.updated_at = time.time()
        item.error = error
        self.links[item.url] = item
        self.save()

    def pending(self, include_failed: bool = True) -> list[LinkState]:
        """Links still needing work, oldest discovery first."""
        wanted = {"pending", "downloading", "compressing"}
        if include_failed:
            wanted.add("failed")
        items = [i for i in self.links.values() if i.status in wanted]
        return sorted(items, key=lambda i: i.discovered_at)
