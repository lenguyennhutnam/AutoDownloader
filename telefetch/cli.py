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
