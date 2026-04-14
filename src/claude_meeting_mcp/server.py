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
Meeting recording, transcription, and minutes (PV) server. Any conferencing app.
Respond in the user's language.

GREETING: When the user mentions meetings/recording, present these choices:
1. Check server status (meeting_status)
2. Configure settings (guided setup wizard)
3. Start recording / transcribe / generate minutes
Mention that configuration is optional — defaults work out of the box.

CONFIGURATION WIZARD: When user picks "configure", walk through step by step.
Ask ONE question at a time, wait for answer, apply with meeting_configure, then next.
Step 1: "What language are your meetings in?" → transcription.language (fr, en, es, de, ja...)
Step 2: "Transcription quality?" → fast (medium), balanced (large-v3-turbo), best (large-v3)
Step 3: "Multiple speakers per side?" → diarization.enabled + ask backend (pyannote/whisperx)
Step 4: "Auto-generate meeting minutes after transcription?" → pv.auto_generate (true/false)
Step 5: "Local or remote transcription?" → if remote: ask for API URL only.
  SECURITY: Never ask for API keys in the chat. Tell the user to set the env var themselves:
  "Set your API key: export TRANSCRIPTION_API_KEY=your-key-here"
After each step, confirm and ask "Next setting, or all done?"
Show final config summary at the end.

WORKFLOW: record → stop+transcribe (ask participants) → suggest PV → suggest action items
- Start recording immediately. Do NOT ask for participant names before recording.
- Ask for participants only when stopping/transcribing.
- remote_speakers = people on the call (left channel), local_speakers = at the mic (right channel).
- Prefer meeting_stop_and_transcribe() over separate stop + transcribe.
- After transcription, always suggest generating meeting minutes with generate_meeting_pv().

DISAMBIGUATION:
- User wants to transcribe while recording is active → meeting_stop_and_transcribe()
- User wants to transcribe with no active recording → meeting_transcribe() (suggest most recent)
- User says stop/done/finished → meeting_stop_and_transcribe() (not just meeting_record_stop)
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
def meeting_status() -> dict:
    """Check meeting server status and readiness.

    Returns platform, backends, config, recording state, disk space,
    and most recent recording (for disambiguation).
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
def meeting_record_start() -> dict:
    """Start recording system audio and microphone.

    Captures all audio from the computer (any app: Meet, Teams, Zoom, etc.)
    plus the microphone into a stereo WAV file.
    Left channel = system/remote audio. Right channel = microphone/local audio.
    """
    result = start_recording()
    if "error" not in result:
        result["next_step"] = "When the meeting is over, call meeting_stop_and_transcribe()"
    return result


@mcp.tool()
def meeting_record_stop() -> dict:
    """Stop the current recording and save the WAV file.

    Use meeting_stop_and_transcribe() instead if you also want to transcribe.
    """
    result = stop_recording()
    if "error" not in result:
        result["next_step"] = "Transcribe with meeting_transcribe(file_path=...)"
    return result


@mcp.tool()
def meeting_transcribe(
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
    """Transcribe an existing meeting WAV file.

    Splits stereo channels for speaker attribution.
    If diarization is enabled, identifies individual speakers per channel.
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
        "next_step": f"Generate meeting minutes: generate_meeting_pv('{result.meeting_id}')",
    }


@mcp.tool()
def meeting_stop_and_transcribe(
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
    """Stop the recording and transcribe it in one step.

    Preferred over calling meeting_record_stop + meeting_transcribe separately.
    Single round-trip for the complete stop-and-transcribe pipeline.
    """
    stop_result = stop_recording()
    if "error" in stop_result:
        return stop_result

    file_path = stop_result["file"]
    return meeting_transcribe(file_path, local_speakers, remote_speakers, model)


# --- Retrieval ---


@mcp.tool()
def get_transcription(
    meeting_id: Annotated[str, Field(description="Meeting identifier (filename without .json)")],
) -> dict:
    """Retrieve a past transcription by meeting ID."""
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
    """Retrieve a previously generated meeting minutes (PV)."""
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
    """List all available audio recordings with size and date."""
    return list_recordings()


@mcp.tool()
def transcriptions_list() -> list[dict]:
    """List all available transcriptions with date."""
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
        Field(
            description=(
                "Comma-separated names of all meeting participants. "
                "Helps Claude identify who said what. Example: 'Alice, Bob, Charlie'"
            )
        ),
    ] = None,
) -> dict:
    """Generate meeting minutes (PV) from a transcription using AI.

    Uses MCP Sampling: the server asks Claude to analyze the transcription,
    identify speakers by their conversation content, and produce structured
    meeting minutes with decisions, action items, and speaker attribution.
    For meetings under 1h: single pass. For longer: map-reduce strategy.
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
def meeting_configure(
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
    """Modify a claude-meeting-mcp configuration parameter.

    Common operations:
    - Change transcription model: key='transcription.model', value='small'
    - Enable diarization: key='diarization.enabled', value='true'
    - Switch to remote API: key='transcription.mode', value='remote'
    - Set remote API URL: key='transcription.remote.url', value='https://api.groq.com/...'
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
def meeting_cleanup() -> dict:
    """Remove meeting audio recordings older than 30 days.

    Transcriptions and PVs are kept indefinitely.
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
    mcp.run()


if __name__ == "__main__":
    main()
