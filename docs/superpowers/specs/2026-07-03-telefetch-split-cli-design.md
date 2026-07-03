# Design: split telefetch into scan-only and download-only CLIs

**Ngày:** 2026-07-03
**Trạng thái:** Approved

## Mục tiêu

Hiện `python -m telefetch` gộp cả cào tin nhắn (scan/listen Telegram) lẫn tải file trong 1 CLI. Người dùng muốn tách thành 2 CLI độc lập — 1 cào, 1 tải — chạy **lần lượt, không cùng lúc**, để có thể chạy cào xong rồi tải riêng khi cần. CLI gộp hiện tại (`python -m telefetch`) giữ nguyên không đổi.

## Quyết định đã chốt

| Câu hỏi | Quyết định |
|---|---|
| Chạy đồng thời hay lần lượt | **Lần lượt** — không cần cơ chế an toàn ghi đồng thời cho `state.json` |
| CLI tải-only chạy 1 lượt hay loop | **1 lượt** hết pending rồi thoát |
| CLI tải-only có cần `.env`/credentials Telegram | **Không** — tải không liên quan gì tới Telegram |
| CLI gộp hiện tại | Giữ nguyên 100%, không sửa `cli.py` |

## Kiến trúc

### Tách `config.py` thành 2 tầng

```python
@dataclass
class DownloadConfig:
    output_dir: Path
    split_size_bytes: int
    compression: str
    archive_format: str
    rar_path: str
    keep_original: bool

@dataclass
class TelegramConfig(DownloadConfig):
    api_id: int
    api_hash: str
    channel: str
    session_path: Path
```

- `load_download_config(project_root=None, telegram_settings=None) -> DownloadConfig` (mới) — đọc phần `Telegram` trong `config/setting.json` (bỏ qua `Channel`/`SessionPath`), validate `Compression`/`ArchiveFormat`. **Không đọc `.env`, không cần credentials.**
- `load_config(...) -> TelegramConfig` (giữ nguyên chữ ký, đổi implementation nội bộ để build trên `DownloadConfig` rồi thêm Telegram fields) — vẫn cần `.env` như hiện tại
- `TELEFETCH_DEFAULTS` giữ nguyên (dùng chung cho cả 2 loader)

### `telefetch/processor.py`

- `process_link(store, item, cfg: DownloadConfig)` — đổi type hint từ `TelegramConfig` sang `DownloadConfig` (không đổi hành vi: hàm này vốn chỉ đọc 6 field của `DownloadConfig`, chưa từng đụng Telegram fields). Vì `TelegramConfig` kế thừa `DownloadConfig`, CLI gộp truyền `TelegramConfig` vào vẫn hoạt động y hệt.

### `telefetch/scan_cli.py` (mới) — chỉ cào

```bash
python -m telefetch.scan_cli           # cào backlog + listen realtime mãi
python -m telefetch.scan_cli --once    # cào backlog rồi thoát
```

- Dùng `load_config()` (cần `.env`, phải đăng nhập Telegram)
- Reuse nguyên `scan_history()` + `listen()` từ `scraper.py` — **không sửa `scraper.py`**
- `listen()` được gọi với `on_new=lambda item: None` — link mới chỉ `store.add()` vào `state.json` (status `pending`), không tải gì cả

### `telefetch/download_cli.py` (mới) — chỉ tải

```bash
python -m telefetch.download_cli                  # tải hết pending, thoát
python -m telefetch.download_cli --skip-failed    # không retry link failed
python -m telefetch.download_cli --keep-original  # giữ file gốc sau nén
```

- Dùng `load_download_config()` — **không cần `.env`**
- Đọc `state.json` từ `cfg.output_dir`, lấy `store.pending()`, tải lần lượt bằng `process_link()` có sẵn từ `processor.py` — **không sửa `processor.py` ngoài type hint**
- Logic xử lý tách thành hàm `process_pending(store, cfg, skip_failed)` để test được độc lập, không cần mock Telethon/argv

## Testing

- `tests/test_telefetch_config.py`: thêm test cho `load_download_config()` — không cần `.env`, vẫn validate `Compression`/`ArchiveFormat`, defaults đúng
- `tests/test_telefetch_download_cli.py` (mới): test `build_parser()` (flags), test `process_pending()` với `FakeDownloader` (giống style `test_telefetch_processor.py`) — không cần Telegram/Telethon
- `tests/test_telefetch_scan_cli.py` (mới): test `build_parser()` (`--once`). Theo đúng convention hiện có của `test_telefetch_cli.py` — phần async `_run` không unit test (giống `cli.py` hiện tại), vì logic cào đã được test đầy đủ ở `test_telefetch_scraper_async.py`
- Full suite phải pass, không phá test cũ

## Docs

Cập nhật `CLAUDE.md`: thêm 2 lệnh mới vào file structure + Commands section, note rằng tải-only không cần `.env`.

## Ngoài phạm vi

- Chạy đồng thời scan + download (không cần vì đã chốt chạy lần lượt)
- Cơ chế lock/merge an toàn cho `state.json` khi ghi đồng thời
