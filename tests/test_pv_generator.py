"""Tests for pv_generator module."""

from claude_meeting_mcp.pv_generator import (
    _format_time,
    format_segments_text,
    format_transcription_text,
    save_pv,
    split_transcription_by_duration,
)
from claude_meeting_mcp.schemas import Segment, Transcription


def _make_transcription(duration: float, segment_count: int = 4) -> Transcription:
    """Helper to create a test transcription."""
    interval = duration / segment_count if segment_count > 0 else 0
    segments = []
    for i in range(segment_count):
        start = i * interval
        end = start + interval * 0.9
        speaker = "Alice" if i % 2 == 0 else "Bob"
        segments.append(Segment(start=start, end=end, speaker=speaker, text=f"Segment {i + 1}"))
    return Transcription(
        meeting_id="test-meeting",
        date="2026-04-14",
        duration_seconds=duration,
        speakers={"left": "Alice", "right": "Bob"},
        segments=segments,
    )


def test_format_time_seconds():
    assert _format_time(0) == "0:00"
    assert _format_time(65) == "1:05"
    assert _format_time(3661) == "1:01:01"


def test_format_transcription_text():
    t = _make_transcription(120, 2)
    text = format_transcription_text(t)
    assert "Alice" in text
    assert "Segment 1" in text
    assert "Segment 2" in text


def test_format_segments_text():
    segments = [
        Segment(start=0.0, end=1.0, speaker="Alice", text="Hello"),
        Segment(start=2.0, end=3.0, speaker="Bob", text="Hi"),
    ]
    text = format_segments_text(segments)
    assert "Alice: Hello" in text
    assert "Bob: Hi" in text


def test_split_short_meeting():
    """Short meeting should not be split."""
    t = _make_transcription(1800, 10)  # 30 min
    chunks = split_transcription_by_duration(t, 1800)
    assert len(chunks) == 1
    assert len(chunks[0]) == 10


def test_split_long_meeting():
    """3-hour meeting should be split into multiple chunks."""
    t = _make_transcription(10800, 12)  # 3h, 12 segments
    chunks = split_transcription_by_duration(t, 1800)  # 30 min chunks
    assert len(chunks) >= 2
    # All segments accounted for
    total = sum(len(c) for c in chunks)
    assert total == 12


def test_split_empty():
    t = _make_transcription(0, 0)
    chunks = split_transcription_by_duration(t)
    assert chunks == []


def test_save_pv(tmp_path, monkeypatch):
    import claude_meeting_mcp.pv_generator as pv_mod
    import claude_meeting_mcp.storage as storage

    pv_dir = tmp_path / "pv"
    storage.PV_DIR = pv_dir
    pv_mod.PV_DIR = pv_dir

    pv_text = "# PV de reunion\n\n## Decisions\n- Decision 1"
    path = save_pv("test-meeting", pv_text)

    assert "test-meeting_pv.md" in path
    assert (pv_dir / "test-meeting_pv.md").exists()

    content = (pv_dir / "test-meeting_pv.md").read_text()
    assert "Decision 1" in content
