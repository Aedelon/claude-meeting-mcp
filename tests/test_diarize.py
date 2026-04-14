"""Tests for diarize module."""

from __future__ import annotations

from claude_meeting_mcp.diarize import assign_speakers_to_segments


def test_assign_single_speaker():
    """One speaker in diarization → all segments get that name."""
    whisper = [
        {"start": 0.0, "end": 2.0, "text": "Hello"},
        {"start": 3.0, "end": 5.0, "text": "How are you"},
    ]
    diar = [
        {"start": 0.0, "end": 5.0, "speaker": "SPEAKER_00"},
    ]
    result = assign_speakers_to_segments(whisper, diar, ["Bruno"], "remote")
    assert result[0]["speaker"] == "Bruno"
    assert result[1]["speaker"] == "Bruno"


def test_assign_two_speakers():
    """Two speakers alternate → correct names assigned."""
    whisper = [
        {"start": 0.0, "end": 2.0, "text": "Hello"},
        {"start": 3.0, "end": 5.0, "text": "Hi there"},
        {"start": 6.0, "end": 8.0, "text": "How are you"},
    ]
    diar = [
        {"start": 0.0, "end": 2.5, "speaker": "SPEAKER_00"},
        {"start": 2.5, "end": 5.5, "speaker": "SPEAKER_01"},
        {"start": 5.5, "end": 8.0, "speaker": "SPEAKER_00"},
    ]
    result = assign_speakers_to_segments(whisper, diar, ["Bruno", "Alice"], "remote")
    assert result[0]["speaker"] == "Bruno"
    assert result[1]["speaker"] == "Alice"
    assert result[2]["speaker"] == "Bruno"


def test_assign_more_speakers_than_names():
    """More pyannote speakers than provided names → fallback to prefix."""
    whisper = [
        {"start": 0.0, "end": 1.0, "text": "A"},
        {"start": 2.0, "end": 3.0, "text": "B"},
        {"start": 4.0, "end": 5.0, "text": "C"},
    ]
    diar = [
        {"start": 0.0, "end": 1.5, "speaker": "SPEAKER_00"},
        {"start": 1.5, "end": 3.5, "speaker": "SPEAKER_01"},
        {"start": 3.5, "end": 5.0, "speaker": "SPEAKER_02"},
    ]
    result = assign_speakers_to_segments(whisper, diar, ["Bruno"], "remote")
    assert result[0]["speaker"] == "Bruno"
    assert result[1]["speaker"] == "remote_2"
    assert result[2]["speaker"] == "remote_3"


def test_assign_empty_diarization():
    """No diarization results → use first name."""
    whisper = [{"start": 0.0, "end": 1.0, "text": "Hello"}]
    result = assign_speakers_to_segments(whisper, [], ["Bruno"], "remote")
    assert result[0]["speaker"] == "Bruno"


def test_assign_no_overlap():
    """Whisper segment has no overlap with any diarization → fallback."""
    whisper = [{"start": 10.0, "end": 12.0, "text": "Late segment"}]
    diar = [{"start": 0.0, "end": 5.0, "speaker": "SPEAKER_00"}]
    result = assign_speakers_to_segments(whisper, diar, ["Bruno"], "remote")
    assert result[0]["speaker"] == "Bruno"


def test_assign_majority_vote():
    """When a Whisper segment overlaps two speakers, the one with more overlap wins."""
    whisper = [{"start": 1.0, "end": 4.0, "text": "Shared segment"}]
    diar = [
        {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"},  # 1.0s overlap
        {"start": 2.0, "end": 5.0, "speaker": "SPEAKER_01"},  # 2.0s overlap → wins
    ]
    result = assign_speakers_to_segments(whisper, diar, ["Bruno", "Alice"], "remote")
    assert result[0]["speaker"] == "Alice"


def test_assign_preserves_text():
    """Speaker assignment preserves other segment fields."""
    whisper = [{"start": 0.0, "end": 1.0, "text": "Keep this"}]
    diar = [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]
    result = assign_speakers_to_segments(whisper, diar, ["Bruno"], "remote")
    assert result[0]["text"] == "Keep this"
    assert result[0]["start"] == 0.0
    assert result[0]["end"] == 1.0
