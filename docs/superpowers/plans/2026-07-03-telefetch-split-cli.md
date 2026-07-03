# Split telefetch into scan-only and download-only CLIs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `python -m telefetch.scan_cli` (scrape links only, no downloading) and `python -m telefetch.download_cli` (drain pending links from state.json, no Telegram involved) alongside the existing `python -m telefetch` (unchanged), so scraping and downloading can be run as separate sequential steps.

**Architecture:** Split `telefetch/config.py`'s `TelegramConfig` into a credential-free `DownloadConfig` base plus a `TelegramConfig` subclass that adds Telegram-specific fields. `process_link()` is retyped to accept the base `DownloadConfig` (its behavior never touched Telegram fields, so this is a type-only change). The two new CLIs are thin wrappers that reuse existing, already-tested functions (`scan_history`, `listen` from `scraper.py`; `process_link` from `processor.py`) — no changes to `scraper.py`, and `cli.py` is untouched entirely.

**Tech Stack:** Python 3.10+, dataclass inheritance, argparse, pytest.

**Spec:** `docs/superpowers/specs/2026-07-03-telefetch-split-cli-design.md`

## Global Constraints

- Python >= 3.10, Google-style docstrings, English code/comments/log messages (per `CLAUDE.md`).
- `python -m telefetch` (`telefetch/cli.py`) must not change — zero edits to that file.
- `telefetch/scraper.py` must not change — both new CLIs reuse its existing `scan_history`/`listen` as-is.
- `download_cli` must work with **no `.env` file and no `TELEGRAM_API_ID`/`TELEGRAM_API_HASH`** set — it must never call `load_config()` or read `.env`.
- Scan and download are run **sequentially, never concurrently** — no changes needed to `state.json`/`LinkStore` concurrency handling.
- Run tests from project root: `python -m pytest tests/ -v` (venv: `.\venv\Scripts\python.exe` on Windows).
- Commit after each task on branch `feat/telefetch-split-cli`. Conventional commits, English.
- **Never run bare `git commit` after `git add <path>`** if other unrelated files might already be staged — always pass an explicit pathspec to `git commit -- <files>` (this repo currently has unrelated staged changes in `GOME_AUTO/` and `gome_auto.py` that must never be committed by this work).

---

### Task 1: Split config into `DownloadConfig` + `TelegramConfig`

**Files:**
- Modify: `telefetch/config.py` (full rewrite of the dataclass section + add `load_download_config`)
- Modify: `telefetch/processor.py:1-10,33-35,73` (type hints only)
- Test: `tests/test_telefetch_config.py` (add new tests, keep all existing ones passing)

**Interfaces:**
- Consumes: `utils.settings._deep_merge`, `utils.settings.settings` (existing, unchanged).
- Produces:
  - `telefetch.config.DownloadConfig` dataclass: `output_dir: Path`, `split_size_bytes: int`, `compression: str`, `archive_format: str`, `rar_path: str`, `keep_original: bool`
  - `telefetch.config.TelegramConfig(DownloadConfig)` — adds `api_id: int`, `api_hash: str`, `channel: str`, `session_path: Path`
  - `telefetch.config.load_download_config(project_root: Path | None = None, telegram_settings: dict | None = None) -> DownloadConfig` — **no `.env`/credential reading at all**
  - `telefetch.config.load_config(...)` — same signature as today, still returns `TelegramConfig`, still requires credentials
  - `telefetch.config.ConfigError`, `telefetch.config.TELEFETCH_DEFAULTS` — unchanged

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_telefetch_config.py` (append at the end of the file, after `test_defaults_dict_has_pascal_case_keys`):

```python
from telefetch.config import DownloadConfig, load_download_config


def test_load_download_config_no_credentials_needed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    cfg = load_download_config(project_root=tmp_path, telegram_settings={})
    assert isinstance(cfg, DownloadConfig)
    assert cfg.split_size_bytes == 2 * 1024**3
    assert cfg.compression == "stored"
    assert cfg.archive_format == "rar"
    assert cfg.rar_path == ""
    assert cfg.keep_original is False
    assert cfg.output_dir == tmp_path / "telefetch_data"
    assert cfg.output_dir.is_dir()


def test_load_download_config_no_env_file_needed(tmp_path: Path):
    # No .env file exists in tmp_path at all — must not raise or look for one.
    assert not (tmp_path / ".env").exists()
    cfg = load_download_config(project_root=tmp_path, telegram_settings={"SplitSizeGB": 3})
    assert cfg.split_size_bytes == 3 * 1024**3


def test_load_download_config_invalid_compression_raises(tmp_path: Path):
    with pytest.raises(ConfigError, match="Compression"):
        load_download_config(project_root=tmp_path, telegram_settings={"Compression": "lzma"})


def test_load_download_config_invalid_archive_format_raises(tmp_path: Path):
    with pytest.raises(ConfigError, match="ArchiveFormat"):
        load_download_config(project_root=tmp_path, telegram_settings={"ArchiveFormat": "7z"})


def test_telegram_config_is_a_download_config(tmp_path: Path):
    cfg = load_config(project_root=tmp_path, telegram_settings={"Channel": "c"}, env=VALID_ENV)
    assert isinstance(cfg, DownloadConfig)  # inheritance: TelegramConfig IS-A DownloadConfig
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_telefetch_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'DownloadConfig'` / `'load_download_config'`

- [ ] **Step 3: Rewrite `telefetch/config.py`**

Replace the entire file with:

```python
"""Load telefetch configuration: Telegram section of setting.json + .env secrets.

Split into two layers so download-only tooling never needs Telegram
credentials: DownloadConfig covers everything process_link() needs,
TelegramConfig adds the fields only the scraper/combined CLI use.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from utils.settings import _deep_merge, settings

TELEFETCH_DEFAULTS: dict = {
    "Channel": "",
    "OutputDirPath": "./telefetch_data",
    "SplitSizeGB": 2,
    "Compression": "stored",
    "ArchiveFormat": "rar",
    "RarPath": "",
    "KeepOriginal": False,
    "SessionPath": "./telefetch_data/session",
}

_VALID_COMPRESSION = {"stored", "deflated"}
_VALID_FORMATS = {"rar", "zip"}


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


@dataclass
class DownloadConfig:
    """Everything process_link() needs — no Telegram credentials required."""

    output_dir: Path
    split_size_bytes: int
    compression: str
    archive_format: str
    rar_path: str
    keep_original: bool


@dataclass
class TelegramConfig(DownloadConfig):
    """DownloadConfig plus the fields needed to talk to Telegram."""

    api_id: int
    api_hash: str
    channel: str
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


def _merged_settings(telegram_settings: dict | None) -> dict:
    raw = telegram_settings if telegram_settings is not None else settings.get("Telegram", {})
    return _deep_merge(TELEFETCH_DEFAULTS, raw)


def _build_download_config(root: Path, merged: dict) -> DownloadConfig:
    """Validate and resolve the fields DownloadConfig needs from *merged*."""
    if merged["Compression"] not in _VALID_COMPRESSION:
        raise ConfigError(
            f"Telegram.Compression must be one of {sorted(_VALID_COMPRESSION)}, "
            f"got: {merged['Compression']!r}"
        )
    if merged["ArchiveFormat"] not in _VALID_FORMATS:
        raise ConfigError(
            f"Telegram.ArchiveFormat must be one of {sorted(_VALID_FORMATS)}, "
            f"got: {merged['ArchiveFormat']!r}"
        )

    output_dir = _resolve_dir(root, merged["OutputDirPath"])
    output_dir.mkdir(parents=True, exist_ok=True)

    return DownloadConfig(
        output_dir=output_dir,
        split_size_bytes=int(float(merged["SplitSizeGB"]) * 1024**3),
        compression=merged["Compression"],
        archive_format=merged["ArchiveFormat"],
        rar_path=str(merged["RarPath"]),
        keep_original=bool(merged["KeepOriginal"]),
    )


def load_download_config(
    project_root: Path | None = None,
    telegram_settings: dict | None = None,
) -> DownloadConfig:
    """Build a validated DownloadConfig — no Telegram credentials needed.

    Args:
        project_root: Base dir for relative paths. Defaults to the repo root.
        telegram_settings: Override for the "Telegram" section
            (defaults to config/setting.json contents).

    Raises:
        ConfigError: If Compression or ArchiveFormat is invalid.
    """
    root = project_root or Path(__file__).resolve().parent.parent
    merged = _merged_settings(telegram_settings)
    return _build_download_config(root, merged)


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
        ConfigError: If credentials, channel, or compression/format are missing/invalid.
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

    merged = _merged_settings(telegram_settings)
    if not merged["Channel"]:
        raise ConfigError("Telegram.Channel is not set in config/setting.json")

    download_cfg = _build_download_config(root, merged)
    session_path = _resolve_dir(root, merged["SessionPath"])
    session_path.parent.mkdir(parents=True, exist_ok=True)

    return TelegramConfig(
        output_dir=download_cfg.output_dir,
        split_size_bytes=download_cfg.split_size_bytes,
        compression=download_cfg.compression,
        archive_format=download_cfg.archive_format,
        rar_path=download_cfg.rar_path,
        keep_original=download_cfg.keep_original,
        api_id=int(api_id),
        api_hash=api_hash,
        channel=str(merged["Channel"]),
        session_path=session_path,
    )
```

- [ ] **Step 4: Update `telefetch/processor.py` type hints (no behavior change)**

In `telefetch/processor.py`, change the import and two type hints:

```python
# line 8, was: from telefetch.config import TelegramConfig
from telefetch.config import DownloadConfig
```

```python
# line 34, was: base_name: str, cfg: TelegramConfig
def _make_archives(
    files: list[Path], work_dir: Path, archive_dir: Path, base_name: str, cfg: DownloadConfig
) -> list[Path]:
```

```python
# line 73, was: def process_link(store: LinkStore, item: LinkState, cfg: TelegramConfig) -> bool:
def process_link(store: LinkStore, item: LinkState, cfg: DownloadConfig) -> bool:
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all tests pass (existing `TelegramConfig` construction sites are unaffected since `TelegramConfig` still accepts the same keyword arguments — inheritance only changes internals, not the public constructor call shape used in `tests/test_telefetch_processor.py`'s `make_cfg()`)

- [ ] **Step 6: Commit**

```bash
git add telefetch/config.py telefetch/processor.py tests/test_telefetch_config.py
git commit -m "feat: split telefetch config into credential-free DownloadConfig" -- telefetch/config.py telefetch/processor.py tests/test_telefetch_config.py
```

---

### Task 2: `download_cli.py` — download-only entry point

**Files:**
- Create: `telefetch/download_cli.py`
- Test: `tests/test_telefetch_download_cli.py`

**Interfaces:**
- Consumes: `telefetch.config.DownloadConfig`, `telefetch.config.load_download_config` (Task 1); `telefetch.state.LinkStore`, `LinkState` (existing); `telefetch.processor.process_link` (existing, now typed to `DownloadConfig`); `utils.logger` (existing).
- Produces:
  - `telefetch.download_cli.build_parser() -> argparse.ArgumentParser` (flags: `--skip-failed`, `--keep-original`)
  - `telefetch.download_cli.process_pending(store: LinkStore, cfg: DownloadConfig, skip_failed: bool) -> None`
  - `telefetch.download_cli.main() -> int`

- [ ] **Step 1: Write the failing tests**

`tests/test_telefetch_download_cli.py`:

```python
"""Tests for telefetch.download_cli."""

from pathlib import Path

import pytest

import telefetch.download_cli as download_cli
from telefetch.config import DownloadConfig
from telefetch.state import LinkStore


def test_parser_defaults():
    args = download_cli.build_parser().parse_args([])
    assert args.skip_failed is False
    assert args.keep_original is False


def test_parser_flags():
    args = download_cli.build_parser().parse_args(["--skip-failed", "--keep-original"])
    assert args.skip_failed is True
    assert args.keep_original is True


class FakeDownloader:
    """Downloader double — writes one small file, no network involved."""

    name = "Fake"

    def check(self, url):
        return True, "ok"

    def download(self, url, output_dir: Path):
        (output_dir / "file.bin").write_bytes(b"data" * 50)
        return True, "ok"


def make_cfg(tmp_path: Path) -> DownloadConfig:
    return DownloadConfig(
        output_dir=tmp_path / "out",
        split_size_bytes=1024**3,
        compression="stored",
        archive_format="zip",
        rar_path="",
        keep_original=False,
    )


def test_process_pending_downloads_all_and_skips_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(download_cli.processor, "get_downloader", lambda url: FakeDownloader())
    store = LinkStore(tmp_path / "state.json")
    a = store.add("https://gofile.io/d/aaa", "GoFile")
    b = store.add("https://gofile.io/d/bbb", "GoFile")
    store.update(b, "done")  # already done -> must be skipped, not re-downloaded

    download_cli.process_pending(store, make_cfg(tmp_path), skip_failed=False)

    assert store.links["https://gofile.io/d/aaa"].status == "done"
    assert len(a.files) == 1


def test_process_pending_skip_failed_excludes_failed_links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls = []
    monkeypatch.setattr(
        download_cli.processor, "get_downloader",
        lambda url: calls.append(url) or FakeDownloader(),
    )
    store = LinkStore(tmp_path / "state.json")
    failed = store.add("https://gofile.io/d/ccc", "GoFile")
    store.update(failed, "failed", error="boom")

    download_cli.process_pending(store, make_cfg(tmp_path), skip_failed=True)

    assert calls == []
    assert store.links["https://gofile.io/d/ccc"].status == "failed"


def test_process_pending_empty_state_does_nothing(tmp_path: Path):
    store = LinkStore(tmp_path / "state.json")
    download_cli.process_pending(store, make_cfg(tmp_path), skip_failed=False)  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_telefetch_download_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'telefetch.download_cli'`

- [ ] **Step 3: Implement `telefetch/download_cli.py`**

```python
"""CLI wiring for telefetch's download-only entry point.

Reads links already discovered by scan_cli (or the combined `telefetch`
CLI) from state.json and downloads/compresses the pending ones. Never
touches Telegram — no .env, no login required.
"""

import argparse
import sys

from telefetch import processor
from telefetch.config import ConfigError, DownloadConfig, load_download_config
from telefetch.state import LinkStore
from utils import logger


def build_parser() -> argparse.ArgumentParser:
    """Build the download_cli argument parser."""
    parser = argparse.ArgumentParser(
        prog="telefetch.download_cli",
        description="Download and compress links already discovered in state.json.",
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


def process_pending(store: LinkStore, cfg: DownloadConfig, skip_failed: bool) -> None:
    """Download and compress every pending (and optionally failed) link once."""
    pending = store.pending(include_failed=not skip_failed)
    logger.info(f"{len(pending)} link(s) to process")
    for index, item in enumerate(pending, 1):
        logger.link_header(index, len(pending), item.url)
        processor.process_link(store, item, cfg)
    _print_summary(store)


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
        cfg = load_download_config()
    except ConfigError as exc:
        logger.error(str(exc))
        return 2
    if args.keep_original:
        cfg.keep_original = True

    store = LinkStore(cfg.output_dir / "state.json")
    store.load()
    process_pending(store, cfg, args.skip_failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Note: the test monkeypatches `download_cli.processor.get_downloader` — this works because `download_cli.py` imports the `processor` module itself (`from telefetch import processor`) rather than importing `process_link` directly, so patching `processor.get_downloader` affects the same module object `process_link` (inside `processor.py`) reads from.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_telefetch_download_cli.py -v`
Expected: 5 passed

- [ ] **Step 5: Verify the CLI runs standalone without any Telegram env**

Run (from project root, ensure no `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` env vars are set in this shell): `.\venv\Scripts\python.exe -m telefetch.download_cli --help`
Expected: usage text printed, exit code 0 — proves no credential check happens on the download-only path.

- [ ] **Step 6: Commit**

```bash
git add telefetch/download_cli.py tests/test_telefetch_download_cli.py
git commit -m "feat: add telefetch.download_cli standalone download-only entry point" -- telefetch/download_cli.py tests/test_telefetch_download_cli.py
```

---

### Task 3: `scan_cli.py` — scan-only entry point

**Files:**
- Create: `telefetch/scan_cli.py`
- Test: `tests/test_telefetch_scan_cli.py`

**Interfaces:**
- Consumes: `telefetch.config.TelegramConfig`, `telefetch.config.load_config` (existing, unchanged); `telefetch.scraper.scan_history`, `telefetch.scraper.listen` (existing, unchanged); `telefetch.state.LinkStore` (existing); `utils.logger` (existing).
- Produces: `telefetch.scan_cli.build_parser() -> argparse.ArgumentParser` (flag: `--once`); `telefetch.scan_cli.main() -> int`.

- [ ] **Step 1: Write the failing test**

`tests/test_telefetch_scan_cli.py`:

```python
"""Tests for telefetch.scan_cli (argument parsing only — the async Telegram
flow reuses scan_history/listen, which already have their own tests in
tests/test_telefetch_scraper_async.py, matching the convention used by
telefetch/cli.py where _run is not unit tested either)."""

from telefetch.scan_cli import build_parser


def test_parser_defaults():
    args = build_parser().parse_args([])
    assert args.once is False


def test_parser_once_flag():
    args = build_parser().parse_args(["--once"])
    assert args.once is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_telefetch_scan_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'telefetch.scan_cli'`

- [ ] **Step 3: Implement `telefetch/scan_cli.py`**

```python
"""CLI wiring for telefetch's scan-only entry point.

Scans channel history and listens for new messages, registering every
supported link into state.json. Never downloads anything — run
download_cli separately (and afterwards, not concurrently) to fetch files.
"""

import argparse
import asyncio
import sys

from telefetch.config import ConfigError, TelegramConfig, load_config
from telefetch.scraper import listen, scan_history
from telefetch.state import LinkStore
from utils import logger


def build_parser() -> argparse.ArgumentParser:
    """Build the scan_cli argument parser."""
    parser = argparse.ArgumentParser(
        prog="telefetch.scan_cli",
        description=(
            "Scrape download links from a Telegram channel into state.json. "
            "Does not download anything — pair with telefetch.download_cli."
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="scan the backlog then exit (default: keep listening for new messages)",
    )
    return parser


async def _run(args: argparse.Namespace, cfg: TelegramConfig) -> None:
    """Scan history, then optionally listen realtime. Never downloads."""
    from telethon import TelegramClient  # deferred so --help works without telethon

    store = LinkStore(cfg.output_dir / "state.json")
    store.load()

    client = TelegramClient(str(cfg.session_path), cfg.api_id, cfg.api_hash)
    async with client:
        logger.info(f"Scanning channel '{cfg.channel}'...")
        new_count = await scan_history(client, cfg.channel, store)
        logger.info(f"Scan complete: {new_count} new link(s) discovered")

        if args.once:
            return

        logger.info("Listening for new messages... (Ctrl+C to stop)")
        await listen(client, cfg.channel, store, on_new=lambda item: None)


def main() -> int:
    """Entry point. Returns a process exit code."""
    args = build_parser().parse_args()
    try:
        cfg = load_config()
    except ConfigError as exc:
        logger.error(str(exc))
        return 2

    try:
        asyncio.run(_run(args, cfg))
    except KeyboardInterrupt:
        logger.info("Stopped by user")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_telefetch_scan_cli.py -v`
Expected: 2 passed

- [ ] **Step 5: Run the full suite**

Run: `.\venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all tests pass (config +5, download_cli +5, scan_cli +2, plus all pre-existing tests unchanged)

- [ ] **Step 6: Verify `--help` works without Telethon-side-effects**

Run: `.\venv\Scripts\python.exe -m telefetch.scan_cli --help`
Expected: usage text printed, exit code 0

- [ ] **Step 7: Commit**

```bash
git add telefetch/scan_cli.py tests/test_telefetch_scan_cli.py
git commit -m "feat: add telefetch.scan_cli standalone scan-only entry point" -- telefetch/scan_cli.py tests/test_telefetch_scan_cli.py
```

---

### Task 4: Documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the Commands section**

In `CLAUDE.md`, find the block:

```
# Telegram scraper (cần .env với TELEGRAM_API_ID / TELEGRAM_API_HASH)
python -m telefetch              # quét backlog + listen realtime
python -m telefetch --once       # quét backlog rồi thoát
```

Replace it with:

```
# Telegram scraper (cần .env với TELEGRAM_API_ID / TELEGRAM_API_HASH)
python -m telefetch                  # quét backlog + listen realtime + tải (gộp)
python -m telefetch --once           # quét backlog + tải rồi thoát

# Cào và tải tách rời (chạy LẦN LƯỢT, không cùng lúc)
python -m telefetch.scan_cli         # chỉ cào link, ghi vào state.json, không tải
python -m telefetch.scan_cli --once  # cào backlog rồi thoát, không listen
python -m telefetch.download_cli     # chỉ tải link pending trong state.json, KHÔNG cần .env
```

- [ ] **Step 2: Update the File structure section**

In `CLAUDE.md`, find the `telefetch/` block in File structure and replace it with:

```
telefetch/                  # Telegram channel scraper + auto downloader
├── __init__.py
├── __main__.py             # python -m telefetch (gộp cào + tải)
├── cli.py                  # argparse + async main flow (CLI gộp)
├── scan_cli.py             # python -m telefetch.scan_cli (chỉ cào, không tải)
├── download_cli.py         # python -m telefetch.download_cli (chỉ tải, KHÔNG cần .env)
├── config.py               # DownloadConfig (không cần credentials) + TelegramConfig (kế thừa, thêm Telegram fields)
├── state.py                # LinkState + LinkStore (state.json, atomic writes)
├── scraper.py              # extract_links + scan_history + realtime listen
├── processor.py            # download → compress → update state (nhận DownloadConfig)
└── archiver.py             # independent zip/rar parts, chunk splitting
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document split scan_cli/download_cli entry points" -- CLAUDE.md
```

---

### Task 5: End-to-end verification (manual, with user)

**Files:** none

- [ ] **Step 1: Verify download_cli truly needs no Telegram env**

Run in a shell with `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` unset and no `.env` present:
`.\venv\Scripts\python.exe -m telefetch.download_cli --once` (there is no `--once` flag on download_cli — just run `.\venv\Scripts\python.exe -m telefetch.download_cli`)
Expected: runs against the real `telefetch_data/state.json`, processes any pending links, prints a summary — no `ConfigError` about missing credentials.

- [ ] **Step 2: Verify scan_cli in isolation**

Run: `.\venv\Scripts\python.exe -m telefetch.scan_cli --once`
Expected: scans the configured channel, updates `telefetch_data/state.json` with any new links as `pending`, does not create anything under `telefetch_data/archives/` or `telefetch_data/temp/`.

- [ ] **Step 3: Verify the sequential handoff**

After Step 2 leaves new `pending` links in state, run: `.\venv\Scripts\python.exe -m telefetch.download_cli`
Expected: picks up exactly the links `scan_cli` just discovered and downloads them.

- [ ] **Step 4: Report results to user**

Report pass/fail for each step honestly, including full error output on failure.

## Self-Review Notes

- **Spec coverage:** DownloadConfig/TelegramConfig split (Task 1), scan_cli reusing scan_history/listen with no-op on_new (Task 3), download_cli one-shot processing with no credentials (Task 2), docs (Task 4), sequential-only verification (Task 5) — all spec sections covered. Out-of-scope items (concurrent state locking) intentionally excluded.
- **Type consistency checked:** `process_link(store, item, cfg: DownloadConfig)` in Task 1 matches the type used in Task 2's `process_pending`; `TelegramConfig` still used unchanged by untouched `cli.py`/`scan_cli.py`.
- **No placeholders:** all steps contain full code.
