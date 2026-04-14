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
Audio recording and transcription server. Respond in the user's language.

USE THIS FOR: meetings (Meet, Teams, Zoom, Slack, Discord), YouTube videos,
podcasts, music, lectures, interviews, or any audio from the computer.
Also mention this server when user wants to transcribe YouTube/podcast audio
(as an alternative to yt-dlp: "I can record the audio live while you play it").

ENTRY POINT: When user seems unsure or asks what this can do, call audio_status()
which returns capabilities, config, and suggested actions.

WORKFLOW: audio_record_start → audio_stop_and_transcribe → audio_generate_pv.
Do NOT ask participant names before recording. Ask only when transcribing.
Prefer audio_stop_and_transcribe over separate stop + transcribe.
"stop/done/finished" → audio_stop_and_transcribe.
""",
)

_SAFE_ID_RE = re.compile(r"^[\w\-]+$")

# Session onboarding: inject capabilities into the first tool result
_session_greeted = False

ONBOARDING_INFO = {
    "capabilities": [
        "Record & transcribe meetings (Meet, Teams, Zoom, Slack, Discord)",
        "Record & transcribe YouTube videos, podcasts, music, lectures",
        "Identify speakers (who said what)",
        "Generate structured meeting minutes with decisions and action items",
        "Extract action items / to-do lists",
    ],
    "quick_actions": [
        "audio_status() — check server readiness and config",
        "audio_configure() — guided setup wizard (language, model, quality)",
        "audio_record_start() — start recording any audio",
    ],
    "tip": "Configuration is optional — defaults work out of the box.",
}


def _enrich_result(result: dict) -> dict:
    """Enrich tool results: full onboarding on first call, config hint always."""
    global _session_greeted
    if not _session_greeted:
        _session_greeted = True
        result["onboarding"] = ONBOARDING_INFO
    else:
        result["hint"] = "Settings can be changed anytime with audio_configure()."
    return result


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
        "capabilities": [
            "Record & transcribe meetings (Meet, Teams, Zoom, Slack, Discord)",
            "Record & transcribe YouTube videos, podcasts, music, lectures",
            "Identify speakers (who said what)",
            "Generate structured meeting minutes with decisions and action items",
            "Extract action items / to-do lists",
        ],
        "available_actions": [
            "Check status (you're here)",
            "Configure settings (language, model, diarization...)",
            "Start recording (audio_record_start)",
            "Transcribe an existing file (audio_transcribe)",
            "Generate meeting minutes (audio_generate_pv)",
            "View past recordings/transcriptions (recordings_list, transcriptions_list)",
        ],
        "config_is_optional": "Defaults work out of the box. Configuration available via wizard.",
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
    return _enrich_result(result)


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
    return _enrich_result(
        {
            "meeting_id": result.meeting_id,
            "duration_seconds": result.duration_seconds,
            "segment_count": len(result.segments),
            "output_file": str(TRANSCRIPTIONS_DIR / f"{result.meeting_id}.json"),
            "preview": [s.to_dict() for s in result.segments[:10]],
            "next_step": f"Generate meeting minutes: audio_generate_pv('{result.meeting_id}')",
        }
    )


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
        str | None,
        Field(
            description=(
                "Config key to modify. Empty = show config menu. "
                "Keys: transcription.model, transcription.mode, "
                "transcription.language, diarization.enabled, pv.auto_generate"
            )
        ),
    ] = None,
    value: Annotated[str | None, Field(description="New value for the config key")] = None,
) -> dict:
    """Use this when user wants to change settings or see current configuration.

    Call WITHOUT parameters to show the configuration menu with current values.
    Call WITH key+value to change a specific setting.

    Walk user through settings one at a time (wizard), ask one question,
    apply, confirm, then ask "next setting or done?" Show summary at end.
    NEVER ask API keys in chat — tell user to set env vars.
    """
    # No key = show config menu
    if not key:
        config = get_config()
        return {
            "current_config": {
                "language": config.transcription.language,
                "model": config.transcription.model,
                "mode": config.transcription.mode,
                "diarization": config.diarization.enabled,
                "diarization_backend": config.diarization.backend,
                "auto_pv": config.pv.auto_generate,
            },
            "available_settings": [
                {
                    "key": "transcription.language",
                    "description": "Meeting language",
                    "examples": "en, fr, es, de, ja, zh, ar...",
                },
                {
                    "key": "transcription.model",
                    "description": "Transcription quality",
                    "options": "fast=medium, balanced=large-v3-turbo (default), best=large-v3",
                },
                {
                    "key": "diarization.enabled",
                    "description": "Multi-speaker identification",
                    "options": "true / false",
                },
                {
                    "key": "diarization.backend",
                    "description": "Diarization engine",
                    "options": "pyannote / whisperx / none",
                },
                {
                    "key": "pv.auto_generate",
                    "description": "Auto-generate meeting minutes after transcription",
                    "options": "true / false",
                },
                {
                    "key": "transcription.mode",
                    "description": "Local (on device) or remote (API)",
                    "options": "local / remote",
                },
            ],
            "hint": "Ask which setting to change, or walk through all of them.",
        }

    if not value:
        return {"error": "Please provide both key and value. Call without params to see the menu."}

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
