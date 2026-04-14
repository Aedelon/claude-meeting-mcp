"""Automatic meeting minutes (PV) generation via MCP Sampling."""

from mcp.server.fastmcp import Context
from mcp.types import SamplingMessage, TextContent

from .schemas import Segment, Transcription
from .storage import PV_DIR, ensure_dirs

CHUNK_DURATION_SECONDS = 1800  # 30 minutes
SHORT_MEETING_THRESHOLD = 3600  # 1 hour

PV_SYSTEM_PROMPT = """Tu es un assistant specialise dans la redaction de proces-verbaux de reunion.

IMPORTANT — Resolution des identites :
La transcription utilise des labels generiques (remote_1, remote_2, local_1, etc.).
Une liste de participants connus est fournie. Tu DOIS identifier qui est qui en te basant sur :
- Le contenu de ce que chaque speaker dit (role, sujet, expertise)
- Le contexte de la reunion
- Les indices dans la conversation (quand quelqu'un s'adresse a un autre par son nom)
Remplace les labels generiques par les vrais noms dans le PV.
Si tu ne peux pas identifier un speaker, garde le label generique.

Genere un PV structure en markdown avec :
- **Date** et **Duree**
- **Participants** (vrais noms identifies)
- **Points discutes** (resume par theme, avec attribution au vrai nom du speaker)
- **Decisions prises** (liste numerotee)
- **Actions a suivre** (qui, quoi, deadline si mentionnee)
Sois factuel, concis, et preserve les nuances importantes.
Ne rajoute pas d'information qui ne figure pas dans la transcription."""

CHUNK_SUMMARY_PROMPT = """Resume les points cles de cet extrait de transcription de reunion.
Conserve : les decisions, les actions, les points de desaccord, et les informations factuelles.
Indique quel speaker a dit quoi quand c'est pertinent."""

SYNTHESIS_PROMPT = """A partir de ces resumes partiels d'une meme reunion, genere un PV final :
- **Date** et **Duree**
- **Participants**
- **Points discutes** (resume par theme, avec attribution)
- **Decisions prises** (liste numerotee)
- **Actions a suivre** (qui, quoi, deadline si mentionnee)
Unifie les informations sans doublons. Sois factuel et concis."""


def format_transcription_text(transcription: Transcription) -> str:
    """Format transcription segments as readable text for the LLM."""
    lines = []
    for seg in transcription.segments:
        timestamp = f"[{_format_time(seg.start)} - {_format_time(seg.end)}]"
        lines.append(f"{timestamp} {seg.speaker}: {seg.text}")
    return "\n".join(lines)


def format_segments_text(segments: list[Segment]) -> str:
    """Format a subset of segments as readable text."""
    lines = []
    for seg in segments:
        timestamp = f"[{_format_time(seg.start)} - {_format_time(seg.end)}]"
        lines.append(f"{timestamp} {seg.speaker}: {seg.text}")
    return "\n".join(lines)


def _format_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def split_transcription_by_duration(
    transcription: Transcription, chunk_seconds: float = CHUNK_DURATION_SECONDS
) -> list[list[Segment]]:
    """Split transcription segments into time-based chunks."""
    if not transcription.segments:
        return []

    chunks: list[list[Segment]] = []
    current_chunk: list[Segment] = []
    chunk_start = 0.0

    for seg in transcription.segments:
        if seg.start >= chunk_start + chunk_seconds and current_chunk:
            chunks.append(current_chunk)
            current_chunk = []
            chunk_start = seg.start
        current_chunk.append(seg)

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


async def _call_sampling(
    ctx: Context,
    user_text: str,
    system_prompt: str,
    max_tokens: int = 4096,
) -> str:
    """Call MCP sampling to get LLM-generated text."""
    result = await ctx.session.create_message(
        messages=[
            SamplingMessage(
                role="user",
                content=TextContent(type="text", text=user_text),
            )
        ],
        max_tokens=max_tokens,
        system_prompt=system_prompt,
    )

    # Extract text from result
    if hasattr(result.content, "text"):
        return result.content.text
    return str(result.content)


async def generate_pv_direct(
    ctx: Context,
    transcription: Transcription,
    known_participants: list[str] | None = None,
) -> str:
    """Generate PV for short meetings (<1h) in a single sampling call."""
    transcript_text = format_transcription_text(transcription)

    participants_info = ", ".join(transcription.speakers.values())
    known_info = ""
    if known_participants:
        names = ", ".join(known_participants)
        known_info = f"Participants connus (a identifier): {names}\n"

    metadata = (
        f"Date: {transcription.date}\n"
        f"Duree: {transcription.duration_seconds / 60:.0f} minutes\n"
        f"Labels dans la transcription: {participants_info}\n"
        f"{known_info}\n"
    )

    return await _call_sampling(
        ctx,
        user_text=metadata + "Transcription:\n\n" + transcript_text,
        system_prompt=PV_SYSTEM_PROMPT,
    )


async def generate_pv_map_reduce(
    ctx: Context,
    transcription: Transcription,
    known_participants: list[str] | None = None,
) -> str:
    """Generate PV for long meetings (>=1h) using map-reduce strategy."""
    chunks = split_transcription_by_duration(transcription, CHUNK_DURATION_SECONDS)

    known_info = ""
    if known_participants:
        known_info = f"\nParticipants connus: {', '.join(known_participants)}\n"

    # Map: summarize each chunk
    partial_summaries = []
    for i, chunk in enumerate(chunks):
        chunk_text = format_segments_text(chunk)
        start_time = _format_time(chunk[0].start) if chunk else "0:00"
        end_time = _format_time(chunk[-1].end) if chunk else "0:00"

        summary = await _call_sampling(
            ctx,
            user_text=(
                f"Bloc {i + 1}/{len(chunks)} ({start_time} - {end_time}):{known_info}\n{chunk_text}"
            ),
            system_prompt=CHUNK_SUMMARY_PROMPT,
            max_tokens=2048,
        )
        partial_summaries.append(f"## Bloc {i + 1} ({start_time} - {end_time})\n{summary}")

    # Reduce: synthesize all summaries into final PV
    all_summaries = "\n\n".join(partial_summaries)
    participants_info = ", ".join(transcription.speakers.values())
    metadata = (
        f"Date: {transcription.date}\n"
        f"Duree: {transcription.duration_seconds / 60:.0f} minutes\n"
        f"Labels: {participants_info}\n"
        f"{known_info}\n"
    )

    return await _call_sampling(
        ctx,
        user_text=metadata + "Resumes partiels:\n\n" + all_summaries,
        system_prompt=SYNTHESIS_PROMPT,
    )


async def generate_pv(
    ctx: Context,
    transcription: Transcription,
    known_participants: list[str] | None = None,
) -> str:
    """Generate PV using the appropriate strategy based on meeting duration."""
    if transcription.duration_seconds < SHORT_MEETING_THRESHOLD:
        return await generate_pv_direct(ctx, transcription, known_participants)
    return await generate_pv_map_reduce(ctx, transcription, known_participants)


def save_pv(meeting_id: str, pv_text: str) -> str:
    """Save generated PV to file. Returns the file path."""
    ensure_dirs()
    pv_path = PV_DIR / f"{meeting_id}_pv.md"
    pv_path.write_text(pv_text, encoding="utf-8")
    return str(pv_path)
