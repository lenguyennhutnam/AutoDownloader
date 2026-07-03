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
