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
