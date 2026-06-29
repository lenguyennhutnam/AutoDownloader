#!/usr/bin/env python3
"""Multi-Link Downloader CLI.

Downloads files from a text file containing GoFile, MEGA, and Google Drive links.
Each link is processed sequentially with pre-download validation.

Usage:
    python downloader.py links.txt
    python downloader.py links.txt --output ./downloads
    python downloader.py links.txt -o E:\\my_downloads --retries 5
"""

import argparse
import sys
import time
from pathlib import Path

from downloaders import get_downloader
from utils import logger
from utils.link_parser import parse_links_file

DEFAULT_OUTPUT = Path("./downloads")
DEFAULT_RETRIES = 3


def resolve_output_path(url: str, output_dir: Path) -> Path:
    """Create a per-link subdirectory inside output_dir to avoid filename collisions.

    Uses the last path segment of the URL as the directory name.
    Falls back to a timestamp-based name if extraction fails.
    """
    try:
        segment = url.rstrip("/").split("/")[-1].split("?")[0].split("#")[0]
        # Remove characters that are invalid in directory names
        safe = "".join(c for c in segment if c.isalnum() or c in "-_.")
        if safe:
            return output_dir / safe
    except Exception:
        pass
    return output_dir / f"link_{int(time.time())}"


def process_link(url: str, index: int, total: int, output_dir: Path, retries: int) -> str:
    """Process a single link. Returns 'success', 'failed', or 'skipped'."""

    logger.link_header(index, total, url)

    # 1. Find matching downloader
    dl = get_downloader(url)
    if dl is None:
        logger.skip("Unsupported link type")
        return "skipped"

    logger.info(f"Type: {dl.name}")

    # 2. Pre-download check
    logger.info("Checking...")
    can_download, check_msg = dl.check(url)
    if not can_download:
        logger.fail(f"Cannot download — {check_msg}")
        return "failed"

    # 3. Download with retries
    link_output = resolve_output_path(url, output_dir)

    for attempt in range(1, retries + 1):
        logger.info(f"Downloading..." + (f" (attempt {attempt}/{retries})" if attempt > 1 else ""))

        success, msg = dl.download(url, link_output)

        if success:
            logger.done(f"→ {msg}")
            return "success"

        if attempt < retries:
            logger.error(f"Attempt {attempt} failed: {msg} — retrying...")
            time.sleep(2)  # Brief pause before retry
        else:
            logger.error(f"Failed after {retries} attempts: {msg}")

    return "failed"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download files from a link list (GoFile, MEGA, Google Drive).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python downloader.py links.txt\n"
            "  python downloader.py links.txt -o E:\\downloads --retries 5\n"
        ),
    )
    parser.add_argument("input_file", help="Text file containing download links (one per line)")
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output directory for downloaded files (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help=f"Number of retries on failure (default: {DEFAULT_RETRIES})",
    )
    args = parser.parse_args()

    # Parse input file
    try:
        links = parse_links_file(args.input_file)
    except (FileNotFoundError, ValueError) as e:
        logger.error(str(e))
        return 1

    # Ensure output directory exists
    output_dir: Path = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Print banner
    logger.header(args.input_file, len(links), str(output_dir))

    # Process each link
    results = {"success": 0, "failed": 0, "skipped": 0}

    try:
        for idx, url in enumerate(links, 1):
            result = process_link(url, idx, len(links), output_dir, args.retries)
            results[result] += 1
    except KeyboardInterrupt:
        logger.error("Stopped by user (Ctrl+C)")

    # Print summary
    logger.summary(results["success"], results["failed"], results["skipped"])

    return 0 if results["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
