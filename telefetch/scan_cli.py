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
