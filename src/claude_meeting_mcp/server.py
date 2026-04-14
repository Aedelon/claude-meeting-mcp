"""MCP Server exposing meeting recording and transcription tools."""

from __future__ import annotations

import json
import re
import sys
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
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
Use this server when the user wants to record, transcribe, or summarize audio.
Respond in the user's language.

USE THIS FOR: meetings (Meet, Teams, Zoom, Slack, Discord), YouTube videos,
podcasts, music, lectures, interviews, or any audio from the computer.

ON FIRST USE: present 3 choices:
1. Check status (audio_status) 2. Configure (wizard) 3. Start action
Explain capabilities: record audio, transcribe, identify speakers, generate
meeting minutes, extract action items. Config is optional, defaults work.

WORKFLOW: audio_record_start → audio_stop_and_transcribe (ask participants) →
suggest audio_generate_pv → suggest extract_action_items.
Do NOT ask participant names before recording. Ask only when transcribing.

WIZARD (if user picks configure): one question at a time, apply, confirm, next.
1. Language (transcription.language) 2. Quality: fast=medium, balanced=large-v3-turbo, best=large-v3
3. Multi-speaker (diarization.enabled + backend) 4. Auto-PV (pv.auto_generate)
5. Local/remote (transcription.mode). NEVER ask API keys in chat — tell user to
set TRANSCRIPTION_API_KEY/HF_TOKEN in env or Claude Desktop config.

RULES:
- Prefer audio_stop_and_transcribe over separate stop + transcribe
- "transcribe" while recording → audio_stop_and_transcribe
- "transcribe" without recording → audio_transcribe (suggest most recent)
- "stop/done/finished" → audio_stop_and_transcribe
""",
)

_SAFE_ID_RE = re.compile(r"^[\w\-]+$")


def _validate_meeting_id(meeting_id: str) -> str | None:
    """Validate meeting_id to prevent path traversal. Returns error or None."""
    if not meeting_id or ".." in meeting_id or "/" in meeting_id or "\\" in meeting_id:
        return "Invalid meeting_id: must not contain path separators"
    if not _SAFE_ID_RE.match(meeting_id):
        return "Invalid meeting_id: only alphanumeric, hyphens, underscores allowed"
    return None


# --- Recording & Transcription ---


@mcp.tool()
def audio_status() -> dict:
    """Use this to check if the audio server is ready and show current config.

    Call this when user asks about status, setup, or what this server can do.
    Returns: platform, audio backend, transcription engine, model, language,
    diarization state, disk space, and last recording ID.
    """
    import shutil

    capturer = get_capturer()
    config = get_config()

    # Disk space
    disk = shutil.disk_usage(TRANSCRIPTIONS_DIR)
    disk_free_gb = round(disk.free / (1024**3), 1)

    # Last recording for disambiguation
    recs = list_recordings()
    last_recording = recs[0]["meeting_id"] if recs else None

    return {
        "platform": sys.platform,
        "audio_capture_available": capturer.is_available(),
        "audio_capture_backend": type(capturer).__name__,
        "transcription_backend": _get_backend(),
        "transcription_model": config.transcription.model,
        "transcription_mode": config.transcription.mode,
        "transcription_language": config.transcription.language,
        "diarization_enabled": config.diarization.enabled,
        "currently_recording": is_recording(),
        "disk_free_gb": disk_free_gb,
        "last_recording": last_recording,
    }


@mcp.tool()
def audio_record_start() -> dict:
    """Use this when the user wants to record audio playing on the computer.

    Works with: meetings (Meet/Teams/Zoom/Slack/Discord), YouTube, podcasts,
    Spotify, lectures, interviews — any system audio. Also captures microphone.
    Stereo WAV output: left channel = system audio, right channel = microphone.
    """
    result = start_recording()
    if "error" not in result:
        result["next_step"] = "When done, call audio_stop_and_transcribe()"
    return result


@mcp.tool()
def audio_record_stop() -> dict:
    """Use this to stop recording WITHOUT transcribing.

    Saves the WAV file and returns its path. Prefer audio_stop_and_transcribe()
    which stops AND transcribes in one call.
    """
    result = stop_recording()
    if "error" not in result:
        result["next_step"] = "Transcribe with audio_transcribe(file_path=...)"
    return result


@mcp.tool()
def audio_transcribe(
    file_path: Annotated[str, Field(description="Path to the WAV file to transcribe")],
    local_speakers: Annotated[
        str | None,
        Field(
            description=(
                "Comma-separated names of people at the microphone (right channel). "
                "Example: 'Alice, Bob'"
            )
        ),
    ] = None,
    remote_speakers: Annotated[
        str | None,
        Field(
            description=(
                "Comma-separated names of people on the call (left channel). "
                "Example: 'Charlie, Diana'"
            )
        ),
    ] = None,
    model: Annotated[
        str | None,
        Field(
            description="Transcription model: tiny, base, small, medium, large-v3-turbo, large-v3"
        ),
    ] = None,
) -> dict:
    """Use this to transcribe a WAV file that already exists on disk.

    For recordings just made, use audio_stop_and_transcribe() instead.
    Works on any audio: meetings, YouTube, podcasts, lectures, interviews.
    Splits stereo channels for speaker attribution. With diarization,
    identifies individual speakers within each channel.
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
        "next_step": f"Generate meeting minutes: audio_generate_pv('{result.meeting_id}')",
    }


@mcp.tool()
def audio_stop_and_transcribe(
    local_speakers: Annotated[
        str | None,
        Field(
            description=("Comma-separated names of people at the microphone. Example: 'Alice, Bob'")
        ),
    ] = None,
    remote_speakers: Annotated[
        str | None,
        Field(
            description=("Comma-separated names of people on the call. Example: 'Charlie, Diana'")
        ),
    ] = None,
    model: Annotated[
        str | None,
        Field(
            description="Transcription model: tiny, base, small, medium, large-v3-turbo, large-v3"
        ),
    ] = None,
) -> dict:
    """Use this when the user says stop/done/finished — stops AND transcribes.

    This is the recommended way to end a recording session. One call does both.
    Returns transcription preview and suggests generating meeting minutes next.
    """
    stop_result = stop_recording()
    if "error" in stop_result:
        return stop_result

    file_path = stop_result["file"]
    return audio_transcribe(file_path, local_speakers, remote_speakers, model)


# --- Retrieval ---


@mcp.tool()
def get_transcription(
    meeting_id: Annotated[str, Field(description="Meeting identifier (filename without .json)")],
) -> dict:
    """Use this to read a past transcription. Returns full text with timestamps and speakers."""
    if err := _validate_meeting_id(meeting_id):
        return {"error": err}
    path = TRANSCRIPTIONS_DIR / f"{meeting_id}.json"
    if not path.exists():
        return {"error": f"Transcription not found: {meeting_id}"}
    t = Transcription.from_json(path.read_text(encoding="utf-8"))
    return t.to_dict()


@mcp.tool()
def get_pv(
    meeting_id: Annotated[str, Field(description="Meeting identifier")],
) -> dict:
    """Use this to read previously generated meeting minutes (PV) as markdown."""
    if err := _validate_meeting_id(meeting_id):
        return {"error": err}
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
    """Use this to see all past recordings. Returns meeting_id (for other tools), size, date."""
    return list_recordings()


@mcp.tool()
def transcriptions_list() -> list[dict]:
    """Use this to see all past transcriptions. Returns meeting_id (for get_transcription), date."""
    return list_transcriptions()


@mcp.tool()
def pvs_list() -> list[dict]:
    """Use this to see all generated meeting minutes. Returns meeting_id (for get_pv), date."""
    return list_pvs()


# --- PV Generation ---


@mcp.tool()
async def audio_generate_pv(
    ctx: Context,
    meeting_id: Annotated[str, Field(description="Meeting identifier (filename without .json)")],
    participants: Annotated[
        str | None,
        Field(
            description=(
                "Comma-separated names of all meeting participants. "
                "Helps Claude identify who said what. Example: 'Alice, Bob, Charlie'"
            )
        ),
    ] = None,
) -> dict:
    """Use this after transcription to generate structured meeting minutes.

    Produces markdown with: participants, topics, decisions, action items.
    Claude identifies speakers by conversation content (no voice enrollment).
    Short audio (<1h): single pass. Longer: automatic map-reduce.
    """
    if err := _validate_meeting_id(meeting_id):
        return {"error": err}
    path = TRANSCRIPTIONS_DIR / f"{meeting_id}.json"
    if not path.exists():
        return {"error": f"Transcription not found: {meeting_id}"}

    transcription = Transcription.from_json(path.read_text(encoding="utf-8"))

    # Check if client supports sampling
    has_sampling = (
        ctx.session.client_params is not None
        and ctx.session.client_params.capabilities is not None
        and ctx.session.client_params.capabilities.sampling is not None
    )
    if not has_sampling:
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
        "next_step": f"Extract action items with extract_action_items prompt for '{meeting_id}'",
    }


# --- Configuration ---


@mcp.tool()
def audio_configure(
    key: Annotated[
        str,
        Field(
            description=(
                "Config key to modify. "
                "Options: transcription.model, transcription.mode, transcription.language, "
                "diarization.enabled, diarization.backend, recording.sample_rate, "
                "pv.auto_generate, transcription.remote.url, transcription.remote.api_key_env"
            )
        ),
    ],
    value: Annotated[str, Field(description="New value for the config key")],
) -> dict:
    """Use this when user wants to change settings (language, model, quality, etc.).

    Key examples: transcription.language='fr', transcription.model='large-v3-turbo',
    diarization.enabled='true', transcription.mode='remote'.
    """
    try:
        config = update_config(key, value)
        errors = validate_config(config)
        if errors:
            return {"status": "warning", "key": key, "value": value, "warnings": errors}
        return {"status": "updated", "key": key, "value": value}
    except ValueError as e:
        return {"error": str(e)}


@mcp.tool()
def audio_cleanup() -> dict:
    """Use this to free disk space by removing old recordings (>30 days).

    Only deletes WAV audio files. Transcriptions and minutes are kept.
    """
    removed = cleanup_old_recordings()
    return {"removed_count": len(removed), "removed_files": removed}


# --- MCP Resources ---


@mcp.resource("transcription://{meeting_id}")
def transcription_resource(meeting_id: str) -> str:
    """Read a transcription as a formatted text resource."""
    if _validate_meeting_id(meeting_id):
        return f"Invalid meeting_id: {meeting_id}"
    path = TRANSCRIPTIONS_DIR / f"{meeting_id}.json"
    if not path.exists():
        return f"Transcription not found: {meeting_id}"
    t = Transcription.from_json(path.read_text(encoding="utf-8"))
    return json.dumps(t.to_dict(), ensure_ascii=False, indent=2)


@mcp.resource("pv://{meeting_id}")
def pv_resource(meeting_id: str) -> str:
    """Read a meeting minutes (PV) resource."""
    if _validate_meeting_id(meeting_id):
        return f"Invalid meeting_id: {meeting_id}"
    pv_path = PV_DIR / f"{meeting_id}_pv.md"
    if not pv_path.exists():
        return f"PV not found for meeting: {meeting_id}"
    return pv_path.read_text(encoding="utf-8")


# --- MCP Prompts ---


@mcp.prompt()
def regenerate_pv(meeting_id: str) -> str:
    """Regenerate meeting minutes with custom instructions.

    Use this when the user wants a different format, language, or focus.
    The user can add instructions after this prompt is loaded.
    """
    path = TRANSCRIPTIONS_DIR / f"{meeting_id}.json"
    if not path.exists():
        return f"Transcription not found: {meeting_id}"
    t = Transcription.from_json(path.read_text(encoding="utf-8"))
    transcript = json.dumps(t.to_dict(), ensure_ascii=False, indent=2)
    return (
        f"Here is the transcription of meeting {meeting_id}. "
        f"Generate structured meeting minutes in the user's language.\n\n"
        f"{transcript}"
    )


@mcp.prompt()
def extract_action_items(meeting_id: str) -> str:
    """Extract action items and tasks from a meeting.

    Returns a checklist of actions with responsible person and deadline.
    """
    path = TRANSCRIPTIONS_DIR / f"{meeting_id}.json"
    if not path.exists():
        return f"Transcription not found: {meeting_id}"
    t = Transcription.from_json(path.read_text(encoding="utf-8"))
    transcript = json.dumps(t.to_dict(), ensure_ascii=False, indent=2)
    return (
        f"Extract all action items from this meeting. "
        f"Format: - [ ] Action (responsible person, deadline if mentioned)\n"
        f"Respond in the user's language.\n\n{transcript}"
    )


def main():
    """Entry point for the MCP server."""
    # Auto-cleanup old recordings on startup
    removed = cleanup_old_recordings()
    if removed:
        import logging

        logging.getLogger(__name__).info("Auto-cleanup: removed %d old recordings", len(removed))

    mcp.run()


if __name__ == "__main__":
    main()
