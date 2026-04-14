"""Dual-backend transcription (MLX-Whisper / faster-whisper / remote) with speaker attribution."""

import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import soundfile as sf

from .config import get_config, get_faster_model_id, get_mlx_model_id
from .schemas import Segment, Transcription
from .storage import TRANSCRIPTIONS_DIR, ensure_dirs

logger = logging.getLogger(__name__)

# Cached model instances (avoid reloading on every call)
_faster_model = None
_faster_model_name: str | None = None


def _get_backend(mode_override: str | None = None) -> str:
    """Detect best available transcription backend.

    Returns: 'remote', 'mlx', or 'faster'
    """
    config = get_config()
    mode = mode_override or config.transcription.mode

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
    model_id = get_mlx_model_id(model or config.transcription.model)
    language = config.transcription.language

    audio = audio.astype(np.float32, copy=False)
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
    model_id = get_faster_model_id(model or config.transcription.model)
    language = config.transcription.language

    # Cache model singleton (reload if model changed)
    if _faster_model is None or _faster_model_name != model_id:
        _faster_model = WhisperModel(model_id, device="auto", compute_type="auto")
        _faster_model_name = model_id

    audio = audio.astype(np.float32, copy=False)
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
    url = config.transcription.remote.url
    api_key = os.environ.get(config.transcription.remote.api_key_env, "")

    if not url:
        raise RuntimeError("Remote transcription URL not configured. Set transcription.remote.url")

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    with open(wav_path, "rb") as f:
        response = httpx.post(
            url,
            headers=headers,
            files={"file": (f"audio_{channel}.wav", f, "audio/wav")},
            data={
                "model": config.transcription.model,
                "language": config.transcription.language,
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


def _can_parallelize() -> bool:
    """Check if dual-channel transcription can be parallelized."""
    backend = _get_backend()
    if backend == "remote":
        return True
    if backend == "mlx":
        return False  # MLX monopolizes Metal GPU
    if backend == "faster":
        # CTranslate2 releases the GIL — safe to parallelize
        # Pre-load model if not yet loaded so first call benefits too
        if _faster_model is None:
            _ensure_faster_model()
        return True
    return False


def _ensure_faster_model() -> None:
    """Ensure faster-whisper model is loaded (for parallelization check)."""
    global _faster_model, _faster_model_name
    if _faster_model is not None:
        return
    from faster_whisper import WhisperModel

    config = get_config()
    model_id = get_faster_model_id(config.transcription.model)
    _faster_model = WhisperModel(model_id, device="auto", compute_type="auto")
    _faster_model_name = model_id
    logger.info("Faster-whisper model loaded: %s", model_id)


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
    data, samplerate = sf.read(wav_path, dtype="float32")
    if data.ndim == 1:
        return data, data.copy(), samplerate
    left = np.ascontiguousarray(data[:, 0])
    right = np.ascontiguousarray(data[:, 1])
    del data  # Free stereo buffer immediately (~1 GB for 3h recording)
    return left, right, samplerate


def merge_segments(
    left_segments: list[dict],
    right_segments: list[dict],
    left_speaker: str,
    right_speaker: str,
) -> list[Segment]:
    """Merge two channel transcriptions into a single timeline sorted by start time.

    If segments already have a "speaker" key (from diarization), it is used.
    Otherwise, left_speaker/right_speaker is applied as fallback.
    """
    segments = []
    for seg in left_segments:
        speaker = seg.get("speaker") or left_speaker
        segments.append(
            Segment(
                start=round(seg["start"], 2),
                end=round(seg["end"], 2),
                speaker=speaker,
                text=seg["text"].strip(),
            )
        )
    for seg in right_segments:
        speaker = seg.get("speaker") or right_speaker
        segments.append(
            Segment(
                start=round(seg["start"], 2),
                end=round(seg["end"], 2),
                speaker=speaker,
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


def _diarize_and_assign(
    audio: np.ndarray,
    samplerate: int,
    whisper_segments: list[dict],
    speaker_names: list[str],
    channel_prefix: str,
) -> list[dict]:
    """Run diarization on a channel and assign speakers to Whisper segments."""
    from .diarize import assign_speakers_to_segments, diarize_channel

    diar_segments = diarize_channel(audio, samplerate)
    return assign_speakers_to_segments(
        whisper_segments, diar_segments, speaker_names, channel_prefix
    )


def transcribe_meeting(
    wav_path: str,
    remote_speakers: str | None = None,
    local_speakers: str | None = None,
    model: str | None = None,
) -> Transcription:
    """Full transcription pipeline: split channels, transcribe each, merge.

    Args:
        wav_path: Path to the stereo WAV file
        remote_speakers: Comma-separated names for system audio (left channel)
        local_speakers: Comma-separated names for microphone (right channel)
        model: Whisper model override (default from config)
    """
    ensure_dirs()
    config = get_config()
    wav = Path(wav_path)

    remote_label = remote_speakers or "Remote"
    local_label = local_speakers or "Local"
    remote_names = [n.strip() for n in remote_label.split(",")]
    local_names = [n.strip() for n in local_label.split(",")]

    left_audio, right_audio, samplerate = split_channels(wav_path)
    duration = len(left_audio) / samplerate

    logger.info("Transcribing %s (%.1fs, %d Hz)", wav_path, duration, samplerate)
    t0 = time.monotonic()
    backend = _get_backend()

    if backend == "remote":
        left_tmp = _save_channel_temp(left_audio, samplerate, "left")
        right_tmp = _save_channel_temp(right_audio, samplerate, "right")
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                fl = pool.submit(_transcribe_remote_channel, left_tmp, "left")
                fr = pool.submit(_transcribe_remote_channel, right_tmp, "right")
                left_segments = fl.result()
                right_segments = fr.result()
        finally:
            os.unlink(left_tmp)
            os.unlink(right_tmp)
    elif _can_parallelize():
        logger.info("Parallel transcription (backend=%s)", backend)
        with ThreadPoolExecutor(max_workers=2) as pool:
            fl = pool.submit(transcribe_channel, left_audio, samplerate, model)
            fr = pool.submit(transcribe_channel, right_audio, samplerate, model)
            left_segments = fl.result()
            right_segments = fr.result()
    else:
        logger.info("Sequential transcription (backend=%s)", backend)
        left_segments = transcribe_channel(left_audio, samplerate, model)
        right_segments = transcribe_channel(right_audio, samplerate, model)

    # Apply diarization if enabled
    if config.diarization.enabled:
        left_segments = _diarize_and_assign(
            left_audio, samplerate, left_segments, remote_names, "remote"
        )
        right_segments = _diarize_and_assign(
            right_audio, samplerate, right_segments, local_names, "local"
        )
        segments = merge_segments(left_segments, right_segments, "", "")
    else:
        segments = merge_segments(left_segments, right_segments, remote_label, local_label)

    # Collect all unique speaker names
    all_speakers = sorted(set(s.speaker for s in segments))
    speakers_dict = {f"speaker_{i}": name for i, name in enumerate(all_speakers)}

    meeting_id = wav.stem
    transcription = Transcription(
        meeting_id=meeting_id,
        date=meeting_id[:10] if len(meeting_id) >= 10 else "",
        duration_seconds=round(duration, 1),
        speakers=speakers_dict,
        segments=segments,
    )

    # Save JSON
    output_path = TRANSCRIPTIONS_DIR / f"{meeting_id}.json"
    output_path.write_text(transcription.to_json(), encoding="utf-8")

    elapsed = time.monotonic() - t0
    logger.info(
        "Transcription complete: %d segments, %.1fs elapsed (backend=%s)",
        len(segments),
        elapsed,
        backend,
    )
    return transcription


def cli():
    """CLI entry point for manual transcription."""
    if len(sys.argv) < 2:
        print("Usage: transcribe <path_to_wav> [remote_speakers] [local_speakers] [model]")
        sys.exit(1)

    wav_path = sys.argv[1]
    remote = sys.argv[2] if len(sys.argv) > 2 else None
    local = sys.argv[3] if len(sys.argv) > 3 else None
    model = sys.argv[4] if len(sys.argv) > 4 else None

    print(f"Transcribing {wav_path} (backend: {_get_backend()})...")
    result = transcribe_meeting(wav_path, remote, local, model)
    print(f"Done. {len(result.segments)} segments, {result.duration_seconds}s")
    print(f"Saved to: transcriptions/{result.meeting_id}.json")
