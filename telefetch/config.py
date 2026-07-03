"""Load telefetch configuration: Telegram section of setting.json + .env secrets."""

import os
from dataclasses import dataclass
from pathlib import Path

from utils.settings import _deep_merge, settings

TELEFETCH_DEFAULTS: dict = {
    "Channel": "",
    "OutputDirPath": "./telefetch_data",
    "SplitSizeGB": 1,
    "Compression": "stored",
    "KeepOriginal": False,
    "SessionPath": "./telefetch_data/session",
}

_VALID_COMPRESSION = {"stored", "deflated"}


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


@dataclass
class TelegramConfig:
    """Resolved, validated telefetch configuration."""

    api_id: int
    api_hash: str
    channel: str
    output_dir: Path
    split_size_bytes: int
    compression: str
    keep_original: bool
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
        ConfigError: If credentials, channel, or compression are missing/invalid.
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

    raw = telegram_settings if telegram_settings is not None else settings.get("Telegram", {})
    merged = _deep_merge(TELEFETCH_DEFAULTS, raw)

    if not merged["Channel"]:
        raise ConfigError("Telegram.Channel is not set in config/setting.json")
    if merged["Compression"] not in _VALID_COMPRESSION:
        raise ConfigError(
            f"Telegram.Compression must be one of {sorted(_VALID_COMPRESSION)}, "
            f"got: {merged['Compression']!r}"
        )

    output_dir = _resolve_dir(root, merged["OutputDirPath"])
    session_path = _resolve_dir(root, merged["SessionPath"])
    output_dir.mkdir(parents=True, exist_ok=True)
    session_path.parent.mkdir(parents=True, exist_ok=True)

    return TelegramConfig(
        api_id=int(api_id),
        api_hash=api_hash,
        channel=str(merged["Channel"]),
        output_dir=output_dir,
        split_size_bytes=int(float(merged["SplitSizeGB"]) * 1024**3),
        compression=merged["Compression"],
        keep_original=bool(merged["KeepOriginal"]),
        session_path=session_path,
    )
