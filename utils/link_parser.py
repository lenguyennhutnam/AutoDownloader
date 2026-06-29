"""Parse a text file containing download links.

Supported format:
    - One URL per line
    - Lines starting with '#' are comments (ignored)
    - Blank lines and leading/trailing whitespace are ignored
"""

from pathlib import Path


def parse_links_file(filepath: str | Path) -> list[str]:
    """Read a text file and return a list of cleaned, non-empty URLs.

    Args:
        filepath: Path to the text file containing links.

    Returns:
        List of URL strings, preserving original order.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file contains no valid links.
    """
    path = Path(filepath)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    links: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        links.append(stripped)

    if not links:
        raise ValueError(f"No valid links found in file: {path}")

    return links
