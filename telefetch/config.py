"""Load telefetch configuration: Telegram section of setting.json + .env secrets.

Split into two layers so download-only tooling never needs Telegram
credentials: DownloadConfig covers everything process_link() needs,
TelegramConfig adds the fields only the scraper/combined CLI use.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from utils.settings import _deep_merge, settings

TELEFETCH_DEFAULTS: dict = {
    "Channel": "",
    "OutputDirPath": "./telefetch_data",
    "SplitSizeGB": 2,
    "Compression": "stored",
    "ArchiveFormat": "rar",
    "RarPath": "",
    "KeepOriginal": False,
    "SessionPath": "./telefetch_data/session",
}

_VALID_COMPRESSION = {"stored", "deflated"}
_VALID_FORMATS = {"rar", "zip"}


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


@dataclass
class DownloadConfig:
    """Everything process_link() needs — no Telegram credentials required."""

    output_dir: Path
    split_size_bytes: int
    compression: str
    archive_format: str
    rar_path: str
    keep_original: bool


@dataclass
class TelegramConfig(DownloadConfig):
    """DownloadConfig plus the fields needed to talk to Telegram."""

    api_id: int
    api_hash: str
    channel: str
    session_path: Path


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse a minimal KEY=VALUE .env file (comments and blank lines ignored)."""
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip().strip("'\"")
    return result


def _resolve_dir(root: Path, raw: str) -> Path:
    """Resolve *raw* against *root* unless it is already absolute."""
    path = Path(raw)
    return path if path.is_absolute() else root / path


def _merged_settings(telegram_settings: dict | None) -> dict:
    raw = telegram_settings if telegram_settings is not None else settings.get("Telegram", {})
    return _deep_merge(TELEFETCH_DEFAULTS, raw)


def _build_download_config(root: Path, merged: dict) -> DownloadConfig:
    """Validate and resolve the fields DownloadConfig needs from *merged*."""
    if merged["Compression"] not in _VALID_COMPRESSION:
        raise ConfigError(
            f"Telegram.Compression must be one of {sorted(_VALID_COMPRESSION)}, "
            f"got: {merged['Compression']!r}"
        )
    if merged["ArchiveFormat"] not in _VALID_FORMATS:
        raise ConfigError(
            f"Telegram.ArchiveFormat must be one of {sorted(_VALID_FORMATS)}, "
            f"got: {merged['ArchiveFormat']!r}"
        )

    output_dir = _resolve_dir(root, merged["OutputDirPath"])
    output_dir.mkdir(parents=True, exist_ok=True)

    return DownloadConfig(
        output_dir=output_dir,
        split_size_bytes=int(float(merged["SplitSizeGB"]) * 1024**3),
        compression=merged["Compression"],
        archive_format=merged["ArchiveFormat"],
        rar_path=str(merged["RarPath"]),
        keep_original=bool(merged["KeepOriginal"]),
    )


def load_download_config(
    project_root: Path | None = None,
    telegram_settings: dict | None = None,
) -> DownloadConfig:
    """Build a validated DownloadConfig — no Telegram credentials needed.

    Args:
        project_root: Base dir for relative paths. Defaults to the repo root.
        telegram_settings: Override for the "Telegram" section
            (defaults to config/setting.json contents).

    Raises:
        ConfigError: If Compression or ArchiveFormat is invalid.
    """
    root = project_root or Path(__file__).resolve().parent.parent
    merged = _merged_settings(telegram_settings)
    return _build_download_config(root, merged)


def load_config(
    project_root: Path | None = None,
    telegram_settings: dict | None = None,
    env: dict[str, str] | None = None,
) -> TelegramConfig:
    """Build a validated TelegramConfig.

    Args:
        project_root: Base dir for relative paths and .env lookup.
            Defaults to the repository root.
        telegram_settings: Override for the "Telegram" section
            (defaults to config/setting.json contents).
        env: Override for credential lookup (defaults to .env file
            merged with os.environ, where os.environ wins).

    Raises:
        ConfigError: If credentials, channel, or compression/format are missing/invalid.
    """
    root = project_root or Path(__file__).resolve().parent.parent
    if env is None:
        env = {**_read_env_file(root / ".env"), **os.environ}

    api_id = env.get("TELEGRAM_API_ID", "").strip()
    api_hash = env.get("TELEGRAM_API_HASH", "").strip()
    if not api_id or not api_hash:
        raise ConfigError(
            "Missing TELEGRAM_API_ID / TELEGRAM_API_HASH. "
            "Copy .env.example to .env and fill in credentials from https://my.telegram.org/apps"
        )
    if not api_id.isdigit():
        raise ConfigError(f"TELEGRAM_API_ID must be an integer, got: {api_id!r}")

    merged = _merged_settings(telegram_settings)
    if not merged["Channel"]:
        raise ConfigError("Telegram.Channel is not set in config/setting.json")

    download_cfg = _build_download_config(root, merged)
    session_path = _resolve_dir(root, merged["SessionPath"])
    session_path.parent.mkdir(parents=True, exist_ok=True)

    return TelegramConfig(
        output_dir=download_cfg.output_dir,
        split_size_bytes=download_cfg.split_size_bytes,
        compression=download_cfg.compression,
        archive_format=download_cfg.archive_format,
        rar_path=download_cfg.rar_path,
        keep_original=download_cfg.keep_original,
        api_id=int(api_id),
        api_hash=api_hash,
        channel=str(merged["Channel"]),
        session_path=session_path,
    )
