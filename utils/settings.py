"""Load configuration from config/setting.json with built-in defaults."""

import json
from pathlib import Path

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "setting.json"

_DEFAULTS: dict = {
    "InputPath": "./inputLinks.txt",
    "OutputDirPath": "./downloads",
    "Retries": 3,
    "RetryDelaySeconds": 2,
    "Mega": {
        "TimeoutSeconds": 600,
        "ServerStartupWaitSeconds": 3,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load() -> dict:
    if not _CONFIG_PATH.is_file():
        return dict(_DEFAULTS)
    try:
        raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        return _deep_merge(_DEFAULTS, raw)
    except Exception:
        return dict(_DEFAULTS)


settings = _load()
