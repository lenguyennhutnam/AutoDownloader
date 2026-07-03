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
