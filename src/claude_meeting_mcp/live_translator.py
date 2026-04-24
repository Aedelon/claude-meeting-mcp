"""Live translation during recording.

Reads audio from a growing WAV file (macOS) or deque buffers (Win/Linux),
transcribes+translates with Whisper in real-time, and writes results
to a live-updating markdown file.
"""

from __future__ import annotations

import logging
import os
import struct
import threading
import time
from pathlib import Path
from typing import Protocol

import numpy as np

logger = logging.getLogger(__name__)


# --- Audio Sources ---


class LiveAudioSource(Protocol):
    """Interface for reading audio during recording."""

    def get_new_audio(self) -> np.ndarray | None:
        """Return new audio samples since last call, or None if no new data."""
        ...

    def get_sample_rate(self) -> int:
        """Return the sample rate of the audio source."""
        ...

    def is_active(self) -> bool:
        """Return True if the source is still producing audio."""
        ...


class FileAudioSource:
    """Reads audio from a WAV file that is being written to (macOS audiocap).

    Tracks byte offset and reads new PCM data on each call.
    """

    def __init__(self, wav_path: str, sample_rate: int = 48000, channels: int = 2) -> None:
        self._path = wav_path
        self._sample_rate = sample_rate
        self._channels = channels
        self._bytes_per_sample = 2  # 16-bit PCM
        self._frame_size = channels * self._bytes_per_sample
        self._data_offset: int | None = None  # byte offset where PCM data starts
        self._read_offset: int = 0  # bytes read so far past data_offset
        self._active = True

    def _find_data_offset(self) -> int | None:
        """Find the 'data' chunk offset in a WAV file."""
        try:
            with open(self._path, "rb") as f:
                header = f.read(44)
                if len(header) < 44:
                    return None
                # Standard WAV: 'data' chunk starts at byte 36, data at byte 44
                # But some WAVs have extra chunks. Search for 'data' marker.
                f.seek(12)  # past RIFF header
                while True:
                    chunk_header = f.read(8)
                    if len(chunk_header) < 8:
                        return None
                    chunk_id = chunk_header[:4]
                    chunk_size = struct.unpack("<I", chunk_header[4:8])[0]
                    if chunk_id == b"data":
                        return f.tell()
                    f.seek(chunk_size, 1)  # skip chunk
        except (OSError, struct.error):
            return None

    def get_new_audio(self) -> np.ndarray | None:
        if not os.path.exists(self._path):
            return None

        # Find data offset on first successful read
        if self._data_offset is None:
            self._data_offset = self._find_data_offset()
            if self._data_offset is None:
                return None
            self._read_offset = 0

        try:
            file_size = os.path.getsize(self._path)
            available = file_size - self._data_offset - self._read_offset
            # Align to frame boundary
            available = (available // self._frame_size) * self._frame_size
            if available <= 0:
                return None

            # Cap read size to ~5 seconds to avoid loading huge files at once
            max_bytes = self._sample_rate * 5 * self._frame_size
            read_size = min(available, max_bytes)

            with open(self._path, "rb") as f:
                f.seek(self._data_offset + self._read_offset)
                raw = f.read(read_size)

            self._read_offset += len(raw)

            # Decode 16-bit PCM to float32
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            # Reshape to (frames, channels) and take left channel (system audio)
            if self._channels > 1:
                samples = samples.reshape(-1, self._channels)[:, 0]

            return samples

        except OSError:
            return None

    def get_sample_rate(self) -> int:
        return self._sample_rate

    def is_active(self) -> bool:
        return self._active

    def deactivate(self) -> None:
        self._active = False


# --- Live Translator ---


class LiveTranslator:
    """Transcribes and translates audio in real-time during recording.

    Runs in a daemon thread. Reads audio chunks from a LiveAudioSource,
    accumulates in a ring buffer, transcribes every N seconds, and writes
    results to a live-updating markdown file.
    """

    def __init__(
        self,
        source: LiveAudioSource,
        output_path: str,
        target_language: str = "en",
        model: str = "small",
        chunk_seconds: float = 5.0,
        window_seconds: float = 30.0,
    ) -> None:
        self._source = source
        self._output_path = Path(output_path)
        self._target_language = target_language
        self._model = model
        self._chunk_seconds = chunk_seconds
        self._window_seconds = window_seconds

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_time: float = 0.0

        # Ring buffer for audio
        self._ring_buffer: list[np.ndarray] = []
        self._ring_max_samples = int(window_seconds * source.get_sample_rate())

        # Transcription results
        self._confirmed_segments: list[dict] = []
        self._tentative_text: str = ""
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start the live translation thread."""
        self._start_time = time.monotonic()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info(
            "Live translator started (target=%s, model=%s)", self._target_language, self._model
        )
        self._write_markdown()  # Initial file

    def stop(self) -> None:
        """Stop the live translation thread and write final output."""
        self._stop_event.set()
        if hasattr(self._source, "deactivate"):
            self._source.deactivate()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None
        # Final transcription of remaining audio
        self._transcribe_window()
        self._write_markdown(final=True)
        logger.info("Live translator stopped, %d segments", len(self._confirmed_segments))

    def get_status(self) -> dict:
        """Return current translation status for the polling tool."""
        elapsed = time.monotonic() - self._start_time
        with self._lock:
            last_segments = self._confirmed_segments[-5:] if self._confirmed_segments else []
            return {
                "status": "stopped" if self._stop_event.is_set() else "translating",
                "elapsed_seconds": round(elapsed, 1),
                "target_language": self._target_language,
                "model": self._model,
                "confirmed_segments": len(self._confirmed_segments),
                "latest_text": [s["text"] for s in last_segments],
                "tentative": self._tentative_text,
                "live_file": str(self._output_path),
            }

    def _run(self) -> None:
        """Main loop: read audio → accumulate → transcribe → write."""
        # Wait a bit for audio to start flowing
        time.sleep(2.0)

        while not self._stop_event.is_set():
            # Read new audio
            new_audio = self._source.get_new_audio()
            if new_audio is not None and len(new_audio) > 0:
                self._ring_buffer.append(new_audio)
                self._trim_ring_buffer()

            # Transcribe the current window
            self._transcribe_window()
            self._write_markdown()

            # Wait for next cycle
            self._stop_event.wait(timeout=self._chunk_seconds)

    def _trim_ring_buffer(self) -> None:
        """Keep only the last window_seconds of audio."""
        total = sum(len(chunk) for chunk in self._ring_buffer)
        while total > self._ring_max_samples and self._ring_buffer:
            removed = self._ring_buffer.pop(0)
            total -= len(removed)

    def _transcribe_window(self) -> None:
        """Transcribe the current ring buffer contents."""
        if not self._ring_buffer:
            return

        # Concatenate ring buffer
        audio = np.concatenate(self._ring_buffer)
        if len(audio) < self._source.get_sample_rate():  # Less than 1 second
            return

        # Check for silence
        rms = float(np.sqrt(np.mean(audio**2)))
        if rms < 0.001:
            return

        try:
            segments = self._do_transcribe(audio)
            with self._lock:
                if segments:
                    self._confirmed_segments = segments
                    self._tentative_text = ""
        except Exception as e:
            logger.error("Live transcription error: %s", e)

    def _do_transcribe(self, audio: np.ndarray) -> list[dict]:
        """Run Whisper transcription+translation on an audio chunk."""
        import sys

        from .config import get_config, get_faster_model_id, get_mlx_model_id
        from .transcriber import _resample_to_16k

        # Resample to 16kHz
        sr = self._source.get_sample_rate()
        audio = _resample_to_16k(audio, sr)

        config = get_config()
        task = "translate" if self._target_language == "en" else "transcribe"

        if sys.platform == "darwin":
            try:
                import mlx_whisper

                model_id = get_mlx_model_id(self._model)
                result = mlx_whisper.transcribe(
                    audio.astype(np.float32, copy=False),
                    path_or_hf_repo=model_id,
                    language=config.transcription.language,
                    task=task,
                    word_timestamps=False,
                    condition_on_previous_text=False,
                    hallucination_silence_threshold=1.0,
                )
                return [
                    {"start": s["start"], "end": s["end"], "text": s["text"].strip()}
                    for s in result.get("segments", [])
                    if s["text"].strip()
                ]
            except ImportError:
                pass

        # Fallback: faster-whisper
        try:
            from faster_whisper import WhisperModel

            model_id = get_faster_model_id(self._model)
            model = WhisperModel(model_id, device="auto", compute_type="auto")
            segments_gen, info = model.transcribe(
                audio.astype(np.float32, copy=False),
                language=config.transcription.language,
                task=task,
                word_timestamps=False,
                vad_filter=True,
            )
            return [
                {"start": s.start, "end": s.end, "text": s.text.strip()}
                for s in segments_gen
                if s.text.strip()
            ]
        except ImportError:
            logger.error("No transcription backend available for live translation")
            return []

    def _write_markdown(self, final: bool = False) -> None:
        """Write the live translation to a markdown file (atomic write)."""
        elapsed = time.monotonic() - self._start_time
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)

        status = "Completed" if final else f"Recording ({minutes}m {seconds:02d}s)"

        lines = [
            "# Live Translation",
            f"**Status**: {status} | Target: {self._target_language}",
            "",
            "---",
            "",
        ]

        with self._lock:
            for seg in self._confirmed_segments:
                t = seg["start"]
                m = int(t // 60)
                s = int(t % 60)
                lines.append(f"[{m}:{s:02d}] {seg['text']}")

            if self._tentative_text:
                lines.append(f"_{self._tentative_text}_")

        if not final and not self._stop_event.is_set():
            lines.append("")
            lines.append("_(translating...)_")

        content = "\n".join(lines) + "\n"

        # Atomic write: write to temp, rename
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._output_path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.rename(self._output_path)
