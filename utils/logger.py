"""Colored logging and progress display for the CLI.

Uses colorama for cross-platform ANSI support (especially Windows cmd.exe).
"""

import sys
import io

from colorama import Fore, Style, init

# Enable ANSI on Windows cmd.exe
init(autoreset=True)

# Fix Unicode output on Windows consoles with limited codepages
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )


def _write(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def info(msg: str) -> None:
    _write(f"  {Fore.CYAN}[INFO]{Style.RESET_ALL} {msg}")


def done(msg: str) -> None:
    _write(f"  {Fore.GREEN}[DONE]{Style.RESET_ALL} {msg}")


def fail(msg: str) -> None:
    _write(f"  {Fore.RED}[FAIL]{Style.RESET_ALL} {msg}")


def error(msg: str) -> None:
    _write(f"  {Fore.RED}{Style.BRIGHT}[ERROR]{Style.RESET_ALL} {msg}")


def skip(msg: str) -> None:
    _write(f"  {Fore.YELLOW}[SKIP]{Style.RESET_ALL} {msg}")


def header(input_file: str, link_count: int, output_dir: str) -> None:
    """Print the startup banner."""
    border = "=" * 50
    _write(f"\n{border}")
    _write(f"  Multi-Link Downloader")
    _write(f"  Input:  {input_file} ({link_count} links)")
    _write(f"  Output: {output_dir}")
    _write(f"{border}\n")


def link_header(index: int, total: int, url: str) -> None:
    """Print the header for each link being processed."""
    _write(f"\n{Fore.WHITE}{Style.BRIGHT}[{index}/{total}]{Style.RESET_ALL} {url}")


def summary(success: int, failed: int, skipped: int) -> None:
    """Print the final summary table."""
    total = success + failed + skipped
    border = "=" * 50
    sep = "-" * 50
    _write(f"\n{border}")
    _write(f"  SUMMARY")
    _write(f"{sep}")
    _write(f"  {Fore.GREEN}✓ Succeeded:  {success}{Style.RESET_ALL}")
    _write(f"  {Fore.RED}✗ Failed:      {failed}{Style.RESET_ALL}")
    _write(f"  {Fore.YELLOW}⊘ Skipped:     {skipped}{Style.RESET_ALL}")
    _write(f"  Total:         {total}")
    _write(f"{border}")
