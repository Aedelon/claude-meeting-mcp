"""MCP Server exposing meeting recording and transcription tools."""

import sys

from mcp.server.fastmcp import FastMCP

from .capture import get_capturer
from .config import get_config, update_config, validate_config
from .recorder import is_recording, start_recording, stop_recording
from .schemas import Transcription
from .storage import (
    TRANSCRIPTIONS_DIR,
    cleanup_old_recordings,
    list_pvs,
    list_recordings,
    list_transcriptions,
)
from .transcriber import _get_backend, transcribe_meeting

mcp = FastMCP(
    "claude-meeting-mcp",
    instructions="Record meetings (mic + system audio) and transcribe locally with Whisper",
)


@mcp.tool()
def check_status() -> dict:
    """Check server status: platform, audio capture backend, transcription backend."""
    capturer = get_capturer()
    config = get_config()
    return {
        "platform": sys.platform,
        "audio_capture_available": capturer.is_available(),
        "audio_capture_backend": type(capturer).__name__,
        "transcription_backend": _get_backend(),
        "whisper_model": config.whisper.model,
        "whisper_mode": config.whisper.mode,
        "currently_recording": is_recording(),
    }


@mcp.tool()
def record_start() -> dict:
    """Start recording system audio and microphone.

    Creates a stereo WAV file: left channel = system audio, right channel = microphone.
    """
    return start_recording()


@mcp.tool()
def record_stop() -> dict:
    """Stop the current recording and save the WAV file."""
    return stop_recording()


@mcp.tool()
def transcribe(
    file_path: str,
    left_speaker: str | None = None,
    right_speaker: str | None = None,
    model: str | None = None,
) -> dict:
    """Transcribe a recorded meeting WAV file.

    Splits stereo channels for automatic speaker attribution.
    Uses the configured Whisper backend (MLX on macOS, faster-whisper elsewhere).

    Args:
        file_path: Path to the WAV file to transcribe
        left_speaker: Name for system audio speaker (default from config)
        right_speaker: Name for microphone speaker (default from config)
        model: Whisper model override (default from config)
    """
    result = transcribe_meeting(file_path, left_speaker, right_speaker, model)
    return {
        "meeting_id": result.meeting_id,
        "duration_seconds": result.duration_seconds,
        "segment_count": len(result.segments),
        "output_file": str(TRANSCRIPTIONS_DIR / f"{result.meeting_id}.json"),
        "preview": [s.to_dict() for s in result.segments[:10]],
    }


@mcp.tool()
def get_transcription(meeting_id: str) -> dict:
    """Retrieve a past transcription by meeting ID.

    Args:
        meeting_id: The meeting identifier (filename without .json extension)
    """
    path = TRANSCRIPTIONS_DIR / f"{meeting_id}.json"
    if not path.exists():
        return {"error": f"Transcription not found: {meeting_id}"}
    t = Transcription.from_json(path.read_text(encoding="utf-8"))
    return t.to_dict()


@mcp.tool()
def recordings_list() -> list[dict]:
    """List all available audio recordings with metadata."""
    return list_recordings()


@mcp.tool()
def transcriptions_list() -> list[dict]:
    """List all available transcriptions."""
    return list_transcriptions()


@mcp.tool()
def pvs_list() -> list[dict]:
    """List all available meeting minutes (PV) files."""
    return list_pvs()


@mcp.tool()
def record_and_transcribe(
    left_speaker: str | None = None,
    right_speaker: str | None = None,
    model: str | None = None,
) -> dict:
    """Stop a running recording, then transcribe it immediately.

    Args:
        left_speaker: Name for system audio speaker (default from config)
        right_speaker: Name for microphone speaker (default from config)
        model: Whisper model override (default from config)
    """
    stop_result = stop_recording()
    if "error" in stop_result:
        return stop_result

    file_path = stop_result["file"]
    return transcribe(file_path, left_speaker, right_speaker, model)


@mcp.tool()
def cleanup() -> dict:
    """Remove audio recordings older than 30 days."""
    removed = cleanup_old_recordings()
    return {"removed_count": len(removed), "removed_files": removed}


@mcp.tool()
def configure(key: str, value: str) -> dict:
    """Modify a configuration parameter.

    Args:
        key: Config key (e.g., 'whisper.model', 'whisper.mode', 'recording.left_speaker')
        value: New value
    """
    try:
        config = update_config(key, value)
        errors = validate_config(config)
        if errors:
            return {"status": "warning", "key": key, "value": value, "warnings": errors}
        return {"status": "updated", "key": key, "value": value}
    except ValueError as e:
        return {"error": str(e)}


def main():
    """Entry point for the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
