"""Tests for telefetch.archiver."""

import json
import zipfile
from pathlib import Path

from telefetch.archiver import archive_files, plan_groups, safe_name, split_file_to_chunks

KB = 1024


def write_file(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_safe_name_strips_specials():
    assert safe_name("https://mega.nz/file/ab#key!") == "https_mega.nz_file_ab_key"
    assert safe_name("///") == "item"


def test_plan_groups_respects_limit(tmp_path: Path):
    files = [write_file(tmp_path / f"f{i}.bin", 40 * KB) for i in range(5)]
    groups = plan_groups(files, limit_bytes=100 * KB)
    assert [len(g) for g in groups] == [2, 2, 1]


def test_plan_groups_single_group_when_under_limit(tmp_path: Path):
    files = [write_file(tmp_path / f"f{i}.bin", 10 * KB) for i in range(3)]
    assert len(plan_groups(files, limit_bytes=100 * KB)) == 1


def test_split_file_to_chunks_reassembles(tmp_path: Path):
    original = write_file(tmp_path / "big.bin", 250 * KB)
    out = tmp_path / "chunks"
    parts = split_file_to_chunks(original, out, limit_bytes=100 * KB)

    manifest_path = parts[-1]
    assert manifest_path.name.endswith("MANIFEST.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["original_size"] == 250 * KB
    assert manifest["chunk_count"] == 3

    reassembled = b"".join(p.read_bytes() for p in parts[:-1])
    assert reassembled == original.read_bytes()


def test_archive_files_single_zip_when_small(tmp_path: Path):
    base = tmp_path / "work"
    files = [write_file(base / "a.txt", KB), write_file(base / "sub" / "b.txt", KB)]
    out = tmp_path / "archives"

    zips = archive_files(files, base, out, "myitem", limit_bytes=100 * KB, compression="stored")

    assert [z.name for z in zips] == ["myitem.zip"]
    with zipfile.ZipFile(zips[0]) as zf:
        names = set(zf.namelist())
    assert names == {"a.txt", "sub/b.txt"}


def test_archive_files_splits_into_parts(tmp_path: Path):
    base = tmp_path / "work"
    files = [write_file(base / f"f{i}.bin", 40 * KB) for i in range(5)]
    out = tmp_path / "archives"

    zips = archive_files(files, base, out, "myitem", limit_bytes=100 * KB, compression="stored")

    assert [z.name for z in zips] == ["myitem.part001.zip", "myitem.part002.zip", "myitem.part003.zip"]
    # Each part extracts independently and stays within the limit
    for z in zips:
        assert z.stat().st_size <= 100 * KB
        with zipfile.ZipFile(z) as zf:
            assert zf.testzip() is None


def test_archive_files_chunks_oversized_single_file(tmp_path: Path):
    base = tmp_path / "work"
    write_file(base / "huge.bin", 250 * KB)
    out = tmp_path / "archives"

    zips = archive_files(
        [base / "huge.bin"], base, out, "huge", limit_bytes=100 * KB, compression="stored"
    )

    assert len(zips) >= 3  # 3 chunks (+ manifest travels with a part)
    all_members: list[str] = []
    for z in zips:
        with zipfile.ZipFile(z) as zf:
            all_members.extend(zf.namelist())
    assert any(m.endswith("MANIFEST.json") for m in all_members)
    assert sum(1 for m in all_members if ".chunk" in m) == 3
    # staging dir cleaned up
    assert not (base / "_staging").exists()


def test_archive_files_extracts_oversized_zip_and_repacks(tmp_path: Path):
    """A downloaded .zip bigger than the limit is extracted and its CONTENTS
    are packed into independent parts — no raw .chunk members."""
    base = tmp_path / "work"
    base.mkdir(parents=True)
    inner_dir = tmp_path / "payload"
    inner_files = [write_file(inner_dir / f"data{i}.bin", 40 * KB) for i in range(5)]
    big_zip = base / "BRADMAX_9900_JUNE.zip"
    with zipfile.ZipFile(big_zip, "w", compression=zipfile.ZIP_STORED) as zf:
        for f in inner_files:
            zf.write(f, f.name)
    assert big_zip.stat().st_size > 100 * KB  # oversized for this test's limit

    out = tmp_path / "archives"
    zips = archive_files(
        [big_zip], base, out, "BRADMAX_9900_JUNE", limit_bytes=100 * KB, compression="stored"
    )

    all_members: list[str] = []
    for z in zips:
        with zipfile.ZipFile(z) as zf:
            all_members.extend(zf.namelist())
    # real data files inside, prefixed by the source archive's stem
    assert sum(1 for m in all_members if m.endswith(".bin")) == 5
    assert all(m.replace("\\", "/").startswith("BRADMAX_9900_JUNE/") for m in all_members)
    # no chunk fallback, no staging leftovers
    assert not any(".chunk" in m for m in all_members)
    assert not (base / "_staging").exists()


def test_archive_files_falls_back_to_chunks_for_corrupt_zip(tmp_path: Path):
    """An oversized file with .zip extension that cannot be extracted still
    gets the raw-chunk treatment instead of failing."""
    base = tmp_path / "work"
    fake_zip = write_file(base / "broken.zip", 250 * KB)  # not a real zip
    out = tmp_path / "archives"

    zips = archive_files(
        [fake_zip], base, out, "broken", limit_bytes=100 * KB, compression="stored"
    )

    all_members: list[str] = []
    for z in zips:
        with zipfile.ZipFile(z) as zf:
            all_members.extend(zf.namelist())
    assert sum(1 for m in all_members if ".chunk" in m) == 3
    assert any(m.endswith("MANIFEST.json") for m in all_members)
