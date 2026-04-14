"""Tests for server module."""

import sys
from unittest.mock import MagicMock, patch


def test_audio_status_keys():
    """Verify audio_status returns all expected keys."""
    with (
        patch("claude_meeting_mcp.server.get_capturer") as mock_capturer,
        patch("claude_meeting_mcp.server._get_backend", return_value="mlx"),
        patch("claude_meeting_mcp.server.is_recording", return_value=False),
    ):
        mock_cap = MagicMock()
        mock_cap.is_available.return_value = True
        mock_capturer.return_value = mock_cap

        from claude_meeting_mcp.server import audio_status

        result = audio_status()
        assert "platform" in result
        assert "audio_capture_available" in result
        assert "audio_capture_backend" in result
        assert "transcription_backend" in result
        assert "transcription_model" in result
        assert "currently_recording" in result
        assert result["platform"] == sys.platform
        assert result["transcription_backend"] == "mlx"


def test_audio_configure_valid_key():
    """Verify audio_configure updates config correctly."""
    from claude_meeting_mcp.server import audio_configure

    with patch("claude_meeting_mcp.server.update_config") as mock_update:
        from claude_meeting_mcp.config import Config

        mock_update.return_value = Config()
        result = audio_configure("whisper.model", "small")
        assert result["status"] == "updated"


def test_audio_configure_invalid_key():
    """Verify audio_configure returns error for invalid key."""
    from claude_meeting_mcp.server import audio_configure

    with patch(
        "claude_meeting_mcp.server.update_config",
        side_effect=ValueError("Unknown config key: bad.key"),
    ):
        result = audio_configure("bad.key", "value")
        assert "error" in result
