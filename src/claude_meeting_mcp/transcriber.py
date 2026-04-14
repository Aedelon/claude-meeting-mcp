"""Transcription with MLX-Whisper, dual-channel speaker attribution."""

import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

from .schemas import Segment, Transcription
from .storage import TRANSCRIPTIONS_DIR, ensure_dirs

MODEL_REPO = "mlx-community/whisper-large-v3-turbo"


def split_channels(wav_path: str) -> tuple[np.ndarray, np.ndarray, int]:
    """Split stereo WAV into left (system) and right (mic) channels."""
    data, samplerate = sf.read(wav_path)
    if data.ndim == 1:
        # Mono file: same audio for both channels
        return data, data, samplerate
    return data[:, 0], data[:, 1], samplerate


def transcribe_channel(audio: np.ndarray, samplerate: int) -> list[dict]:
    """Transcribe a single audio channel with MLX-Whisper."""
    import mlx_whisper

    # mlx_whisper expects float32 numpy array
    audio = audio.astype(np.float32)
    result = mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=MODEL_REPO,
        language="fr",
        word_timestamps=False,
    )
    return result.get("segments", [])


def merge_segments(left_segments: list[dict], right_segments: list[dict],
                   left_speaker: str, right_speaker: str) -> list[Segment]:
    """Merge two channel transcriptions into a single timeline sorted by start time."""
    segments = []
    for seg in left_segments:
        segments.append(Segment(
            start=round(seg["start"], 2),
            end=round(seg["end"], 2),
            speaker=left_speaker,
            text=seg["text"].strip(),
        ))
    for seg in right_segments:
        segments.append(Segment(
            start=round(seg["start"], 2),
            end=round(seg["end"], 2),
            speaker=right_speaker,
            text=seg["text"].strip(),
        ))
    segments.sort(key=lambda s: s.start)
    return segments


def transcribe_meeting(wav_path: str, left_speaker: str = "Bruno",
                       right_speaker: str = "Delanoe") -> Transcription:
    """Full transcription pipeline: split channels, transcribe each, merge."""
    ensure_dirs()
    wav = Path(wav_path)

    left_audio, right_audio, samplerate = split_channels(wav_path)
    duration = len(left_audio) / samplerate

    left_segments = transcribe_channel(left_audio, samplerate)
    right_segments = transcribe_channel(right_audio, samplerate)

    segments = merge_segments(left_segments, right_segments, left_speaker, right_speaker)

    meeting_id = wav.stem
    transcription = Transcription(
        meeting_id=meeting_id,
        date=meeting_id[:10] if len(meeting_id) >= 10 else "",
        duration_seconds=round(duration, 1),
        speakers={"left": left_speaker, "right": right_speaker},
        segments=segments,
    )

    # Save JSON
    output_path = TRANSCRIPTIONS_DIR / f"{meeting_id}.json"
    output_path.write_text(transcription.to_json(), encoding="utf-8")

    return transcription


def cli():
    """CLI entry point for manual transcription."""
    if len(sys.argv) < 2:
        print("Usage: transcribe <path_to_wav> [left_speaker] [right_speaker]")
        sys.exit(1)

    wav_path = sys.argv[1]
    left = sys.argv[2] if len(sys.argv) > 2 else "Bruno"
    right = sys.argv[3] if len(sys.argv) > 3 else "Delanoe"

    print(f"Transcribing {wav_path}...")
    result = transcribe_meeting(wav_path, left, right)
    print(f"Done. {len(result.segments)} segments, {result.duration_seconds}s")
    print(f"Saved to: transcriptions/{result.meeting_id}.json")
