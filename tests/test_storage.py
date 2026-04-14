"""Tests for storage module."""
from claude_meeting_mcp.storage import generate_filename, ensure_dirs


def test_generate_filename():
    name = generate_filename()
    assert name.endswith(".wav")
    assert "meeting" in name


def test_generate_filename_custom():
    name = generate_filename(prefix="test", ext="json")
    assert name.endswith(".json")
    assert "test" in name
