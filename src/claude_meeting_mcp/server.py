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
Meeting recording, transcription, and minutes generation for any video conferencing app.
Always respond in the user's language.

INTENT ROUTING — trigger words by language → tool:

Record / start:
  EN: "record", "start recording" | FR: "enregistre", "demarre" | ES: "graba", "empieza"
  IT: "registra", "inizia" | PT: "grava", "comeca" | RU: "запиши", "начни"
  ZH: "录音", "开始录制" | HE: "הקלט", "התחל הקלטה"
  → meeting_record_start()

Stop / finish:
  EN: "stop", "done", "finish" | FR: "stop", "c'est fini", "arrete" | ES: "para", "termina"
  IT: "ferma", "finito" | PT: "para", "terminou" | RU: "стоп", "закончи"
  ZH: "停止", "结束" | HE: "עצור", "סיים"
  → meeting_stop_and_transcribe() (preferred) or meeting_record_stop()

Transcribe:
  EN: "transcribe" | FR: "transcris" | ES: "transcribe" | IT: "trascrivi"
  PT: "transcreve" | RU: "транскрибируй" | ZH: "转录" | HE: "תמלל"
  → meeting_transcribe(file_path=...) for existing files
  → meeting_stop_and_transcribe() after a recording

Meeting minutes / summary:
  EN: "minutes", "summary" | FR: "PV", "proces-verbal", "compte-rendu"
  ES: "acta", "resumen" | IT: "verbale", "riassunto" | PT: "ata", "resumo"
  RU: "протокол", "резюме" | ZH: "会议纪要", "总结" | HE: "פרוטוקול", "סיכום"
  → generate_meeting_pv()

Action items / tasks:
  EN: "actions", "todo" | FR: "actions", "taches" | ES: "tareas", "acciones"
  IT: "azioni", "compiti" | PT: "acoes", "tarefas" | RU: "задачи", "действия"
  ZH: "行动项", "任务" | HE: "משימות", "פעולות"
  → extract_action_items prompt

Status / check:
  EN: "status", "ready?" | FR: "statut", "ca marche?" | ES: "estado", "funciona?"
  IT: "stato", "funziona?" | PT: "status", "funciona?" | RU: "статус", "работает?"
  ZH: "状态", "准备好了吗" | HE: "סטטוס", "מוכן?"
  → meeting_status()

Settings:
  EN: "settings", "config" | FR: "configuration", "parametres"
  ES: "configuracion", "ajustes" | IT: "configurazione", "impostazioni"
  PT: "configuracao" | RU: "настройки" | ZH: "设置", "配置" | HE: "הגדרות"
  → meeting_configure()

History:
  EN: "list", "history", "past meetings" | FR: "liste", "historique", "reunions passees"
  ES: "lista", "historial" | IT: "lista", "storico" | PT: "lista", "historico"
  RU: "список", "история" | ZH: "列表", "历史" | HE: "רשימה", "היסטוריה"
  → recordings_list() / transcriptions_list() / pvs_list()

Cleanup:
  EN: "cleanup", "delete old" | FR: "nettoyer", "supprimer" | ES: "limpiar", "borrar"
  IT: "pulisci", "elimina" | PT: "limpar", "apagar" | RU: "очистить", "удалить"
  ZH: "清理", "删除旧的" | HE: "נקה", "מחק"
  → meeting_cleanup()

PARAMETERS:
- Always ask for participant names if not provided
- remote_speakers = people on the call (system audio, left channel)
- local_speakers = people in the room with the microphone (right channel)
- Prefer meeting_stop_and_transcribe over separate stop + transcribe calls
- After transcription, suggest generating meeting minutes
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
    """Check meeting server status.

    Returns platform, audio capture backend, transcription backend,
    Whisper model, diarization state, and recording state.
    """
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

    Captures all audio from the computer (any app: Meet, Teams, Zoom, etc.)
    plus the microphone into a stereo WAV file.
    Left channel = system/remote audio. Right channel = microphone/local audio.
    """
    return start_recording()


@mcp.tool()
def meeting_record_stop() -> dict:
    """Stop the current recording and save the WAV file.

    Use meeting_stop_and_transcribe() instead if you also want to transcribe.
    """
    return stop_recording()


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
        Field(description="Whisper model: tiny, base, small, medium, large-v3-turbo, large-v3"),
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
        Field(description="Whisper model: tiny, base, small, medium, large-v3-turbo, large-v3"),
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
    }


# --- Configuration ---


@mcp.tool()
def meeting_configure(
    key: Annotated[
        str,
        Field(
            description=(
                "Config key to modify. "
                "Options: whisper.model, whisper.mode, whisper.language, "
                "diarization.enabled, diarization.backend, recording.sample_rate, "
                "pv.auto_generate, whisper.remote.url, whisper.remote.api_key_env"
            )
        ),
    ],
    value: Annotated[str, Field(description="New value for the config key")],
) -> dict:
    """Modify a claude-meeting-mcp configuration parameter.

    Common operations:
    - Change Whisper model: key='whisper.model', value='small'
    - Enable diarization: key='diarization.enabled', value='true'
    - Switch to remote: key='whisper.mode', value='remote'
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
