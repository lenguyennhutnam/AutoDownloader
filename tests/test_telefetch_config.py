"""Tests for telefetch.config."""

from pathlib import Path

import pytest

from telefetch.config import ConfigError, TELEFETCH_DEFAULTS, load_config

VALID_ENV = {"TELEGRAM_API_ID": "12345", "TELEGRAM_API_HASH": "abcdef"}


def test_load_config_happy_path(tmp_path: Path):
    cfg = load_config(
        project_root=tmp_path,
        telegram_settings={"Channel": "mychannel"},
        env=VALID_ENV,
    )
    assert cfg.api_id == 12345
    assert cfg.api_hash == "abcdef"
    assert cfg.channel == "mychannel"
    assert cfg.split_size_bytes == 1 * 1024**3          # default 1 GB
    assert cfg.compression == "stored"
    assert cfg.keep_original is False
    assert cfg.output_dir == tmp_path / "telefetch_data"
    assert cfg.output_dir.is_dir()                       # created on load
    assert cfg.session_path.parent.is_dir()              # session dir created


def test_split_size_override(tmp_path: Path):
    cfg = load_config(
        project_root=tmp_path,
        telegram_settings={"Channel": "c", "SplitSizeGB": 2.5},
        env=VALID_ENV,
    )
    assert cfg.split_size_bytes == int(2.5 * 1024**3)


def test_missing_credentials_raises(tmp_path: Path):
    with pytest.raises(ConfigError, match="TELEGRAM_API_ID"):
        load_config(project_root=tmp_path, telegram_settings={"Channel": "c"}, env={})


def test_missing_channel_raises(tmp_path: Path):
    with pytest.raises(ConfigError, match="Channel"):
        load_config(project_root=tmp_path, telegram_settings={}, env=VALID_ENV)


def test_invalid_compression_raises(tmp_path: Path):
    with pytest.raises(ConfigError, match="Compression"):
        load_config(
            project_root=tmp_path,
            telegram_settings={"Channel": "c", "Compression": "lzma"},
            env=VALID_ENV,
        )


def test_env_file_parsing(tmp_path: Path):
    (tmp_path / ".env").write_text(
        "# comment\nTELEGRAM_API_ID=999\nTELEGRAM_API_HASH='hash'\n\n", encoding="utf-8"
    )
    cfg = load_config(project_root=tmp_path, telegram_settings={"Channel": "c"}, env=None)
    assert cfg.api_id == 999
    assert cfg.api_hash == "hash"


def test_defaults_dict_has_pascal_case_keys():
    assert set(TELEFETCH_DEFAULTS) == {
        "Channel", "OutputDirPath", "SplitSizeGB", "Compression", "KeepOriginal", "SessionPath",
    }
