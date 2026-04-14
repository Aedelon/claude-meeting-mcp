"""Tests for recorder module."""

from unittest.mock import MagicMock, patch

from claude_meeting_mcp.recorder import (
    is_recording,
    start_recording,
    stop_recording,
)


@patch("claude_meeting_mcp.recorder.get_capturer")
@patch("claude_meeting_mcp.recorder._capturer", None)
@patch("claude_meeting_mcp.recorder._current_file", None)
def test_start_stop_lifecycle(mock_get, tmp_path):
    mock_capturer = MagicMock()
    mock_capturer.is_available.return_value = True
    mock_get.return_value = mock_capturer

    result = start_recording()
    assert result["status"] == "recording"
    assert "file" in result

    # Reset internal state for stop
    import claude_meeting_mcp.recorder as rec

    rec._capturer = mock_capturer
    result = stop_recording()
    assert result["status"] == "stopped"
    assert not is_recording()


@patch("claude_meeting_mcp.recorder.get_capturer")
@patch("claude_meeting_mcp.recorder._capturer", "something")
@patch("claude_meeting_mcp.recorder._current_file", "/fake/path.wav")
def test_double_start_error(mock_get):
    result = start_recording()
    assert "error" in result
    assert "already in progress" in result["error"]


@patch("claude_meeting_mcp.recorder._capturer", None)
def test_stop_without_start():
    result = stop_recording()
    assert "error" in result
    assert "No recording" in result["error"]


@patch("claude_meeting_mcp.recorder.get_capturer")
@patch("claude_meeting_mcp.recorder._capturer", None)
@patch("claude_meeting_mcp.recorder._current_file", None)
def test_start_when_not_available(mock_get):
    mock_capturer = MagicMock()
    mock_capturer.is_available.return_value = False
    mock_get.return_value = mock_capturer

    result = start_recording()
    assert "error" in result
    assert "not available" in result["error"]


def test_get_capturer_returns_macos():
    """On macOS, get_capturer should return MacOSCapturer."""
    import sys

    if sys.platform == "darwin":
        from claude_meeting_mcp.capture import get_capturer
        from claude_meeting_mcp.capture._macos import MacOSCapturer

        capturer = get_capturer()
        assert isinstance(capturer, MacOSCapturer)
