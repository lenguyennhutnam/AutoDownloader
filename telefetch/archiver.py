"""Compress downloaded files into independent archive parts (zip or rar).

Each produced archive extracts on its own — losing one part does not
break the others (this is NOT rar/zip multi-volume). Oversized files that
are themselves archives (.zip/.rar/.7z) are extracted first so their
CONTENTS are packed as complete, directly usable files. Only oversized
files that cannot be extracted (e.g. .iso, .mp4, corrupt archives) fall
back to raw chunks + a MANIFEST.json describing how to reassemble them
(concatenate chunks in order).

The rar branch shells out to the WinRAR/rar CLI, which must be installed
separately (Windows: WinRAR; Ubuntu: `sudo apt install rar`).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

_COMPRESSION_MODES = {
    "stored": zipfile.ZIP_STORED,
    "deflated": zipfile.ZIP_DEFLATED,
}

# rar -m0 = store, -m3 = normal compression
_RAR_COMPRESSION_FLAGS = {
    "stored": "-m0",
    "deflated": "-m3",
}

# Reserve headroom for archive headers so a full group still fits the limit.
_ZIP_MARGIN = 64 * 1024
_STAGING_DIR = "_staging"

_ARCHIVE_EXTS = {".zip", ".rar", ".7z"}

# Well-known WinRAR install locations checked when `rar` is not on PATH.
_WINDOWS_RAR_CANDIDATES = (
    Path("C:/Program Files/WinRAR/Rar.exe"),
    Path("C:/Program Files (x86)/WinRAR/Rar.exe"),
)


def find_rar_binary(configured: str = "") -> str | None:
    """Locate the rar CLI binary, or None if unavailable.

    Args:
        configured: Explicit path from config (wins when set; None is
            returned if it does not exist so a config typo is not
            silently papered over by a PATH lookup).
    """
    if configured:
        path = Path(configured)
        return str(path) if path.is_file() else None
    found = shutil.which("rar")
    if found:
        return found
    if sys.platform == "win32":
        for candidate in _WINDOWS_RAR_CANDIDATES:
            if candidate.is_file():
                return str(candidate)
    return None


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


def is_archive(file_path: Path) -> bool:
    """True if the file looks like an archive we know how to extract."""
    return file_path.suffix.lower() in _ARCHIVE_EXTS


def extract_archive(file_path: Path, out_dir: Path, rar_binary: str | None = None) -> bool:
    """Extract an archive into *out_dir*; False when it cannot be extracted.

    zip uses the stdlib; rar needs *rar_binary*; 7z needs a `7z` on PATH.
    A False return (unknown format, missing tool, corrupt file, empty
    result) tells the caller to fall back to raw chunking.
    """
    suffix = file_path.suffix.lower()
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        if suffix == ".zip":
            with zipfile.ZipFile(file_path) as zf:
                zf.extractall(out_dir)
        elif suffix == ".rar" and rar_binary:
            result = subprocess.run(
                [rar_binary, "x", "-idq", "-y", str(file_path), str(out_dir) + os.sep],
                capture_output=True,
                text=True,
            )
            if result.returncode > 1:
                return False
        elif suffix == ".7z":
            seven_zip = shutil.which("7z")
            if not seven_zip:
                return False
            result = subprocess.run(
                [seven_zip, "x", "-y", f"-o{out_dir}", str(file_path)],
                capture_output=True,
                text=True,
            )
            if result.returncode not in (0, 1):
                return False
        else:
            return False
    except Exception:
        return False
    return any(p.is_file() for p in out_dir.rglob("*"))


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
    rar_binary: str | None = None,
) -> list[Path]:
    """Zip *files* into independent parts no larger than *limit_bytes*.

    Args:
        files: Files to pack (must exist).
        base_dir: Root used to compute archive-relative names.
        out_dir: Where zips are written.
        base_name: Zip name stem; parts get a .partNNN suffix when split.
        limit_bytes: Max size per zip.
        compression: "stored" or "deflated".
        rar_binary: Optional rar CLI path — lets oversized .rar downloads
            be extracted instead of chunked.

    Returns:
        Created zip paths, in part order.
    """
    mode = _COMPRESSION_MODES[compression]
    groups, staging_root = _prepare_groups(files, base_dir, limit_bytes, rar_binary)
    out_dir.mkdir(parents=True, exist_ok=True)
    single = len(groups) == 1
    zips: list[Path] = []
    try:
        for index, group in enumerate(groups, 1):
            name = f"{base_name}.zip" if single else f"{base_name}.part{index:03d}.zip"
            zip_path = out_dir / name
            with zipfile.ZipFile(zip_path, "w", compression=mode, allowZip64=True) as zf:
                for file_path in group:
                    zf.write(file_path, str(_arcname(file_path, base_dir)))
            zips.append(zip_path)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
    return zips


def archive_files_rar(
    files: list[Path],
    base_dir: Path,
    out_dir: Path,
    base_name: str,
    limit_bytes: int,
    compression: str,
    rar_binary: str,
) -> list[Path]:
    """Pack *files* into independent rar archives no larger than *limit_bytes*.

    Same grouping/chunking behaviour as archive_files, but each group is
    written by the external rar CLI. Every .rar is a complete standalone
    archive (no multi-volume) so parts extract independently.

    Args:
        rar_binary: Path to the rar executable (see find_rar_binary).

    Raises:
        RuntimeError: If rar exits with a fatal code (>1; 1 is a warning).
    """
    groups, staging_root = _prepare_groups(files, base_dir, limit_bytes, rar_binary)
    out_dir.mkdir(parents=True, exist_ok=True)
    single = len(groups) == 1
    rars: list[Path] = []
    try:
        for index, group in enumerate(groups, 1):
            name = f"{base_name}.rar" if single else f"{base_name}.part{index:03d}.rar"
            rar_path = out_dir / name
            rar_path.unlink(missing_ok=True)  # rar appends to existing archives
            # rar stores paths as given relative to cwd; staged and normal
            # files have different roots, so invoke rar once per root
            # (subsequent runs append to the same archive).
            buckets: dict[Path, list[Path]] = {}
            for file_path in group:
                root = _member_root(file_path, base_dir)
                buckets.setdefault(root, []).append(file_path)
            for root, members in buckets.items():
                cmd = [
                    rar_binary,
                    "a",
                    _RAR_COMPRESSION_FLAGS[compression],
                    "-idq",  # quiet
                    "-y",    # assume Yes on all prompts
                    str(rar_path),
                    *[str(f.relative_to(root)) for f in members],
                ]
                result = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
                if result.returncode > 1:  # 0 = ok, 1 = non-fatal warning
                    raise RuntimeError(
                        f"rar exited with code {result.returncode}: {result.stderr.strip()}"
                    )
            rars.append(rar_path)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
    return rars


def _member_root(file_path: Path, base_dir: Path) -> Path:
    """Directory that member paths inside the archive are relative to.

    Staged files (extracted contents, chunks) resolve against the staging
    dir so archives never expose the internal `_staging/` prefix.
    """
    staging_root = base_dir / _STAGING_DIR
    if file_path.is_relative_to(staging_root):
        return staging_root
    if file_path.is_relative_to(base_dir):
        return base_dir
    return file_path.parent


def _arcname(file_path: Path, base_dir: Path) -> Path:
    """Archive member name for *file_path*."""
    return file_path.relative_to(_member_root(file_path, base_dir))


def _prepare_groups(
    files: list[Path], base_dir: Path, limit_bytes: int, rar_binary: str | None = None
) -> tuple[list[list[Path]], Path]:
    """Stage oversized files, then group everything within the size budget.

    Oversized archives are extracted into the staging dir (their contents
    become the packed files, prefixed by the archive's stem); anything
    else oversized is cut into raw chunks.

    Returns:
        (groups, staging_root) — caller must remove staging_root when done.
    """
    budget = _budget(limit_bytes)
    staging_root = base_dir / _STAGING_DIR
    prepared: list[Path] = []

    def add_sized(file_path: Path) -> None:
        """Add a file, chunking it if it exceeds the budget."""
        if file_path.stat().st_size > budget:
            chunk_dir = staging_root / f"{safe_name(file_path.name)}_chunks"
            prepared.extend(split_file_to_chunks(file_path, chunk_dir, limit_bytes))
        else:
            prepared.append(file_path)

    for file_path in files:
        if file_path.stat().st_size <= budget:
            prepared.append(file_path)
            continue
        if is_archive(file_path):
            extract_dir = staging_root / safe_name(file_path.stem)
            if extract_archive(file_path, extract_dir, rar_binary):
                for inner in sorted(p for p in extract_dir.rglob("*") if p.is_file()):
                    add_sized(inner)
                continue  # contents replace the original archive
        add_sized(file_path)

    return plan_groups(prepared, limit_bytes), staging_root
