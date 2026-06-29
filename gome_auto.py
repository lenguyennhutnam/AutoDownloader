#!/usr/bin/env python3
import argparse
import asyncio
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from telethon import TelegramClient, events


API_ID = 21754563
API_HASH = "79e31b1984a89f76d5c290fda8b1052e"
CHANNEL = "crabfast"

# ROOT_DIR = Path(r"F:\GOME-AUTO")
ROOT_DIR = Path(r"E:\H1nam\M_n\GOME_AUTO")
TEMP_DIR = ROOT_DIR / "temp"
STATE_PATH = ROOT_DIR / "state.json"
LOG_PATH = ROOT_DIR / "gome_auto.log"
SESSION_PATH = ROOT_DIR / "crabfast_session"

# MEGACMD_DIR = Path(r"C:\Users\Administrator\AppData\Local\MEGAcmd")
MEGACMD_DIR = Path.home() / "AppData" / "Local" / "MEGAcmd"
MEGAGET = MEGACMD_DIR / "mega-get.bat"
MEGALOGOUT = MEGACMD_DIR / "mega-logout.bat"

SEVEN_ZIP = Path(r"C:\Program Files\7-Zip\7z.exe")
SPLIT_SIZE = 2 * 1024 * 1024 * 1024
ZIP_TARGET_SIZE = int(1.9 * 1024 * 1024 * 1024)

GOFILE_RE = re.compile(r"https?://(?:www\.)?gofile\.io/d/[a-zA-Z0-9]+")
MEGA_RE = re.compile(r"https?://mega\.nz/(?:file|folder)/[^\s\"'<>]+")


def setup_logging() -> logging.Logger:
    ROOT_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("GOME-AUTO")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(stream)
    logger.addHandler(file_handler)
    return logger


log = setup_logging()


@dataclass
class LinkState:
    url: str
    kind: str
    status: str = "pending"
    attempts: int = 0
    message_id: int | None = None
    discovered_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    files: list[str] = field(default_factory=list)
    parts_dir: str | None = None
    error: str | None = None


def load_state() -> dict[str, LinkState]:
    if not STATE_PATH.exists():
        return {}
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Cannot load state, starting empty: %s", e)
        return {}
    state: dict[str, LinkState] = {}
    for url, item in raw.get("links", {}).items():
        try:
            state[url] = LinkState(**item)
        except TypeError:
            continue
    return state


def save_state(state: dict[str, LinkState]) -> None:
    tmp = STATE_PATH.with_suffix(".json.tmp")
    data = {"links": {url: vars(item) for url, item in sorted(state.items())}}
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_PATH)


def update_state(state: dict[str, LinkState], item: LinkState, status: str, error: str | None = None) -> None:
    item.status = status
    item.updated_at = time.time()
    item.error = error
    state[item.url] = item
    save_state(state)


def safe_name(value: str, max_len: int = 120) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("._")
    return (cleaned or "item")[:max_len]


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def load_gofile_manager() -> Any:
    script = Path(__file__).resolve().parent / "gofile-downloader" / "gofile-downloader.py"
    spec = importlib.util.spec_from_file_location("gofile_downloader", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load GoFile downloader from {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Manager


GOFILE_MANAGER = load_gofile_manager()


def url_work_dir(item: LinkState) -> Path:
    key = item.url.rstrip("/").split("/")[-1]
    return TEMP_DIR / item.kind / safe_name(key)


def list_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*") if p.is_file() and not p.name.startswith(".getxfer")]


def download_gofile(item: LinkState) -> list[Path]:
    dest = url_work_dir(item)
    dest.mkdir(parents=True, exist_ok=True)
    before = {p.resolve() for p in list_files(dest)}
    manager = GOFILE_MANAGER(url_or_file=item.url, password=None)
    manager._root_dir = str(dest)
    manager.run()
    after = list_files(dest)
    new_files = [p for p in after if p.resolve() not in before]
    return new_files or after


def mega_logout() -> None:
    if MEGALOGOUT.exists():
        subprocess.run([str(MEGALOGOUT)], capture_output=True, text=True)


def download_mega(item: LinkState) -> list[Path]:
    if not MEGAGET.exists():
        raise RuntimeError(f"mega-get not found at {MEGAGET}")
    dest = url_work_dir(item)
    dest.mkdir(parents=True, exist_ok=True)
    before = {p.resolve() for p in list_files(dest)}

    # Khởi động mega-cmd server nếu chưa chạy
    mega_server = MEGACMD_DIR / "mega-cmd-server.exe"
    if mega_server.exists():
        try:
            subprocess.Popen(
                [str(mega_server)],
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(3)
        except Exception:
            pass

    mega_logout()
    log.info("Starting mega-get → %s", dest)

    process = subprocess.Popen(
        [str(MEGAGET), item.url, str(dest)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    last_pct = -1.0
    try:
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            if "TRANSFERRING" in line and "%" in line:
                try:
                    pct = float(line.split("(")[1].split("%")[0].strip().split()[-1])
                    if pct - last_pct >= 1.0:
                        log.info("[mega-get] %s", line)
                        last_pct = pct
                except Exception:
                    pass
            else:
                log.info("[mega-get] %s", line)
    except KeyboardInterrupt:
        log.warning("Download paused by user (Ctrl+C). Run again to resume.")
        process.terminate()
        raise  # Để script dừng sạch

    process.wait()
    if process.returncode != 0:
        raise RuntimeError(f"mega-get exited with code {process.returncode}")

    after = list_files(dest)
    new_files = [p for p in after if p.resolve() not in before]
    return new_files or after



def is_archive(file_path: Path) -> bool:
    return file_path.suffix.lower() in {".zip", ".rar", ".7z"}


def extract_archive(file_path: Path, out_dir: Path) -> bool:
    if not SEVEN_ZIP.exists():
        return False
    out_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [str(SEVEN_ZIP), "x", "-y", str(file_path), f"-o{out_dir}"],
        capture_output=True,
        text=True,
    )
    if result.returncode in (0, 1):
        return bool(list_files(out_dir))
    if list_files(out_dir):
        log.warning("7z returned %s but extracted partial content for %s", result.returncode, file_path.name)
        return True
    log.warning("Cannot extract %s before independent split: %s", file_path.name, result.stderr.strip())
    return False


def zip_files(files: list[Path], base_dir: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for file_path in files:
            arcname = file_path.relative_to(base_dir) if file_path.is_relative_to(base_dir) else file_path.name
            zf.write(file_path, arcname)


def make_independent_zips(file_path: Path, parts_dir: Path) -> list[Path]:
    parts_dir.mkdir(parents=True, exist_ok=True)
    staging = parts_dir / "_staging"
    source_root = file_path.parent
    files: list[Path]

    if is_archive(file_path) and extract_archive(file_path, staging):
        source_root = staging
        files = sorted(list_files(staging))
        log.info("Repacking extracted archive content into independent zip parts")
    else:
        source_root = staging
        files = split_raw_to_staging(file_path, staging)
        log.warning("Could not repack archive content; created independent raw chunk zip packages instead")

    parts: list[Path] = []
    current: list[Path] = []
    current_size = 0
    part_no = 1
    base = safe_name(file_path.stem)

    for candidate in files:
        size = candidate.stat().st_size
        if current and current_size + size > ZIP_TARGET_SIZE:
            out = parts_dir / f"{base}.part{part_no:03d}.zip"
            zip_files(current, source_root, out)
            parts.append(out)
            part_no += 1
            current = []
            current_size = 0
        current.append(candidate)
        current_size += size

    if current:
        out = parts_dir / f"{base}.part{part_no:03d}.zip"
        zip_files(current, source_root, out)
        parts.append(out)

    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    return parts


def split_raw_to_staging(file_path: Path, staging: Path) -> list[Path]:
    staging.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    chunk_size = ZIP_TARGET_SIZE
    base = safe_name(file_path.name)
    part_no = 1
    manifest = {
        "original_file": file_path.name,
        "original_size": file_path.stat().st_size,
        "chunk_size": chunk_size,
        "note": "Each zip is independently extractable. Concatenate chunks in order to reconstruct the original file.",
    }
    with open(file_path, "rb") as src:
        while True:
            chunk = src.read(chunk_size)
            if not chunk:
                break
            out = staging / f"{base}.chunk{part_no:03d}"
            out.write_bytes(chunk)
            files.append(out)
            part_no += 1
    (staging / "MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    files.append(staging / "MANIFEST.json")
    return files


def process_downloaded_files(item: LinkState, files: list[Path], delete_original: bool) -> list[Path]:
    final_files: list[Path] = []
    for file_path in files:
        if not file_path.exists():
            continue
        size = file_path.stat().st_size
        if size <= SPLIT_SIZE:
            log.info("Saved %s (%s)", file_path, format_bytes(size))
            final_files.append(file_path)
            continue

        parts_dir = file_path.parent / f"{safe_name(file_path.name)}_parts"
        log.info("File >2GB, creating independent zip parts: %s (%s)", file_path.name, format_bytes(size))
        parts = make_independent_zips(file_path, parts_dir)
        for part in parts:
            log.info("Part ready: %s (%s)", part, format_bytes(part.stat().st_size))
        final_files.extend(parts)
        item.parts_dir = str(parts_dir)
        if delete_original and parts:
            file_path.unlink(missing_ok=True)
            log.info("Deleted original after split: %s", file_path)
    return final_files


def collect_links_from_text(
    state: dict[str, LinkState],
    text: str | None,
    queued: set[str],
    message_id: int | None = None,
) -> list[LinkState]:
    items: list[LinkState] = []
    if not text:
        return items
    for kind, regex in (("gofile", GOFILE_RE), ("mega", MEGA_RE)):
        for url in regex.findall(text):
            if url in queued:
                continue
            if url in state and state[url].status == "done":
                continue
            if url not in state:
                state[url] = LinkState(url=url, kind=kind, message_id=message_id)
            queued.add(url)
            items.append(state[url])
    return items


async def discover_links(state: dict[str, LinkState]) -> list[LinkState]:
    new_items: list[LinkState] = []
    queued: set[str] = set()
    async with TelegramClient(str(SESSION_PATH), API_ID, API_HASH) as tg:
        entity = await tg.get_entity(CHANNEL)
        log.info("Scanning Telegram channel '%s'", CHANNEL)

        async for message in tg.iter_messages(entity):
            new_items.extend(collect_links_from_text(state, message.text, queued, message.id))
        save_state(state)

    return new_items


async def listen_for_links(state: dict[str, LinkState], delete_original: bool) -> None:
    queued: set[str] = set()
    async with TelegramClient(str(SESSION_PATH), API_ID, API_HASH) as tg:
        entity = await tg.get_entity(CHANNEL)
        log.info("Listening for new messages in '%s'", CHANNEL)

        @tg.on(events.NewMessage(chats=entity))
        async def handler(event):
            items = collect_links_from_text(state, event.text, queued, event.id)
            save_state(state)
            for item in items:
                await asyncio.to_thread(process_item, state, item, delete_original)

        await tg.run_until_disconnected()


def process_item(state: dict[str, LinkState], item: LinkState, delete_original: bool) -> None:
    if item.status == "done":
        return
    item.attempts += 1
    update_state(state, item, "downloading")
    log.info("Processing %s link: %s", item.kind, item.url)
    try:
        files = download_gofile(item) if item.kind == "gofile" else download_mega(item)
        if not files:
            raise RuntimeError("download completed but no files found")
        update_state(state, item, "processing")
        final_files = process_downloaded_files(item, files, delete_original)
        item.files = [str(p) for p in final_files]
        update_state(state, item, "done")
        log.info("Done: %s (%d file/part)", item.url, len(final_files))
    except Exception as e:
        update_state(state, item, "failed", str(e))
        log.exception("Failed: %s", item.url)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl crabfast links, download to F:\\GOME-AUTO\\temp, split large files into independent zip packages.")
    parser.add_argument("--listen", action="store_true", help="Keep listening after scanning all messages")
    parser.add_argument("--keep-original", action="store_true", help="Do not delete original file after successful split")
    args = parser.parse_args()

    state = load_state()
    delete_original = not args.keep_original
    items = await discover_links(state)
    pending = [item for item in items if item.status != "done"]
    log.info("Found %d pending links", len(pending))
    for idx, item in enumerate(pending, 1):
        log.info("[%d/%d] %s", idx, len(pending), item.url)
        process_item(state, item, delete_original=delete_original)
    if args.listen:
        await listen_for_links(state, delete_original)
    else:
        log.info("All done")


# if __name__ == "__main__":
#     asyncio.run(main())

if __name__ == "__main__":
    state = load_state()
    # url = "https://mega.nz/file/hFETEC5D#wY-dRmLsRPePjZ4uyV0IuPBjG0kpVW0s0JGy9pLYco0"
    url = "https://gofile.io/d/testfake123"
    if url not in state:
        state[url] = LinkState(url=url, kind="mega")
    save_state(state)
    process_item(state, state[url], delete_original=False)

