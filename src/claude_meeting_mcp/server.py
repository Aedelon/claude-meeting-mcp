"""MCP Server exposing meeting recording and transcription tools."""

from mcp.server.fastmcp import FastMCP

from .recorder import start_recording, stop_recording, is_recording, is_audiocap_available
from .transcriber import transcribe_meeting
from .storage import (
    list_recordings,
    list_transcriptions,
    cleanup_old_recordings,
    TRANSCRIPTIONS_DIR,
)
from .schemas import Transcription

mcp = FastMCP(
    "claude-meeting-mcp",
    description="Record meetings (mic + system audio) and transcribe locally with Whisper",
)


@mcp.tool()
def check_status() -> dict:
    """Check if the MCP server and audiocap binary are ready."""
    return {
        "audiocap_available": is_audiocap_available(),
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
def transcribe(file_path: str, left_speaker: str = "Bruno", right_speaker: str = "Delanoe") -> dict:
    """Transcribe a recorded meeting WAV file using MLX-Whisper.
    Splits stereo channels for automatic speaker attribution.

    Args:
        file_path: Path to the WAV file to transcribe
        left_speaker: Name of the person on the system audio (left channel)
        right_speaker: Name of the person on the microphone (right channel)
    """
    result = transcribe_meeting(file_path, left_speaker, right_speaker)
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
def record_and_transcribe(left_speaker: str = "Bruno", right_speaker: str = "Delanoe") -> str:
    """Stop a running recording, then transcribe it immediately.

    Args:
        left_speaker: Name for system audio speaker
        right_speaker: Name for microphone speaker
    """
    stop_result = stop_recording()
    if "error" in stop_result:
        return stop_result

    file_path = stop_result["file"]
    return transcribe(file_path, left_speaker, right_speaker)


@mcp.tool()
def cleanup() -> dict:
    """Remove audio recordings older than 30 days."""
    removed = cleanup_old_recordings()
    return {"removed_count": len(removed), "removed_files": removed}


def main():
    """Entry point for the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
