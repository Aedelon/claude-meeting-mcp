"""Automatic meeting minutes (PV) generation via MCP Sampling."""

from mcp.server.fastmcp import Context
from mcp.types import SamplingMessage, TextContent

from .schemas import Segment, Transcription
from .storage import PV_DIR, ensure_dirs

CHUNK_DURATION_SECONDS = 1800  # 30 minutes
SHORT_MEETING_THRESHOLD = 3600  # 1 hour

PV_SYSTEM_PROMPT = """\
You are an expert meeting minutes writer.
IMPORTANT: Write the meeting minutes in the SAME LANGUAGE as the transcription.

SPEAKER IDENTIFICATION:
The transcription uses generic labels (remote_1, remote_2, local_1, etc.).
A list of known participants may be provided. You MUST identify who is who based on:
- What each speaker talks about (role, topic, expertise)
- Meeting context
- When someone is addressed by name in the conversation
Replace generic labels with real names in the minutes.
If you cannot identify a speaker, keep the generic label.

OUTPUT FORMAT (structured markdown):
- **Date** and **Duration**
- **Participants** (real names identified)
- **Topics discussed** (summarized by theme, with speaker attribution)
- **Decisions made** (numbered list)
- **Action items** (who, what, deadline if mentioned)

Be factual, concise, and preserve important nuances.
Do not add information that is not in the transcription."""

CHUNK_SUMMARY_PROMPT = """\
Summarize the key points of this meeting transcription excerpt.
Write in the SAME LANGUAGE as the transcription.
Preserve: decisions, actions, disagreements, and factual information.
Indicate which speaker said what when relevant."""

SYNTHESIS_PROMPT = """\
From these partial summaries of the same meeting, generate final meeting minutes.
Write in the SAME LANGUAGE as the summaries.
- **Date** and **Duration**
- **Participants**
- **Topics discussed** (summarized by theme, with attribution)
- **Decisions made** (numbered list)
- **Action items** (who, what, deadline if mentioned)
Merge information without duplicates. Be factual and concise."""


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
