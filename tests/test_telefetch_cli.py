"""Smoke tests for the telefetch CLI parser."""

from telefetch.cli import build_parser


def test_parser_defaults():
    args = build_parser().parse_args([])
    assert args.once is False
    assert args.skip_failed is False
    assert args.keep_original is False


def test_parser_flags():
    args = build_parser().parse_args(["--once", "--skip-failed", "--keep-original"])
    assert args.once is True
    assert args.skip_failed is True
    assert args.keep_original is True
