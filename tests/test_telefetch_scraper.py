"""Tests for telefetch.scraper link extraction."""

from telefetch.scraper import extract_links


def test_extracts_supported_links():
    text = (
        "New game! https://gofile.io/d/Abc123 mirror: "
        "https://mega.nz/file/AAA#secretkey and "
        "https://drive.google.com/file/d/FILE_ID/view"
    )
    result = extract_links(text)
    urls = [u for u, _ in result]
    kinds = [k for _, k in result]
    assert urls == [
        "https://gofile.io/d/Abc123",
        "https://mega.nz/file/AAA#secretkey",
        "https://drive.google.com/file/d/FILE_ID/view",
    ]
    assert kinds == ["GoFile", "MEGA", "Google Drive"]


def test_drops_unsupported_urls():
    assert extract_links("see https://example.com/file.zip please") == []


def test_deduplicates_within_message():
    text = "https://gofile.io/d/abc and again https://gofile.io/d/abc"
    assert len(extract_links(text)) == 1


def test_handles_none_and_empty():
    assert extract_links(None) == []
    assert extract_links("") == []
    assert extract_links("no links here") == []


def test_strips_trailing_punctuation():
    result = extract_links("get it at https://gofile.io/d/abc, thanks")
    assert result == [("https://gofile.io/d/abc", "GoFile")]
