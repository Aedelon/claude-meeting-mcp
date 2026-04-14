"""Tests for transcriber module."""
from claude_meeting_mcp.schemas import Segment, Transcription


def test_segment_to_dict():
    seg = Segment(start=0.0, end=1.5, speaker="Bruno", text="Bonjour")
    d = seg.to_dict()
    assert d["speaker"] == "Bruno"
    assert d["start"] == 0.0


def test_transcription_json_roundtrip():
    t = Transcription(
        meeting_id="test",
        date="2026-04-14",
        duration_seconds=60.0,
        segments=[Segment(start=0.0, end=1.5, speaker="Bruno", text="Test")],
    )
    json_str = t.to_json()
    t2 = Transcription.from_json(json_str)
    assert t2.meeting_id == "test"
    assert len(t2.segments) == 1
    assert t2.segments[0].text == "Test"
