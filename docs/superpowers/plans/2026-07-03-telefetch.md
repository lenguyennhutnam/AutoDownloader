# telefetch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Telegram channel scraper that discovers download links (GoFile/MEGA/Google Drive), downloads them sequentially with per-link status tracking, compresses everything into independent zip parts (split at a configurable GB threshold), and listens for new messages in realtime.

**Architecture:** New `telefetch/` package reusing the existing `downloaders/` registry (URL dispatch + download) and `utils/logger.py` (colored output). State lives in a JSON file with atomic writes. Telethon handles Telegram; all archive logic is stdlib `zipfile` (cross-platform, no 7-Zip).

**Tech Stack:** Python 3.10+, Telethon, stdlib `zipfile`/`asyncio`/`dataclasses`, pytest.

**Spec:** `docs/superpowers/specs/2026-07-03-telefetch-design.md`

## Global Constraints

- Python >= 3.10 — use `X | Y` unions, `list[Path]` builtins.
- Ubuntu is the primary OS; must also run on Windows. **No hardcoded absolute paths anywhere.**
- All terminal messages, code, comments, docstrings, commit messages: **English**. Docstrings Google style.
- Secrets (`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`) come from `.env` / environment only — never hardcoded, never committed.
- Config keys in `config/setting.json` use **PascalCase**. Defaults: `SplitSizeGB: 1`, `Compression: "stored"`, `KeepOriginal: false`.
- Do NOT modify `gofile-downloader/`, `gome_auto.py`, `GOME_AUTO/`, or anything inside `downloaders/`.
- Every downloader error is caught, logged, marked `failed` in state — the loop always moves to the next link.
- `KeyboardInterrupt` is never swallowed: save state, cleanup, re-raise.
- Run all commands from the project root. Tests: `python -m pytest tests/ -v` (uses `venv`).
- Commit after each task on branch `feat/telefetch`. Conventional commits, English.

---

### Task 1: Scaffolding + config module

**Files:**
- Modify: `requirements.txt`
- Modify: `.gitignore`
- Modify: `config/setting.json`
- Create: `.env.example`
- Create: `telefetch/__init__.py`
- Create: `telefetch/config.py`
- Create: `tests/__init__.py`
- Test: `tests/test_telefetch_config.py`

**Interfaces:**
- Consumes: `utils.settings._deep_merge(base: dict, override: dict) -> dict` (existing).
- Produces:
  - `telefetch.config.ConfigError(Exception)`
  - `telefetch.config.TelegramConfig` dataclass: `api_id: int`, `api_hash: str`, `channel: str`, `output_dir: Path`, `split_size_bytes: int`, `compression: str`, `keep_original: bool`, `session_path: Path`
  - `telefetch.config.load_config(project_root: Path | None = None, telegram_settings: dict | None = None, env: dict[str, str] | None = None) -> TelegramConfig`
  - `telefetch.config.TELEFETCH_DEFAULTS: dict`

- [ ] **Step 1: Update supporting files**

`requirements.txt` — add two lines (telethon approved in spec; pytest is already the documented test runner in CLAUDE.md):

```
requests>=2.31,<3
gdown>=5.1,<6
tqdm>=4.66,<5
colorama>=0.4.6,<1
telethon>=1.36,<2
pytest>=8,<9
```

`.gitignore` — append:

```
.env
telefetch_data/
*.session
*.session-journal
```

`config/setting.json` — add `Telegram` section (full file):

```json
{
    "InputPath": "./inputLinks.txt",
    "OutputDirPath": "./downloads",
    "Retries": 3,
    "RetryDelaySeconds": 2,
    "Mega": {
        "TimeoutSeconds": 600,
        "ServerStartupWaitSeconds": 3
    },
    "Telegram": {
        "Channel": "crabfast",
        "OutputDirPath": "./telefetch_data",
        "SplitSizeGB": 1,
        "Compression": "stored",
        "KeepOriginal": false,
        "SessionPath": "./telefetch_data/session"
    }
}
```

`.env.example`:

```
# Telegram API credentials — get yours at https://my.telegram.org/apps
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
```

`telefetch/__init__.py`:

```python
"""telefetch — Telegram channel link scraper and downloader."""
```

`tests/__init__.py`: empty file.

- [ ] **Step 2: Install dependencies**

Run: `.\venv\Scripts\pip install -r requirements.txt` (Windows) / `venv/bin/pip install -r requirements.txt` (Unix)
Expected: telethon and pytest installed without errors.

- [ ] **Step 3: Write the failing tests**

`tests/test_telefetch_config.py`:

```python
"""Tests for telefetch.config."""

from pathlib import Path

import pytest

from telefetch.config import ConfigError, TELEFETCH_DEFAULTS, load_config

VALID_ENV = {"TELEGRAM_API_ID": "12345", "TELEGRAM_API_HASH": "abcdef"}


def test_load_config_happy_path(tmp_path: Path):
    cfg = load_config(
        project_root=tmp_path,
        telegram_settings={"Channel": "mychannel"},
        env=VALID_ENV,
    )
    assert cfg.api_id == 12345
    assert cfg.api_hash == "abcdef"
    assert cfg.channel == "mychannel"
    assert cfg.split_size_bytes == 1 * 1024**3          # default 1 GB
    assert cfg.compression == "stored"
    assert cfg.keep_original is False
    assert cfg.output_dir == tmp_path / "telefetch_data"
    assert cfg.output_dir.is_dir()                       # created on load
    assert cfg.session_path.parent.is_dir()              # session dir created


def test_split_size_override(tmp_path: Path):
    cfg = load_config(
        project_root=tmp_path,
        telegram_settings={"Channel": "c", "SplitSizeGB": 2.5},
        env=VALID_ENV,
    )
    assert cfg.split_size_bytes == int(2.5 * 1024**3)


def test_missing_credentials_raises(tmp_path: Path):
    with pytest.raises(ConfigError, match="TELEGRAM_API_ID"):
        load_config(project_root=tmp_path, telegram_settings={"Channel": "c"}, env={})


def test_missing_channel_raises(tmp_path: Path):
    with pytest.raises(ConfigError, match="Channel"):
        load_config(project_root=tmp_path, telegram_settings={}, env=VALID_ENV)


def test_invalid_compression_raises(tmp_path: Path):
    with pytest.raises(ConfigError, match="Compression"):
        load_config(
            project_root=tmp_path,
            telegram_settings={"Channel": "c", "Compression": "lzma"},
            env=VALID_ENV,
        )


def test_env_file_parsing(tmp_path: Path):
    (tmp_path / ".env").write_text(
        "# comment\nTELEGRAM_API_ID=999\nTELEGRAM_API_HASH='hash'\n\n", encoding="utf-8"
    )
    cfg = load_config(project_root=tmp_path, telegram_settings={"Channel": "c"}, env=None)
    assert cfg.api_id == 999
    assert cfg.api_hash == "hash"


def test_defaults_dict_has_pascal_case_keys():
    assert set(TELEFETCH_DEFAULTS) == {
        "Channel", "OutputDirPath", "SplitSizeGB", "Compression", "KeepOriginal", "SessionPath",
    }
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `python -m pytest tests/test_telefetch_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'telefetch.config'`

- [ ] **Step 5: Implement `telefetch/config.py`**

```python
"""Load telefetch configuration: Telegram section of setting.json + .env secrets."""

import os
from dataclasses import dataclass
from pathlib import Path

from utils.settings import _deep_merge, settings

TELEFETCH_DEFAULTS: dict = {
    "Channel": "",
    "OutputDirPath": "./telefetch_data",
    "SplitSizeGB": 1,
    "Compression": "stored",
    "KeepOriginal": False,
    "SessionPath": "./telefetch_data/session",
}

_VALID_COMPRESSION = {"stored", "deflated"}


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


@dataclass
class TelegramConfig:
    """Resolved, validated telefetch configuration."""

    api_id: int
    api_hash: str
    channel: str
    output_dir: Path
    split_size_bytes: int
    compression: str
    keep_original: bool
    session_path: Path


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse a minimal KEY=VALUE .env file (comments and blank lines ignored)."""
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip().strip("'\"")
    return result


def _resolve_dir(root: Path, raw: str) -> Path:
    """Resolve *raw* against *root* unless it is already absolute."""
    path = Path(raw)
    return path if path.is_absolute() else root / path


def load_config(
    project_root: Path | None = None,
    telegram_settings: dict | None = None,
    env: dict[str, str] | None = None,
) -> TelegramConfig:
    """Build a validated TelegramConfig.

    Args:
        project_root: Base dir for relative paths and .env lookup.
            Defaults to the repository root.
        telegram_settings: Override for the "Telegram" section
            (defaults to config/setting.json contents).
        env: Override for credential lookup (defaults to .env file
            merged with os.environ, where os.environ wins).

    Raises:
        ConfigError: If credentials, channel, or compression are missing/invalid.
    """
    root = project_root or Path(__file__).resolve().parent.parent
    if env is None:
        env = {**_read_env_file(root / ".env"), **os.environ}

    api_id = env.get("TELEGRAM_API_ID", "").strip()
    api_hash = env.get("TELEGRAM_API_HASH", "").strip()
    if not api_id or not api_hash:
        raise ConfigError(
            "Missing TELEGRAM_API_ID / TELEGRAM_API_HASH. "
            "Copy .env.example to .env and fill in credentials from https://my.telegram.org/apps"
        )
    if not api_id.isdigit():
        raise ConfigError(f"TELEGRAM_API_ID must be an integer, got: {api_id!r}")

    raw = telegram_settings if telegram_settings is not None else settings.get("Telegram", {})
    merged = _deep_merge(TELEFETCH_DEFAULTS, raw)

    if not merged["Channel"]:
        raise ConfigError("Telegram.Channel is not set in config/setting.json")
    if merged["Compression"] not in _VALID_COMPRESSION:
        raise ConfigError(
            f"Telegram.Compression must be one of {sorted(_VALID_COMPRESSION)}, "
            f"got: {merged['Compression']!r}"
        )

    output_dir = _resolve_dir(root, merged["OutputDirPath"])
    session_path = _resolve_dir(root, merged["SessionPath"])
    output_dir.mkdir(parents=True, exist_ok=True)
    session_path.parent.mkdir(parents=True, exist_ok=True)

    return TelegramConfig(
        api_id=int(api_id),
        api_hash=api_hash,
        channel=str(merged["Channel"]),
        output_dir=output_dir,
        split_size_bytes=int(float(merged["SplitSizeGB"]) * 1024**3),
        compression=merged["Compression"],
        keep_original=bool(merged["KeepOriginal"]),
        session_path=session_path,
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_telefetch_config.py -v`
Expected: 7 passed

- [ ] **Step 7: Commit**

```bash
git add requirements.txt .gitignore config/setting.json .env.example telefetch/ tests/
git commit -m "feat: add telefetch config module with .env credential loading"
```

---

### Task 2: State store

**Files:**
- Create: `telefetch/state.py`
- Test: `tests/test_telefetch_state.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `telefetch.state.LinkState` dataclass: `url: str`, `kind: str`, `status: str = "pending"`, `attempts: int = 0`, `message_id: int | None = None`, `discovered_at: float`, `updated_at: float`, `files: list[str]`, `error: str | None = None`
  - `telefetch.state.LinkStore(path: Path)` with: `links: dict[str, LinkState]`, `load() -> None`, `save() -> None`, `add(url: str, kind: str, message_id: int | None = None) -> LinkState | None` (None if URL already known), `update(item: LinkState, status: str, error: str | None = None) -> None` (persists immediately), `pending(include_failed: bool = True) -> list[LinkState]` (ordered by `discovered_at`)

- [ ] **Step 1: Write the failing tests**

`tests/test_telefetch_state.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_telefetch_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'telefetch.state'`

- [ ] **Step 3: Implement `telefetch/state.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_telefetch_state.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add telefetch/state.py tests/test_telefetch_state.py
git commit -m "feat: add telefetch state store with atomic JSON persistence"
```

---

### Task 3: Archiver (zip + split)

**Files:**
- Create: `telefetch/archiver.py`
- Test: `tests/test_telefetch_archiver.py`

**Interfaces:**
- Consumes: nothing from other tasks (pure stdlib).
- Produces:
  - `telefetch.archiver.safe_name(value: str, max_len: int = 120) -> str`
  - `telefetch.archiver.plan_groups(files: list[Path], limit_bytes: int) -> list[list[Path]]`
  - `telefetch.archiver.split_file_to_chunks(file_path: Path, out_dir: Path, limit_bytes: int) -> list[Path]` (chunks + `<name>.MANIFEST.json`, manifest last)
  - `telefetch.archiver.archive_files(files: list[Path], base_dir: Path, out_dir: Path, base_name: str, limit_bytes: int, compression: str) -> list[Path]` (returns created zip paths)

- [ ] **Step 1: Write the failing tests**

`tests/test_telefetch_archiver.py`:

```python
"""Tests for telefetch.archiver."""

import json
import zipfile
from pathlib import Path

from telefetch.archiver import archive_files, plan_groups, safe_name, split_file_to_chunks

KB = 1024


def write_file(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_safe_name_strips_specials():
    assert safe_name("https://mega.nz/file/ab#key!") == "https_mega.nz_file_ab_key"
    assert safe_name("///") == "item"


def test_plan_groups_respects_limit(tmp_path: Path):
    files = [write_file(tmp_path / f"f{i}.bin", 40 * KB) for i in range(5)]
    groups = plan_groups(files, limit_bytes=100 * KB)
    assert [len(g) for g in groups] == [2, 2, 1]


def test_plan_groups_single_group_when_under_limit(tmp_path: Path):
    files = [write_file(tmp_path / f"f{i}.bin", 10 * KB) for i in range(3)]
    assert len(plan_groups(files, limit_bytes=100 * KB)) == 1


def test_split_file_to_chunks_reassembles(tmp_path: Path):
    original = write_file(tmp_path / "big.bin", 250 * KB)
    out = tmp_path / "chunks"
    parts = split_file_to_chunks(original, out, limit_bytes=100 * KB)

    manifest_path = parts[-1]
    assert manifest_path.name.endswith("MANIFEST.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["original_size"] == 250 * KB
    assert manifest["chunk_count"] == 3

    reassembled = b"".join(p.read_bytes() for p in parts[:-1])
    assert reassembled == original.read_bytes()


def test_archive_files_single_zip_when_small(tmp_path: Path):
    base = tmp_path / "work"
    files = [write_file(base / "a.txt", KB), write_file(base / "sub" / "b.txt", KB)]
    out = tmp_path / "archives"

    zips = archive_files(files, base, out, "myitem", limit_bytes=100 * KB, compression="stored")

    assert [z.name for z in zips] == ["myitem.zip"]
    with zipfile.ZipFile(zips[0]) as zf:
        names = set(zf.namelist())
    assert names == {"a.txt", "sub/b.txt"}


def test_archive_files_splits_into_parts(tmp_path: Path):
    base = tmp_path / "work"
    files = [write_file(base / f"f{i}.bin", 40 * KB) for i in range(5)]
    out = tmp_path / "archives"

    zips = archive_files(files, base, out, "myitem", limit_bytes=100 * KB, compression="stored")

    assert [z.name for z in zips] == ["myitem.part001.zip", "myitem.part002.zip", "myitem.part003.zip"]
    # Each part extracts independently and stays within the limit
    for z in zips:
        assert z.stat().st_size <= 100 * KB
        with zipfile.ZipFile(z) as zf:
            assert zf.testzip() is None


def test_archive_files_chunks_oversized_single_file(tmp_path: Path):
    base = tmp_path / "work"
    write_file(base / "huge.bin", 250 * KB)
    out = tmp_path / "archives"

    zips = archive_files(
        [base / "huge.bin"], base, out, "huge", limit_bytes=100 * KB, compression="stored"
    )

    assert len(zips) >= 3  # 3 chunks (+ manifest travels with a part)
    all_members: list[str] = []
    for z in zips:
        with zipfile.ZipFile(z) as zf:
            all_members.extend(zf.namelist())
    assert any(m.endswith("MANIFEST.json") for m in all_members)
    assert sum(1 for m in all_members if ".chunk" in m) == 3
    # staging dir cleaned up
    assert not (base / "_chunks").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_telefetch_archiver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'telefetch.archiver'`

- [ ] **Step 3: Implement `telefetch/archiver.py`**

```python
"""Compress downloaded files into independent zip parts.

Each produced zip extracts on its own — losing one part does not break
the others. Single files larger than the limit are cut into raw chunks
accompanied by a MANIFEST.json describing how to reassemble them
(concatenate chunks in order).
"""

import json
import re
import shutil
import zipfile
from pathlib import Path

_COMPRESSION_MODES = {
    "stored": zipfile.ZIP_STORED,
    "deflated": zipfile.ZIP_DEFLATED,
}

# Reserve headroom for zip headers so a full group still fits the limit.
_ZIP_MARGIN = 64 * 1024
_CHUNK_DIR = "_chunks"


def safe_name(value: str, max_len: int = 120) -> str:
    """Reduce *value* to a filesystem-safe ASCII name."""
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("._")
    return (cleaned or "item")[:max_len]


def _budget(limit_bytes: int) -> int:
    """Usable payload size per part: the limit minus zip-header headroom.

    The fixed margin dominates at real (GB) scale; the 90% floor keeps the
    budget sane for small limits (unit tests use KB-scale limits).
    """
    return max(limit_bytes - _ZIP_MARGIN, int(limit_bytes * 0.9), 1024)


def plan_groups(files: list[Path], limit_bytes: int) -> list[list[Path]]:
    """Group *files* so each group's total size fits within *limit_bytes*."""
    budget = _budget(limit_bytes)
    groups: list[list[Path]] = []
    current: list[Path] = []
    current_size = 0
    for file_path in sorted(files):
        size = file_path.stat().st_size
        if current and current_size + size > budget:
            groups.append(current)
            current, current_size = [], 0
        current.append(file_path)
        current_size += size
    if current:
        groups.append(current)
    return groups


def split_file_to_chunks(file_path: Path, out_dir: Path, limit_bytes: int) -> list[Path]:
    """Cut one oversized file into raw chunks + MANIFEST.json (manifest last)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    budget = _budget(limit_bytes)
    base = safe_name(file_path.name)
    chunks: list[Path] = []
    with open(file_path, "rb") as src:
        part_no = 1
        while True:
            data = src.read(budget)
            if not data:
                break
            chunk = out_dir / f"{base}.chunk{part_no:03d}"
            chunk.write_bytes(data)
            chunks.append(chunk)
            part_no += 1
    manifest = out_dir / f"{base}.MANIFEST.json"
    manifest.write_text(
        json.dumps(
            {
                "original_file": file_path.name,
                "original_size": file_path.stat().st_size,
                "chunk_count": len(chunks),
                "note": "Concatenate chunks in order to reconstruct the original file.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    chunks.append(manifest)
    return chunks


def archive_files(
    files: list[Path],
    base_dir: Path,
    out_dir: Path,
    base_name: str,
    limit_bytes: int,
    compression: str,
) -> list[Path]:
    """Zip *files* into independent parts no larger than *limit_bytes*.

    Args:
        files: Files to pack (must exist).
        base_dir: Root used to compute archive-relative names.
        out_dir: Where zips are written.
        base_name: Zip name stem; parts get a .partNNN suffix when split.
        limit_bytes: Max size per zip.
        compression: "stored" or "deflated".

    Returns:
        Created zip paths, in part order.
    """
    mode = _COMPRESSION_MODES[compression]
    budget = _budget(limit_bytes)
    chunk_root = base_dir / _CHUNK_DIR

    # Oversized single files become raw chunks; the rest pass through.
    prepared: list[Path] = []
    for file_path in files:
        if file_path.stat().st_size > budget:
            prepared.extend(
                split_file_to_chunks(file_path, chunk_root / safe_name(file_path.name), limit_bytes)
            )
        else:
            prepared.append(file_path)

    groups = plan_groups(prepared, limit_bytes)
    out_dir.mkdir(parents=True, exist_ok=True)
    single = len(groups) == 1
    zips: list[Path] = []
    try:
        for index, group in enumerate(groups, 1):
            name = f"{base_name}.zip" if single else f"{base_name}.part{index:03d}.zip"
            zip_path = out_dir / name
            with zipfile.ZipFile(zip_path, "w", compression=mode, allowZip64=True) as zf:
                for file_path in group:
                    if file_path.is_relative_to(base_dir):
                        arcname = file_path.relative_to(base_dir)
                    else:
                        arcname = Path(file_path.name)
                    zf.write(file_path, str(arcname))
            zips.append(zip_path)
    finally:
        shutil.rmtree(chunk_root, ignore_errors=True)
    return zips
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_telefetch_archiver.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add telefetch/archiver.py tests/test_telefetch_archiver.py
git commit -m "feat: add archiver with independent zip parts and chunk splitting"
```

---

### Task 4: Link extraction from message text

**Files:**
- Create: `telefetch/scraper.py` (extraction part only; async Telegram parts come in Task 6)
- Test: `tests/test_telefetch_scraper.py`

**Interfaces:**
- Consumes: `downloaders.registry.get_downloader(url: str) -> BaseDownloader | None`, `.name` attribute on downloaders (existing).
- Produces: `telefetch.scraper.extract_links(text: str | None) -> list[tuple[str, str]]` — `(url, downloader_name)` pairs, deduplicated, order of appearance, unsupported URLs dropped.

- [ ] **Step 1: Write the failing tests**

`tests/test_telefetch_scraper.py`:

```python
"""Tests for telefetch.scraper link extraction."""

from telefetch.scraper import extract_links


def test_extracts_supported_links():
    text = (
        "New game! https://gofile.io/d/Abc123 mirror: "
        "https://mega.nz/file/AAA#secretkey and "
        "https://drive.google.com/file/d/FILE_ID/view"
    )
    result = extract_links(text)
    urls = [u for u, _ in result]
    kinds = [k for _, k in result]
    assert urls == [
        "https://gofile.io/d/Abc123",
        "https://mega.nz/file/AAA#secretkey",
        "https://drive.google.com/file/d/FILE_ID/view",
    ]
    assert kinds == ["GoFile", "MEGA", "Google Drive"]


def test_drops_unsupported_urls():
    assert extract_links("see https://example.com/file.zip please") == []


def test_deduplicates_within_message():
    text = "https://gofile.io/d/abc and again https://gofile.io/d/abc"
    assert len(extract_links(text)) == 1


def test_handles_none_and_empty():
    assert extract_links(None) == []
    assert extract_links("") == []
    assert extract_links("no links here") == []


def test_strips_trailing_punctuation():
    result = extract_links("get it at https://gofile.io/d/abc, thanks")
    assert result == [("https://gofile.io/d/abc", "GoFile")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_telefetch_scraper.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'telefetch.scraper'`

- [ ] **Step 3: Implement extraction in `telefetch/scraper.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_telefetch_scraper.py -v`
Expected: 5 passed
Note: if `test_extracts_supported_links` fails because a downloader's `match()` rejects one of the sample URLs, adjust the sample URL in the test to a form the existing regexes accept (check `downloaders/gofile.py`, `downloaders/mega.py`, `downloaders/gdrive.py`) — do NOT modify `downloaders/`.

- [ ] **Step 5: Commit**

```bash
git add telefetch/scraper.py tests/test_telefetch_scraper.py
git commit -m "feat: add registry-based link extraction from message text"
```

---

### Task 5: Processor (download → compress → state)

**Files:**
- Create: `telefetch/processor.py`
- Test: `tests/test_telefetch_processor.py`

**Interfaces:**
- Consumes:
  - `downloaders.registry.get_downloader(url) -> BaseDownloader | None`; `BaseDownloader.check(url) -> tuple[bool, str]`, `.download(url, output_dir: Path) -> tuple[bool, str]`
  - `telefetch.state.LinkStore`, `LinkState` (Task 2)
  - `telefetch.archiver.archive_files`, `safe_name` (Task 3)
  - `telefetch.config.TelegramConfig` (Task 1)
  - `utils.logger` functions `info/done/fail/error` (existing)
- Produces: `telefetch.processor.process_link(store: LinkStore, item: LinkState, cfg: TelegramConfig) -> bool` — True on success; never raises except `KeyboardInterrupt`.

- [ ] **Step 1: Write the failing tests**

`tests/test_telefetch_processor.py`:

```python
"""Tests for telefetch.processor."""

import zipfile
from pathlib import Path

import pytest

import telefetch.processor as processor
from telefetch.config import TelegramConfig
from telefetch.state import LinkStore


class FakeDownloader:
    """Downloader double that writes files or fails on demand."""

    name = "Fake"

    def __init__(self, payload: dict[str, bytes] | None = None, fail: str | None = None):
        self.payload = payload or {"file.bin": b"data" * 100}
        self.fail = fail

    def check(self, url):
        if self.fail == "check":
            return False, "check failed"
        return True, "ok"

    def download(self, url, output_dir: Path):
        if self.fail == "download":
            return False, "download failed"
        if self.fail == "raise":
            raise RuntimeError("unexpected crash")
        for name, data in self.payload.items():
            target = output_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return True, "downloaded"


def make_cfg(tmp_path: Path, keep_original: bool = False) -> TelegramConfig:
    return TelegramConfig(
        api_id=1,
        api_hash="h",
        channel="c",
        output_dir=tmp_path / "out",
        split_size_bytes=1024**3,
        compression="stored",
        keep_original=keep_original,
        session_path=tmp_path / "out" / "session",
    )


def make_item(tmp_path: Path):
    store = LinkStore(tmp_path / "state.json")
    item = store.add("https://gofile.io/d/abc123", "Fake")
    return store, item


def test_success_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(processor, "get_downloader", lambda url: FakeDownloader())
    store, item = make_item(tmp_path)
    cfg = make_cfg(tmp_path)

    assert processor.process_link(store, item, cfg) is True
    assert item.status == "done"
    assert item.attempts == 1
    assert len(item.files) == 1
    zip_path = Path(item.files[0])
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as zf:
        assert zf.namelist() == ["file.bin"]
    # temp work dir removed after successful archive
    assert not (cfg.output_dir / "temp" / "abc123").exists()


def test_check_failure_marks_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(processor, "get_downloader", lambda url: FakeDownloader(fail="check"))
    store, item = make_item(tmp_path)

    assert processor.process_link(store, item, make_cfg(tmp_path)) is False
    assert item.status == "failed"
    assert "check failed" in item.error


def test_download_failure_marks_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(processor, "get_downloader", lambda url: FakeDownloader(fail="download"))
    store, item = make_item(tmp_path)

    assert processor.process_link(store, item, make_cfg(tmp_path)) is False
    assert item.status == "failed"


def test_unexpected_exception_marks_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(processor, "get_downloader", lambda url: FakeDownloader(fail="raise"))
    store, item = make_item(tmp_path)

    assert processor.process_link(store, item, make_cfg(tmp_path)) is False
    assert item.status == "failed"
    assert "unexpected crash" in item.error


def test_no_downloader_marks_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(processor, "get_downloader", lambda url: None)
    store, item = make_item(tmp_path)

    assert processor.process_link(store, item, make_cfg(tmp_path)) is False
    assert item.status == "failed"


def test_done_item_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls = []
    monkeypatch.setattr(processor, "get_downloader", lambda url: calls.append(url))
    store, item = make_item(tmp_path)
    store.update(item, "done")

    assert processor.process_link(store, item, make_cfg(tmp_path)) is True
    assert calls == []


def test_keep_original_preserves_temp_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(processor, "get_downloader", lambda url: FakeDownloader())
    store, item = make_item(tmp_path)
    cfg = make_cfg(tmp_path, keep_original=True)

    assert processor.process_link(store, item, cfg) is True
    assert (cfg.output_dir / "temp" / "abc123" / "file.bin").is_file()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_telefetch_processor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'telefetch.processor'`

- [ ] **Step 3: Implement `telefetch/processor.py`**

```python
"""Process one link end-to-end: download, compress, record state."""

import shutil
from pathlib import Path

from downloaders.registry import get_downloader
from telefetch.archiver import archive_files, safe_name
from telefetch.config import TelegramConfig
from telefetch.state import LinkState, LinkStore
from utils import logger


def _link_key(url: str) -> str:
    """Derive a stable directory/file stem from the URL's last path segment."""
    return safe_name(url.rstrip("/").rsplit("/", 1)[-1])


def _collect_files(root: Path) -> list[Path]:
    """All regular files under *root*, ignoring transfer temp files."""
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and not p.name.startswith(".getxfer") and p.suffix != ".part"
    )


def process_link(store: LinkStore, item: LinkState, cfg: TelegramConfig) -> bool:
    """Download *item*, compress the result, and update its state.

    Never raises except KeyboardInterrupt (state is reset to pending first
    so the next run retries the link).

    Returns:
        True if the link ended up done, False if it failed.
    """
    if item.status == "done":
        return True

    key = _link_key(item.url)
    work_dir = cfg.output_dir / "temp" / key
    try:
        downloader = get_downloader(item.url)
        if downloader is None:
            store.update(item, "failed", "no downloader matches this URL")
            logger.fail(f"No downloader for {item.url}")
            return False

        item.attempts += 1
        store.update(item, "downloading")
        logger.info(f"Type: {downloader.name}")

        ok, msg = downloader.check(item.url)
        if not ok:
            raise RuntimeError(f"check failed: {msg}")

        work_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Downloading...")
        ok, msg = downloader.download(item.url, work_dir)
        if not ok:
            raise RuntimeError(msg)

        files = _collect_files(work_dir)
        if not files:
            raise RuntimeError("download reported success but produced no files")

        store.update(item, "compressing")
        logger.info(f"Compressing {len(files)} file(s)...")
        archive_dir = cfg.output_dir / "archives" / key
        zips = archive_files(
            files,
            base_dir=work_dir,
            out_dir=archive_dir,
            base_name=key,
            limit_bytes=cfg.split_size_bytes,
            compression=cfg.compression,
        )

        if not cfg.keep_original:
            shutil.rmtree(work_dir, ignore_errors=True)

        item.files = [str(p) for p in zips]
        store.update(item, "done")
        logger.done(f"→ {len(zips)} archive(s) in {archive_dir}")
        return True
    except KeyboardInterrupt:
        # Reset so the next run retries this link, then propagate.
        store.update(item, "pending", "interrupted by user")
        raise
    except Exception as exc:  # noqa: BLE001 — must never break the main loop
        store.update(item, "failed", str(exc))
        logger.fail(f"{exc}")
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_telefetch_processor.py -v`
Expected: 7 passed

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests/ -v`
Expected: all tests pass (config 7, state 7, archiver 7, scraper 5, processor 7)

- [ ] **Step 6: Commit**

```bash
git add telefetch/processor.py tests/test_telefetch_processor.py
git commit -m "feat: add link processor wiring download, archive and state"
```

---

### Task 6: Telegram scraping (history scan + realtime listener)

**Files:**
- Modify: `telefetch/scraper.py` (append async functions)
- Test: `tests/test_telefetch_scraper_async.py`

**Interfaces:**
- Consumes: `telefetch.state.LinkStore` (Task 2), `extract_links` (Task 4), Telethon `events`.
- Produces:
  - `telefetch.scraper.scan_history(client, channel: str, store: LinkStore) -> int` (async; returns count of newly discovered links)
  - `telefetch.scraper.listen(client, channel: str, store: LinkStore, on_new: Callable[[LinkState], None]) -> None` (async; processes each new link via `asyncio.to_thread(on_new, item)`; returns when the client disconnects)

- [ ] **Step 1: Write the failing tests**

`tests/test_telefetch_scraper_async.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_telefetch_scraper_async.py -v`
Expected: FAIL — `ImportError: cannot import name 'scan_history'`

- [ ] **Step 3: Append async functions to `telefetch/scraper.py`**

Add these imports at the top of the file (keep existing ones):

```python
import asyncio
from typing import Callable

from telefetch.state import LinkState, LinkStore
from utils import logger
```

Append after `extract_links`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_telefetch_scraper_async.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add telefetch/scraper.py tests/test_telefetch_scraper_async.py
git commit -m "feat: add telegram history scan and realtime listener"
```

---

### Task 7: CLI entry point + docs

**Files:**
- Create: `telefetch/cli.py`
- Create: `telefetch/__main__.py`
- Modify: `CLAUDE.md` (telefetch section + telethon note)
- Test: `tests/test_telefetch_cli.py`

**Interfaces:**
- Consumes: everything above — `load_config`, `ConfigError`, `LinkStore`, `scan_history`, `listen`, `process_link`, `utils.logger`.
- Produces: `python -m telefetch [--once] [--skip-failed] [--keep-original]`.

- [ ] **Step 1: Write the failing test**

Note: a root-level `telefetch.py` script would shadow the `telefetch/` package, so there is NO root script. The parser lives in `telefetch/cli.py` and the tool runs as `python -m telefetch`.

`tests/test_telefetch_cli.py`:

```python
"""Smoke tests for the telefetch CLI parser."""

from telefetch.cli import build_parser


def test_parser_defaults():
    args = build_parser().parse_args([])
    assert args.once is False
    assert args.skip_failed is False
    assert args.keep_original is False


def test_parser_flags():
    args = build_parser().parse_args(["--once", "--skip-failed", "--keep-original"])
    assert args.once is True
    assert args.skip_failed is True
    assert args.keep_original is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_telefetch_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'telefetch.cli'`

- [ ] **Step 3: Implement `telefetch/cli.py`**

```python
"""CLI wiring for telefetch: argument parsing and the async main flow."""

import argparse
import asyncio
import sys

from telefetch.config import ConfigError, TelegramConfig, load_config
from telefetch.processor import process_link
from telefetch.scraper import listen, scan_history
from telefetch.state import LinkStore
from utils import logger


def build_parser() -> argparse.ArgumentParser:
    """Build the telefetch argument parser."""
    parser = argparse.ArgumentParser(
        prog="telefetch",
        description=(
            "Scrape download links from a Telegram channel, download them "
            "sequentially, and compress results into independent zip parts."
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="process the backlog then exit (default: keep listening for new messages)",
    )
    parser.add_argument(
        "--skip-failed",
        action="store_true",
        help="do not retry links that failed in previous runs",
    )
    parser.add_argument(
        "--keep-original",
        action="store_true",
        help="keep raw downloaded files after compression (overrides config)",
    )
    return parser


async def _run(args: argparse.Namespace, cfg: TelegramConfig) -> None:
    """Scan history, process the backlog, then optionally listen realtime."""
    from telethon import TelegramClient  # deferred so --help works without telethon

    store = LinkStore(cfg.output_dir / "state.json")
    store.load()

    client = TelegramClient(str(cfg.session_path), cfg.api_id, cfg.api_hash)
    async with client:
        logger.info(f"Scanning channel '{cfg.channel}'...")
        new_count = await scan_history(client, cfg.channel, store)
        logger.info(f"Scan complete: {new_count} new link(s) discovered")

        pending = store.pending(include_failed=not args.skip_failed)
        logger.info(f"{len(pending)} link(s) to process")
        for index, item in enumerate(pending, 1):
            logger.link_header(index, len(pending), item.url)
            await asyncio.to_thread(process_link, store, item, cfg)

        if args.once:
            _print_summary(store)
            return

        logger.info("Listening for new messages... (Ctrl+C to stop)")
        await listen(client, cfg.channel, store, lambda item: process_link(store, item, cfg))


def _print_summary(store: LinkStore) -> None:
    """Print the standard summary table from current state."""
    statuses = [item.status for item in store.links.values()]
    logger.summary(
        success=statuses.count("done"),
        failed=statuses.count("failed"),
        skipped=0,
    )


def main() -> int:
    """Entry point. Returns a process exit code."""
    args = build_parser().parse_args()
    try:
        cfg = load_config()
    except ConfigError as exc:
        logger.error(str(exc))
        return 2
    if args.keep_original:
        cfg.keep_original = True

    store = LinkStore(cfg.output_dir / "state.json")
    try:
        asyncio.run(_run(args, cfg))
    except KeyboardInterrupt:
        logger.info("Stopped by user — state saved, rerun to resume")
        store.load()
        _print_summary(store)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

And `telefetch/__main__.py` so `python -m telefetch` works:

```python
"""Allow `python -m telefetch`."""

import sys

from telefetch.cli import main

sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_telefetch_cli.py -v`
Expected: 2 passed

Run: `python -m pytest tests/ -v`
Expected: full suite passes

Run: `python -m telefetch --help`
Expected: usage text with --once, --skip-failed, --keep-original; exit 0

- [ ] **Step 5: Update CLAUDE.md**

In `CLAUDE.md`:

1. In the **Dependencies** section, replace the line
   `> **KHÔNG** dùng telethon trong project mới. Đó là dep của gome_auto.py (file cũ riêng biệt).`
   with:
   `> telethon chỉ dùng trong package telefetch/ (Telegram scraper). Downloader CLI (downloader.py) KHÔNG import telethon.`
   and add `telethon>=1.36,<2` + `pytest>=8,<9` to the requirements listing.
2. In **File structure**, add:

```
telefetch/                 # Telegram channel scraper + auto downloader
├── __init__.py
├── __main__.py             # python -m telefetch
├── cli.py                  # argparse + async main flow
├── config.py               # Telegram section of setting.json + .env secrets
├── state.py                # LinkState + LinkStore (state.json, atomic writes)
├── scraper.py              # extract_links + scan_history + realtime listen
├── processor.py            # download → compress → update state
└── archiver.py             # independent zip parts, chunk splitting (stdlib)
```

3. In **Commands**, add:

```bash
# Telegram scraper (needs .env with TELEGRAM_API_ID / TELEGRAM_API_HASH)
python -m telefetch              # scan backlog + listen realtime
python -m telefetch --once       # scan backlog then exit
```

4. In **Config** section, document the `Telegram` block (copy from `config/setting.json`).

- [ ] **Step 6: Commit**

```bash
git add telefetch/cli.py telefetch/__main__.py tests/test_telefetch_cli.py CLAUDE.md
git commit -m "feat: add telefetch CLI entry point and update docs"
```

---

### Task 8: End-to-end verification (manual, with user)

**Files:** none (verification only)

- [ ] **Step 1: Verify config error path**

Run: `python -m telefetch --once` **without** a `.env` file.
Expected: `[ERROR] Missing TELEGRAM_API_ID / TELEGRAM_API_HASH...` message, exit code 2, no traceback.

- [ ] **Step 2: Real run (requires user's .env + first-time Telethon login)**

Ask the user to create `.env` from `.env.example`. First run prompts for phone + login code (Telethon interactive login) — the user must run this themselves in their terminal:

Run: `python -m telefetch --once`
Expected: scans the configured channel, state file appears at `telefetch_data/state.json` with discovered links, downloads proceed sequentially, archives land in `telefetch_data/archives/<key>/`.

- [ ] **Step 3: Verify realtime mode**

Run: `python -m telefetch` and post (or wait for) a new message with a supported link in the channel.
Expected: link is discovered and downloaded without restarting; Ctrl+C prints "Stopped by user" + summary, exit 130.

- [ ] **Step 4: Report results to user**

Report pass/fail for each step honestly, including full error output on failure.
