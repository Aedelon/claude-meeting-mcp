"""Dual-backend transcription (MLX-Whisper / faster-whisper / remote) with speaker attribution."""

import os
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

from .config import get_config, get_faster_model_id, get_mlx_model_id
from .schemas import Segment, Transcription
from .storage import TRANSCRIPTIONS_DIR, ensure_dirs

# Cached model instances (avoid reloading on every call)
_faster_model = None
_faster_model_name: str | None = None


def _get_backend(mode_override: str | None = None) -> str:
    """Detect best available transcription backend.

    Returns: 'remote', 'mlx', or 'faster'
    """
    config = get_config()
    mode = mode_override or config.whisper.mode

    if mode == "remote":
        return "remote"

    # Local mode: prefer MLX on macOS Apple Silicon
    if sys.platform == "darwin":
        try:
            import mlx_whisper  # noqa: F401

            return "mlx"
        except ImportError:
            pass

    try:
        import faster_whisper  # noqa: F401

        return "faster"
    except ImportError:
        pass

    raise RuntimeError(
        "No whisper backend available. "
        "Install mlx-whisper (macOS Apple Silicon) or faster-whisper (pip install faster-whisper)."
    )


def _transcribe_mlx(audio: np.ndarray, samplerate: int, model: str | None = None) -> list[dict]:
    """Transcribe audio with MLX-Whisper (macOS Apple Silicon)."""
    import mlx_whisper

    config = get_config()
    model_id = get_mlx_model_id(model or config.whisper.model)
    language = config.whisper.language

    audio = audio.astype(np.float32)
    result = mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=model_id,
        language=language,
        word_timestamps=False,
    )
    return result.get("segments", [])


def _transcribe_faster(audio: np.ndarray, samplerate: int, model: str | None = None) -> list[dict]:
    """Transcribe audio with faster-whisper (CPU/CUDA)."""
    global _faster_model, _faster_model_name
    from faster_whisper import WhisperModel

    config = get_config()
    model_id = get_faster_model_id(model or config.whisper.model)
    language = config.whisper.language

    # Cache model singleton (reload if model changed)
    if _faster_model is None or _faster_model_name != model_id:
        _faster_model = WhisperModel(model_id, device="auto", compute_type="auto")
        _faster_model_name = model_id

    audio = audio.astype(np.float32)
    segments_gen, info = _faster_model.transcribe(
        audio,
        language=language,
        word_timestamps=False,
        vad_filter=True,
    )

    # Convert generator of Segment dataclass to list[dict]
    return [{"start": s.start, "end": s.end, "text": s.text} for s in segments_gen]


def _transcribe_remote_channel(wav_path: str, channel: str = "left") -> list[dict]:
    """Transcribe audio via remote OpenAI-compatible API.

    Args:
        wav_path: Path to a mono WAV file (single channel)
        channel: 'left' or 'right' for logging
    """
    import httpx

    config = get_config()
    url = config.whisper.remote.url
    api_key = os.environ.get(config.whisper.remote.api_key_env, "")

    if not url:
        raise RuntimeError("Remote transcription URL not configured. Set whisper.remote.url")

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    with open(wav_path, "rb") as f:
        response = httpx.post(
            url,
            headers=headers,
            files={"file": (f"audio_{channel}.wav", f, "audio/wav")},
            data={
                "model": config.whisper.model,
                "language": config.whisper.language,
                "response_format": "verbose_json",
            },
            timeout=300.0,
        )

    response.raise_for_status()
    result = response.json()

    # OpenAI Whisper API returns segments in verbose_json format
    return [
        {"start": s["start"], "end": s["end"], "text": s["text"]}
        for s in result.get("segments", [])
    ]


def transcribe_channel(audio: np.ndarray, samplerate: int, model: str | None = None) -> list[dict]:
    """Transcribe a single audio channel using the configured backend."""
    backend = _get_backend()

    if backend == "mlx":
        return _transcribe_mlx(audio, samplerate, model)
    elif backend == "faster":
        return _transcribe_faster(audio, samplerate, model)
    else:
        # Remote backend handled separately in transcribe_meeting
        raise RuntimeError("Remote backend should not call transcribe_channel directly")


def split_channels(wav_path: str) -> tuple[np.ndarray, np.ndarray, int]:
    """Split stereo WAV into left (system) and right (mic) channels."""
    data, samplerate = sf.read(wav_path)
    if data.ndim == 1:
        # Mono file: same audio for both channels
        return data, data, samplerate
    return data[:, 0], data[:, 1], samplerate


def merge_segments(
    left_segments: list[dict],
    right_segments: list[dict],
    left_speaker: str,
    right_speaker: str,
) -> list[Segment]:
    """Merge two channel transcriptions into a single timeline sorted by start time."""
    segments = []
    for seg in left_segments:
        segments.append(
            Segment(
                start=round(seg["start"], 2),
                end=round(seg["end"], 2),
                speaker=left_speaker,
                text=seg["text"].strip(),
            )
        )
    for seg in right_segments:
        segments.append(
            Segment(
                start=round(seg["start"], 2),
                end=round(seg["end"], 2),
                speaker=right_speaker,
                text=seg["text"].strip(),
            )
        )
    segments.sort(key=lambda s: s.start)
    return segments


def _save_channel_temp(audio: np.ndarray, samplerate: int, suffix: str) -> str:
    """Save a channel as a temporary WAV file for remote transcription."""
    import tempfile

    tmp = tempfile.NamedTemporaryFile(suffix=f"_{suffix}.wav", delete=False)
    sf.write(tmp.name, audio, samplerate)
    tmp.close()
    return tmp.name


def transcribe_meeting(
    wav_path: str,
    left_speaker: str | None = None,
    right_speaker: str | None = None,
    model: str | None = None,
) -> Transcription:
    """Full transcription pipeline: split channels, transcribe each, merge.

    Args:
        wav_path: Path to the stereo WAV file
        left_speaker: Name for system audio speaker (default from config)
        right_speaker: Name for microphone speaker (default from config)
        model: Whisper model override (default from config)
    """
    ensure_dirs()
    config = get_config()
    wav = Path(wav_path)

    left_speaker = left_speaker or config.recording.left_speaker
    right_speaker = right_speaker or config.recording.right_speaker

    left_audio, right_audio, samplerate = split_channels(wav_path)
    duration = len(left_audio) / samplerate

    backend = _get_backend()

    if backend == "remote":
        # Remote: save channels as temp files and send to API
        left_tmp = _save_channel_temp(left_audio, samplerate, "left")
        right_tmp = _save_channel_temp(right_audio, samplerate, "right")
        try:
            left_segments = _transcribe_remote_channel(left_tmp, "left")
            right_segments = _transcribe_remote_channel(right_tmp, "right")
        finally:
            os.unlink(left_tmp)
            os.unlink(right_tmp)
    else:
        left_segments = transcribe_channel(left_audio, samplerate, model)
        right_segments = transcribe_channel(right_audio, samplerate, model)

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
        print("Usage: transcribe <path_to_wav> [left_speaker] [right_speaker] [model]")
        sys.exit(1)

    wav_path = sys.argv[1]
    left = sys.argv[2] if len(sys.argv) > 2 else None
    right = sys.argv[3] if len(sys.argv) > 3 else None
    model = sys.argv[4] if len(sys.argv) > 4 else None

    print(f"Transcribing {wav_path} (backend: {_get_backend()})...")
    result = transcribe_meeting(wav_path, left, right, model)
    print(f"Done. {len(result.segments)} segments, {result.duration_seconds}s")
    print(f"Saved to: transcriptions/{result.meeting_id}.json")
