"""Tests for live translator module."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from claude_meeting_mcp.live_translator import FileAudioSource, LiveTranslator


def _create_test_wav(path: str, duration: float = 1.0, sr: int = 48000) -> None:
    """Create a minimal WAV file for testing."""
    import soundfile as sf

    samples = int(sr * duration)
    audio = np.sin(2 * np.pi * 440 * np.linspace(0, duration, samples)).astype(np.float32)
    stereo = np.column_stack([audio, audio])
    sf.write(path, stereo, sr, subtype="PCM_16")


def test_file_audio_source_reads_wav(tmp_path):
    """FileAudioSource reads PCM data from a WAV file."""
    wav_path = str(tmp_path / "test.wav")
    _create_test_wav(wav_path, duration=0.5, sr=48000)

    source = FileAudioSource(wav_path, sample_rate=48000, channels=2)
    audio = source.get_new_audio()

    assert audio is not None
    assert len(audio) > 0
    assert audio.dtype == np.float32


def test_file_audio_source_returns_none_if_no_file():
    """FileAudioSource returns None if file doesn't exist."""
    source = FileAudioSource("/nonexistent/path.wav")
    assert source.get_new_audio() is None


def test_file_audio_source_incremental_read(tmp_path):
    """FileAudioSource tracks position and returns only new data."""
    wav_path = str(tmp_path / "test.wav")
    _create_test_wav(wav_path, duration=1.0, sr=48000)

    source = FileAudioSource(wav_path, sample_rate=48000, channels=2)

    # First read: gets all data
    audio1 = source.get_new_audio()
    assert audio1 is not None
    assert len(audio1) > 0

    # Second read: no new data
    audio2 = source.get_new_audio()
    assert audio2 is None


def test_file_audio_source_is_active():
    """FileAudioSource active state management."""
    source = FileAudioSource("/some/path.wav")
    assert source.is_active()
    source.deactivate()
    assert not source.is_active()


def test_live_translator_get_status(tmp_path):
    """LiveTranslator returns status dict."""
    wav_path = str(tmp_path / "test.wav")
    _create_test_wav(wav_path, duration=0.1)

    source = FileAudioSource(wav_path, sample_rate=48000)
    output = str(tmp_path / "live.md")

    translator = LiveTranslator(
        source=source,
        output_path=output,
        target_language="en",
        model="small",
    )

    # Before start
    status = translator.get_status()
    assert "status" in status
    assert "target_language" in status
    assert status["target_language"] == "en"
    assert status["live_file"] == output


def test_live_translator_writes_markdown(tmp_path):
    """LiveTranslator creates a markdown file on start."""
    wav_path = str(tmp_path / "test.wav")
    _create_test_wav(wav_path, duration=0.1)

    source = FileAudioSource(wav_path, sample_rate=48000)
    output = str(tmp_path / "live.md")

    translator = LiveTranslator(
        source=source,
        output_path=output,
        target_language="en",
    )

    # Manually call write_markdown to test without starting thread
    translator._start_time = time.monotonic()
    translator._write_markdown()

    assert Path(output).exists()
    content = Path(output).read_text()
    assert "Live Translation" in content
    assert "translating" in content.lower()


def test_live_translator_stop_writes_final(tmp_path):
    """LiveTranslator writes final markdown with Completed status on stop."""
    wav_path = str(tmp_path / "test.wav")
    _create_test_wav(wav_path, duration=0.1)

    source = FileAudioSource(wav_path, sample_rate=48000)
    output = str(tmp_path / "live.md")

    translator = LiveTranslator(
        source=source,
        output_path=output,
        target_language="en",
    )

    translator._start_time = time.monotonic()
    translator._write_markdown(final=True)

    content = Path(output).read_text()
    assert "Completed" in content
    assert "translating..." not in content.lower()
