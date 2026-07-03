# Design: `telefetch` — Telegram link scraper + downloader

**Ngày:** 2026-07-03
**Trạng thái:** Approved

## Mục tiêu

Tool cào tất cả tin nhắn từ một kênh Telegram (tin nhắn chứa link tải GoFile/MEGA/Google Drive), lưu trạng thái từng link, tải lần lượt, nén tất cả sau khi tải, split khi vượt ngưỡng. Lắng nghe realtime tin nhắn mới. Ubuntu-first, chạy được trên Windows.

## Quyết định đã chốt

| Câu hỏi | Quyết định |
|---|---|
| Quan hệ với code có sẵn | Package mới `telefetch/`, **reuse** `downloaders/` + `utils/`. Không đụng `gome_auto.py` |
| Nén | **Nén mọi thứ** sau khi tải xong (kể cả file nhỏ) |
| Split | **Zip part độc lập** — mỗi part tự giải nén được riêng |
| Ngưỡng split | `SplitSizeGB` trong `config/setting.json`, **mặc định 1 GB** |
| Realtime | Mặc định: quét hết backlog → chuyển sang listen realtime. `--once` để thoát sau backlog |

## Kiến trúc

```
telefetch.py                # Entry point CLI (argparse)
telefetch/
├── __init__.py
├── config.py               # Load section "Telegram" từ config/setting.json + secrets từ .env
├── state.py                # LinkState dataclass + JSON store (trạng thái từng link)
├── scraper.py              # Telethon: quét toàn bộ lịch sử + listener realtime
├── processor.py            # Orchestrate: download → nén → split → update state
└── archiver.py             # Nén zip + split part độc lập (stdlib zipfile)
```

**Reuse:**
- `downloaders/registry.py` — dispatch URL → downloader (GoFile, MEGA, Google Drive)
- `downloaders/*.match()` — nhận diện link trong text tin nhắn (không viết regex riêng)
- `utils/logger.py` — colored logging
- Pattern `utils/settings.py` — deep merge config với defaults

## Luồng hoạt động

1. Startup → connect Telethon (user session), quét **toàn bộ** lịch sử kênh
2. Với mỗi tin nhắn: extract URL, hỏi registry — URL nào có downloader nhận thì tạo `LinkState(status="pending")` vào `state.json`
3. Tải **lần lượt** từng link pending theo thứ tự phát hiện:
   `pending → downloading → compressing → done` hoặc `failed`
4. Xong backlog → **listen realtime** (`events.NewMessage`): tin mới đến → extract → tải luôn
5. Link lỗi: log rõ nguyên nhân, mark `failed` kèm `error`, chuyển link tiếp theo — không dừng tool
6. Chạy lại tool: link `done` bỏ qua, link `failed` retry (flag `--skip-failed` để bỏ qua)

## LinkState (state.json)

```python
@dataclass
class LinkState:
    url: str
    kind: str                    # tên downloader: "GoFile", "MEGA", "Google Drive"
    status: str = "pending"      # pending | downloading | compressing | done | failed
    attempts: int = 0
    message_id: int | None = None
    discovered_at: float
    updated_at: float
    files: list[str]             # các file/part kết quả cuối
    error: str | None = None
```

Ghi atomic: write `.tmp` rồi `replace()` (như gome_auto).

## Nén & split (`archiver.py`)

- Sau khi tải xong: nén toàn bộ file tải về thành zip
- **Chia nhóm trước khi nén**: tính size từng file, gom nhóm sao cho mỗi zip ≤ `SplitSizeGB` → `name.part001.zip`, `name.part002.zip`... Mỗi part giải nén độc lập, mất 1 part vẫn dùng được phần còn lại
- Nếu ≤ ngưỡng: 1 file zip duy nhất (không suffix `.partNNN`)
- File đơn lẻ > ngưỡng: cắt raw chunk (`file.chunk001`, ...) + `MANIFEST.json` hướng dẫn ghép, rồi zip từng chunk
- Xóa file gốc sau khi nén thành công; config `KeepOriginal: true` để giữ
- Dùng **stdlib `zipfile`** (allowZip64) — không phụ thuộc 7z, cross-platform
- Mức nén: config `Compression`: `"stored"` (mặc định — nhanh, file game vốn đã nén) hoặc `"deflated"`

## Config

`config/setting.json` thêm section:

```json
{
    "Telegram": {
        "Channel": "crabfast",
        "OutputDirPath": "./telefetch_data",
        "SplitSizeGB": 1,
        "Compression": "stored",
        "KeepOriginal": false,
        "SessionPath": "./telefetch_data/session"
    }
}
```

**Secrets** (KHÔNG commit): `.env` ở project root, gitignored:

```
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
```

`config.py` đọc `.env` thủ công (không thêm dep `python-dotenv`) hoặc từ env vars hệ thống. Thiếu credentials → báo lỗi rõ hướng dẫn lấy từ my.telegram.org.

## Cross-platform

- Không hardcode path — mọi path từ config, default tương đối project root
- MEGA: reuse `downloaders/mega.py` (đã dùng `shutil.which("mega-get")`) — Ubuntu: `apt install megacmd`
- Nén stdlib → không cần 7-Zip
- Session/state/log nằm trong `OutputDirPath`

## Error handling

- Mỗi bước (download, nén) catch exception → mark `failed` + `error` message, next link
- `KeyboardInterrupt`: cleanup (kill subprocess MEGA, xóa file dở), lưu state, re-raise — chạy lại resume từ state
- Telethon disconnect trong listen mode: Telethon tự reconnect (`run_until_disconnected`)

## Dependencies

Thêm vào `requirements.txt`: `telethon>=1.36,<2` — bắt buộc, Bot API không đọc được lịch sử kênh. Cập nhật ghi chú CLAUDE.md (quy tắc "không dùng telethon" chỉ áp dụng cho downloader CLI cũ).

## CLI

```bash
python telefetch.py                  # quét backlog + listen realtime
python telefetch.py --once           # quét backlog rồi thoát
python telefetch.py --skip-failed    # không retry link failed
python telefetch.py --keep-original  # override config, giữ file gốc
```

## Testing

- Unit test: `archiver.py` (chia nhóm, split chunk, manifest), `state.py` (load/save/atomic), `config.py` (merge defaults, thiếu .env)
- `scraper.py` test bằng mock Telethon client (không gọi API thật)
- Verify thực tế: chạy với kênh thật + 1 link nhỏ

## Ngoài phạm vi (Phase sau)

- Tải song song
- Resume download giữa chừng
- Upload part lên nơi khác sau khi nén

## Ghi chú bảo mật

`gome_auto.py:21-22` đang hardcode `API_ID`/`API_HASH` thật và đã nằm trong git history — khuyến nghị revoke/reset API hash trên my.telegram.org sau khi chuyển sang `.env`.
