"""Tests for telefetch.scan_cli (argument parsing only — the async Telegram
flow reuses scan_history/listen, which already have their own tests in
tests/test_telefetch_scraper_async.py, matching the convention used by
telefetch/cli.py where _run is not unit tested either)."""

from telefetch.scan_cli import build_parser


def test_parser_defaults():
    args = build_parser().parse_args([])
    assert args.once is False


def test_parser_once_flag():
    args = build_parser().parse_args(["--once"])
    assert args.once is True
