"""Speaker diarization via pyannote-audio 3.1.

Identifies individual speakers within a single audio channel.
Used after Whisper transcription to sub-divide per-channel segments
into per-speaker segments.
"""

from __future__ import annotations

import os

import numpy as np

# Cached pipeline singleton
_pipeline = None


def _get_pipeline():
    """Lazy-load pyannote diarization pipeline (cached)."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline

    try:
        from pyannote.audio import Pipeline
    except ImportError:
        raise RuntimeError(
            "pyannote-audio not installed. Run: uv add pyannote-audio\n"
            "Then accept the model license at https://huggingface.co/pyannote/speaker-diarization-3.1"
        )

    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if not token:
        raise RuntimeError(
            "HuggingFace token required for pyannote. "
            "Set HF_TOKEN environment variable. "
            "Get a free token at https://huggingface.co/settings/tokens"
        )

    _pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        token=token,
    )
    return _pipeline


def diarize_channel(audio: np.ndarray, samplerate: int) -> list[dict]:
    """Run pyannote diarization on a single audio channel.

    Args:
        audio: Mono float32 audio array
        samplerate: Sample rate in Hz

    Returns:
        List of diarization segments:
        [{"start": 0.5, "end": 2.3, "speaker": "SPEAKER_00"}, ...]
    """
    pipeline = _get_pipeline()

    # pyannote expects a file path or dict {"waveform": tensor, "sample_rate": int}
    # Using dict avoids temp file I/O
    import torch

    waveform = torch.from_numpy(audio.astype(np.float32)).unsqueeze(0)  # (1, samples)
    audio_input = {"waveform": waveform, "sample_rate": samplerate}

    annotation = pipeline(audio_input)

    segments = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        segments.append(
            {
                "start": round(turn.start, 2),
                "end": round(turn.end, 2),
                "speaker": speaker,
            }
        )

    return segments


def assign_speakers_to_segments(
    whisper_segments: list[dict],
    diarization: list[dict],
    speaker_names: list[str],
    channel_prefix: str = "",
) -> list[dict]:
    """Assign speaker names to Whisper segments using pyannote diarization.

    For each Whisper segment, finds the pyannote speaker with the most
    temporal overlap (majority vote). Then maps pyannote labels
    (SPEAKER_00, SPEAKER_01...) to provided speaker names.

    Args:
        whisper_segments: [{"start", "end", "text"}, ...]
        diarization: [{"start", "end", "speaker"}, ...]
        speaker_names: Names to assign (e.g., ["Bruno", "Alice"])
        channel_prefix: Prefix for unnamed speakers (e.g., "remote" → "remote_3")

    Returns:
        Same segments with "speaker" field updated
    """
    if not diarization:
        # No diarization results — use first name or prefix
        default_name = speaker_names[0] if speaker_names else channel_prefix or "Unknown"
        return [{**seg, "speaker": default_name} for seg in whisper_segments]

    # Build mapping: pyannote label → speaker name
    # Ordered by first appearance in diarization
    seen_labels: list[str] = []
    for d in diarization:
        if d["speaker"] not in seen_labels:
            seen_labels.append(d["speaker"])

    label_to_name: dict[str, str] = {}
    for i, label in enumerate(seen_labels):
        if i < len(speaker_names):
            label_to_name[label] = speaker_names[i]
        else:
            label_to_name[label] = (
                f"{channel_prefix}_{i + 1}" if channel_prefix else f"Speaker_{i + 1}"
            )

    # Assign speaker to each Whisper segment via overlap voting
    result = []
    for seg in whisper_segments:
        seg_start = seg["start"]
        seg_end = seg["end"]

        # Compute overlap duration with each pyannote speaker
        overlap: dict[str, float] = {}
        for d in diarization:
            ov_start = max(seg_start, d["start"])
            ov_end = min(seg_end, d["end"])
            if ov_end > ov_start:
                label = d["speaker"]
                overlap[label] = overlap.get(label, 0.0) + (ov_end - ov_start)

        if overlap:
            # Winner: speaker with most overlap
            best_label = max(overlap, key=overlap.get)
            speaker_name = label_to_name.get(best_label, best_label)
        else:
            # No overlap found — use default
            speaker_name = speaker_names[0] if speaker_names else channel_prefix or "Unknown"

        result.append({**seg, "speaker": speaker_name})

    return result
