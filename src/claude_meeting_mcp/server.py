"""MCP Server exposing meeting recording and transcription tools."""

from __future__ import annotations

import json
import sys
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ClientCapabilities, SamplingCapability
from pydantic import Field

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
    instructions="""\
Meeting recording, transcription, and minutes generation.
Works with any video conferencing app (Google Meet, Teams, Zoom, Slack, Discord).
Respond in the user's language.

WORKFLOW — match user intent to the right tool:
- "record", "enregistre", "start" → meeting_record_start()
- "stop", "c'est fini", "done", "arrete" → meeting_stop_and_transcribe()
- "transcribe", "transcris" (existing file) → meeting_transcribe(file_path=...)
- "meeting minutes", "PV", "proces-verbal", "summary" → generate_meeting_pv()
- "actions", "todo", "to-do list" → extract_action_items prompt
- "status", "ca marche?", "ready?" → meeting_status()
- "settings", "config", "change model" → meeting_configure()
- "list", "history", "past meetings" → recordings_list() / transcriptions_list() / pvs_list()
- "cleanup", "delete old" → meeting_cleanup()

PARAMETERS:
- Always ask for participant names if not provided
- remote_speakers = people on the call (system audio, left channel)
- local_speakers = people in the room with the microphone (right channel)
- Prefer meeting_stop_and_transcribe over separate stop + transcribe calls
- After transcription, suggest generating meeting minutes
""",
)


# --- Recording & Transcription ---


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
        "diarization_enabled": config.diarization.enabled,
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
    file_path: Annotated[str, Field(description="Path to the WAV file to transcribe")],
    local_speakers: Annotated[
        str | None,
        Field(description="Comma-separated names of people at the mic (right channel)"),
    ] = None,
    remote_speakers: Annotated[
        str | None,
        Field(description="Comma-separated names of people on the call (left channel)"),
    ] = None,
    model: Annotated[
        str | None,
        Field(description="Whisper model: tiny, base, small, medium, large-v3-turbo, large-v3"),
    ] = None,
) -> dict:
    """Transcribe a recorded meeting WAV file.

    Splits stereo channels for automatic speaker attribution.
    Uses the configured Whisper backend (MLX on macOS, faster-whisper elsewhere).
    """
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
def meeting_stop_and_transcribe(
    local_speakers: Annotated[
        str | None,
        Field(description="Comma-separated names of people at the mic"),
    ] = None,
    remote_speakers: Annotated[
        str | None,
        Field(description="Comma-separated names of people on the call"),
    ] = None,
    model: Annotated[
        str | None,
        Field(description="Whisper model: tiny, base, small, medium, large-v3-turbo, large-v3"),
    ] = None,
) -> dict:
    """Stop a running recording and transcribe it in one step.

    More efficient than calling meeting_record_stop + meeting_transcribe separately.
    """
    stop_result = stop_recording()
    if "error" in stop_result:
        return stop_result

    file_path = stop_result["file"]
    return meeting_transcribe(file_path, local_speakers, remote_speakers, model)


# --- Retrieval ---


@mcp.tool()
def get_transcription(
    meeting_id: Annotated[str, "Meeting identifier (filename without .json)"],
) -> dict:
    """Retrieve a past transcription by meeting ID."""
    path = TRANSCRIPTIONS_DIR / f"{meeting_id}.json"
    if not path.exists():
        return {"error": f"Transcription not found: {meeting_id}"}
    t = Transcription.from_json(path.read_text(encoding="utf-8"))
    return t.to_dict()


@mcp.tool()
def get_pv(
    meeting_id: Annotated[str, "Meeting identifier"],
) -> dict:
    """Retrieve a previously generated meeting minutes (PV)."""
    pv_path = PV_DIR / f"{meeting_id}_pv.md"
    if not pv_path.exists():
        return {"error": f"PV not found for meeting: {meeting_id}"}
    return {
        "meeting_id": meeting_id,
        "pv_file": str(pv_path),
        "content": pv_path.read_text(encoding="utf-8"),
    }


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


# --- PV Generation ---


@mcp.tool()
async def generate_meeting_pv(
    ctx: Context,
    meeting_id: Annotated[str, Field(description="Meeting identifier (filename without .json)")],
    participants: Annotated[
        str | None,
        Field(description="Comma-separated names of known participants (helps identify speakers)"),
    ] = None,
) -> dict:
    """Generate meeting minutes (PV) from a transcription using AI.

    Uses MCP Sampling: the server asks Claude to summarize the transcription.
    Claude identifies who is who based on conversation content and participant names.
    For meetings under 1h: single pass. For longer meetings: map-reduce strategy.
    """
    path = TRANSCRIPTIONS_DIR / f"{meeting_id}.json"
    if not path.exists():
        return {"error": f"Transcription not found: {meeting_id}"}

    transcription = Transcription.from_json(path.read_text(encoding="utf-8"))

    if not ctx.session.check_client_capability(ClientCapabilities(sampling=SamplingCapability())):
        return {
            "error": "Client does not support MCP Sampling. "
            "Generate PV manually by reading the transcription."
        }

    known = [n.strip() for n in participants.split(",")] if participants else None

    pv_text = await generate_pv(ctx, transcription, known)
    pv_path = save_pv(meeting_id, pv_text)

    return {
        "meeting_id": meeting_id,
        "pv_file": pv_path,
        "pv_preview": pv_text[:500],
        "strategy": "direct" if transcription.duration_seconds < 3600 else "map-reduce",
    }


# --- Configuration ---


@mcp.tool()
def meeting_configure(
    key: Annotated[
        str,
        Field(description="Config key: whisper.model, whisper.mode, diarization.*"),
    ],
    value: Annotated[str, Field(description="New value for the config key")],
) -> dict:
    """Modify a claude-meeting-mcp configuration parameter."""
    try:
        config = update_config(key, value)
        errors = validate_config(config)
        if errors:
            return {"status": "warning", "key": key, "value": value, "warnings": errors}
        return {"status": "updated", "key": key, "value": value}
    except ValueError as e:
        return {"error": str(e)}


@mcp.tool()
def meeting_cleanup() -> dict:
    """Remove meeting audio recordings older than 30 days."""
    removed = cleanup_old_recordings()
    return {"removed_count": len(removed), "removed_files": removed}


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
    """Regenerate a PV with custom instructions."""
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
    """Extract only action items from a meeting."""
    path = TRANSCRIPTIONS_DIR / f"{meeting_id}.json"
    if not path.exists():
        return f"Transcription not found: {meeting_id}"
    t = Transcription.from_json(path.read_text(encoding="utf-8"))
    transcript = json.dumps(t.to_dict(), ensure_ascii=False, indent=2)
    return (
        f"Extrais uniquement les actions decidees dans cette reunion. "
        f"Format : - [ ] Action (responsable, deadline si mentionnee)\n\n{transcript}"
    )


def main():
    """Entry point for the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
