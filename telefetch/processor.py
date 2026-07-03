"""Process one link end-to-end: download, compress, record state."""

import shutil
from pathlib import Path

from downloaders.registry import get_downloader
from telefetch.archiver import archive_files, archive_files_rar, find_rar_binary, safe_name
from telefetch.config import TelegramConfig
from telefetch.state import LinkState, LinkStore
from utils import logger


def _link_key(url: str) -> str:
    """Derive a stable directory/file stem from the URL's last path segment."""
    return safe_name(url.rstrip("/").rsplit("/", 1)[-1])


def _content_name(work_dir: Path, files: list[Path]) -> str:
    """Pick a human-meaningful archive name from the downloaded content.

    Priority: single file -> its stem; single top-level folder -> folder
    name; multiple loose files -> stem of the largest one.
    """
    if len(files) == 1:
        return safe_name(files[0].stem)
    top = list(work_dir.iterdir())
    if len(top) == 1 and top[0].is_dir():
        return safe_name(top[0].name)
    largest = max(files, key=lambda p: p.stat().st_size)
    return safe_name(largest.stem)


def _make_archives(
    files: list[Path], work_dir: Path, archive_dir: Path, base_name: str, cfg: TelegramConfig
) -> list[Path]:
    """Create archives in the configured format (rar or zip)."""
    if cfg.archive_format == "rar":
        rar_binary = find_rar_binary(cfg.rar_path)
        if rar_binary is None:
            raise RuntimeError(
                "rar binary not found — install WinRAR (Windows) or "
                "`sudo apt install rar` (Ubuntu), or set Telegram.RarPath "
                "in config/setting.json"
            )
        return archive_files_rar(
            files,
            base_dir=work_dir,
            out_dir=archive_dir,
            base_name=base_name,
            limit_bytes=cfg.split_size_bytes,
            compression=cfg.compression,
            rar_binary=rar_binary,
        )
    return archive_files(
        files,
        base_dir=work_dir,
        out_dir=archive_dir,
        base_name=base_name,
        limit_bytes=cfg.split_size_bytes,
        compression=cfg.compression,
        rar_binary=find_rar_binary(cfg.rar_path),  # extract .rar sources when possible
    )


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
        base_name = _content_name(work_dir, files)
        archive_dir = cfg.output_dir / "archives" / base_name
        archives = _make_archives(files, work_dir, archive_dir, base_name, cfg)

        if not cfg.keep_original:
            shutil.rmtree(work_dir, ignore_errors=True)

        item.files = [str(p) for p in archives]
        store.update(item, "done")
        logger.done(f"→ {len(archives)} archive(s) in {archive_dir}")
        return True
    except KeyboardInterrupt:
        # Reset so the next run retries this link, then propagate.
        store.update(item, "pending", "interrupted by user")
        raise
    except Exception as exc:  # noqa: BLE001 — must never break the main loop
        store.update(item, "failed", str(exc))
        logger.fail(f"{exc}")
        return False
