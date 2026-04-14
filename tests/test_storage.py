"""Tests for storage module."""

from pathlib import Path

from claude_meeting_mcp.storage import (
    _get_data_dir,
    ensure_dirs,
    generate_filename,
    list_pvs,
    list_recordings,
    list_transcriptions,
)


def test_generate_filename():
    name = generate_filename()
    assert name.endswith(".wav")
    assert "meeting" in name


def test_generate_filename_custom():
    name = generate_filename(prefix="test", ext="json")
    assert name.endswith(".json")
    assert "test" in name


def test_data_dir_env_override(monkeypatch, tmp_path):
    custom_dir = str(tmp_path / "custom-data")
    monkeypatch.setenv("CLAUDE_MEETING_DATA_DIR", custom_dir)
    assert _get_data_dir() == Path(custom_dir)


def test_data_dir_default(monkeypatch):
    monkeypatch.delenv("CLAUDE_MEETING_DATA_DIR", raising=False)
    data_dir = _get_data_dir()
    assert "claude-meeting-mcp" in str(data_dir)


def test_ensure_dirs_creates_all(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_MEETING_DATA_DIR", str(tmp_path))
    # Re-import to pick up new env
    import claude_meeting_mcp.storage as storage

    storage.RECORDINGS_DIR = tmp_path / "recordings"
    storage.TRANSCRIPTIONS_DIR = tmp_path / "transcriptions"
    storage.PV_DIR = tmp_path / "pv"

    ensure_dirs()
    assert (tmp_path / "recordings").exists()
    assert (tmp_path / "transcriptions").exists()
    assert (tmp_path / "pv").exists()


def test_list_recordings_empty(monkeypatch, tmp_path):
    import claude_meeting_mcp.storage as storage

    storage.RECORDINGS_DIR = tmp_path / "recordings"
    storage.TRANSCRIPTIONS_DIR = tmp_path / "transcriptions"
    storage.PV_DIR = tmp_path / "pv"

    result = list_recordings()
    assert result == []


def test_list_transcriptions_empty(monkeypatch, tmp_path):
    import claude_meeting_mcp.storage as storage

    storage.RECORDINGS_DIR = tmp_path / "recordings"
    storage.TRANSCRIPTIONS_DIR = tmp_path / "transcriptions"
    storage.PV_DIR = tmp_path / "pv"

    result = list_transcriptions()
    assert result == []


def test_list_pvs_empty(monkeypatch, tmp_path):
    import claude_meeting_mcp.storage as storage

    storage.RECORDINGS_DIR = tmp_path / "recordings"
    storage.TRANSCRIPTIONS_DIR = tmp_path / "transcriptions"
    storage.PV_DIR = tmp_path / "pv"

    result = list_pvs()
    assert result == []
