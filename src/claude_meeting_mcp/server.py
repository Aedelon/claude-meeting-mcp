"""MCP Server exposing meeting recording and transcription tools."""

import json
import sys

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ClientCapabilities, SamplingCapability

from .capture import get_capturer
from .config import get_config, update_config, validate_config
from .pv_generator import generate_pv, save_pv
from .recorder import is_recording, start_recording, stop_recording
from .schemas import Transcription
from .storage import (
    PV_DIR,
    TRANSCRIPTIONS_DIR,
    cleanup_old_recordings,
    list_pvs,
    list_recordings,
    list_transcriptions,
)
from .transcriber import _get_backend, transcribe_meeting

mcp = FastMCP(
    "claude-meeting-mcp",
    instructions=(
        "Record meetings from any video conferencing app "
        "(Google Meet, Teams, Zoom, Slack, Discord, etc.) "
        "by capturing system audio + microphone. "
        "Transcribe with Whisper locally or via a remote API. "
        "Automatically generate meeting minutes (PV)."
    ),
)


@mcp.tool()
def meeting_status() -> dict:
    """Check meeting server status: platform, audio capture backend, transcription backend."""
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
def meeting_record_start() -> dict:
    """Start recording system audio and microphone.

    Creates a stereo WAV file: left channel = system audio, right channel = microphone.
    """
    return start_recording()


@mcp.tool()
def meeting_record_stop() -> dict:
    """Stop the current recording and save the WAV file."""
    return stop_recording()


@mcp.tool()
def meeting_transcribe(
    file_path: str,
    local_speakers: str | None = None,
    remote_speakers: str | None = None,
    model: str | None = None,
) -> dict:
    """Transcribe a recorded meeting WAV file.

    Splits stereo channels: left = remote (system audio), right = local (mic).
    Speaker names are per-meeting, passed as comma-separated strings.

    Args:
        file_path: Path to the WAV file to transcribe
        local_speakers: Comma-separated names of people at the mic (right channel)
        remote_speakers: Comma-separated names of people on the call (left channel)
        model: Whisper model override (default from config)
    """
    # Default names if not provided
    local = local_speakers or "Local"
    remote = remote_speakers or "Remote"
    result = transcribe_meeting(file_path, remote, local, model)
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
def meeting_record_and_transcribe(
    local_speakers: str | None = None,
    remote_speakers: str | None = None,
    model: str | None = None,
) -> dict:
    """Stop a running recording, then transcribe it immediately.

    Args:
        local_speakers: Comma-separated names of people at the mic
        remote_speakers: Comma-separated names of people on the call
        model: Whisper model override (default from config)
    """
    stop_result = stop_recording()
    if "error" in stop_result:
        return stop_result

    file_path = stop_result["file"]
    return meeting_transcribe(file_path, local_speakers, remote_speakers, model)


@mcp.tool()
def meeting_cleanup() -> dict:
    """Remove meeting audio recordings older than 30 days."""
    removed = cleanup_old_recordings()
    return {"removed_count": len(removed), "removed_files": removed}


@mcp.tool()
async def generate_meeting_pv(
    ctx: Context,
    meeting_id: str,
    participants: str | None = None,
) -> dict:
    """Generate a meeting minutes (PV) from a transcription using AI.

    The PV is generated automatically via MCP Sampling (server asks Claude to summarize).
    Claude identifies who is who based on conversation content and the participant list.

    Args:
        meeting_id: The meeting identifier (filename without .json extension)
        participants: Comma-separated names of known participants (helps Claude identify speakers)
    """
    # Load transcription
    path = TRANSCRIPTIONS_DIR / f"{meeting_id}.json"
    if not path.exists():
        return {"error": f"Transcription not found: {meeting_id}"}

    transcription = Transcription.from_json(path.read_text(encoding="utf-8"))

    # Check if client supports sampling
    if not ctx.session.check_client_capability(ClientCapabilities(sampling=SamplingCapability())):
        return {
            "error": "Client does not support MCP Sampling. "
            "Generate PV manually by reading the transcription."
        }

    # Parse known participants
    known = [n.strip() for n in participants.split(",")] if participants else None

    # Generate PV
    pv_text = await generate_pv(ctx, transcription, known)
    pv_path = save_pv(meeting_id, pv_text)

    return {
        "meeting_id": meeting_id,
        "pv_file": pv_path,
        "pv_preview": pv_text[:500],
        "strategy": "direct" if transcription.duration_seconds < 3600 else "map-reduce",
    }


@mcp.tool()
def get_pv(meeting_id: str) -> dict:
    """Retrieve a previously generated meeting minutes (PV).

    Args:
        meeting_id: The meeting identifier
    """
    pv_path = PV_DIR / f"{meeting_id}_pv.md"
    if not pv_path.exists():
        return {"error": f"PV not found for meeting: {meeting_id}"}
    return {
        "meeting_id": meeting_id,
        "pv_file": str(pv_path),
        "content": pv_path.read_text(encoding="utf-8"),
    }


# --- MCP Resources ---


@mcp.resource("transcription://{meeting_id}")
def transcription_resource(meeting_id: str) -> str:
    """Read a transcription as a formatted text resource."""
    path = TRANSCRIPTIONS_DIR / f"{meeting_id}.json"
    if not path.exists():
        return f"Transcription not found: {meeting_id}"
    t = Transcription.from_json(path.read_text(encoding="utf-8"))
    return json.dumps(t.to_dict(), ensure_ascii=False, indent=2)


@mcp.resource("pv://{meeting_id}")
def pv_resource(meeting_id: str) -> str:
    """Read a meeting minutes (PV) resource."""
    pv_path = PV_DIR / f"{meeting_id}_pv.md"
    if not pv_path.exists():
        return f"PV not found for meeting: {meeting_id}"
    return pv_path.read_text(encoding="utf-8")


# --- MCP Prompts ---


@mcp.prompt()
def regenerate_pv(meeting_id: str) -> str:
    """Regenerate a PV with custom instructions.

    Args:
        meeting_id: The meeting to regenerate PV for
    """
    path = TRANSCRIPTIONS_DIR / f"{meeting_id}.json"
    if not path.exists():
        return f"Transcription not found: {meeting_id}"
    t = Transcription.from_json(path.read_text(encoding="utf-8"))
    transcript = json.dumps(t.to_dict(), ensure_ascii=False, indent=2)
    return (
        f"Voici la transcription de la reunion {meeting_id}. "
        f"Genere un PV structure en markdown :\n\n{transcript}"
    )


@mcp.prompt()
def extract_action_items(meeting_id: str) -> str:
    """Extract only action items from a meeting.

    Args:
        meeting_id: The meeting to extract actions from
    """
    path = TRANSCRIPTIONS_DIR / f"{meeting_id}.json"
    if not path.exists():
        return f"Transcription not found: {meeting_id}"
    t = Transcription.from_json(path.read_text(encoding="utf-8"))
    transcript = json.dumps(t.to_dict(), ensure_ascii=False, indent=2)
    return (
        f"Extrais uniquement les actions decidees dans cette reunion. "
        f"Format : - [ ] Action (responsable, deadline si mentionnee)\n\n{transcript}"
    )


@mcp.tool()
def meeting_configure(key: str, value: str) -> dict:
    """Modify a claude-meeting-mcp configuration parameter.

    Args:
        key: Config key (e.g., 'whisper.model', 'diarization.enabled', 'diarization.backend')
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
