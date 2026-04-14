"""Windows audio capture via WASAPI loopback (PyAudioWPatch) + microphone (sounddevice)."""

import logging
import threading
import time
from collections import deque

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

# Flush interval for incremental WAV writing (seconds)
_FLUSH_INTERVAL = 0.5


class WindowsCapturer:
    """Capture system audio (WASAPI loopback) + microphone on Windows."""

    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._output_path: str | None = None
        self._loopback_buffer: deque[np.ndarray] = deque()
        self._mic_buffer: deque[np.ndarray] = deque()
        self._samplerate = 44100
        self._error: Exception | None = None

    def is_available(self) -> bool:
        try:
            import pyaudiowpatch  # noqa: F401
            import sounddevice  # noqa: F401

            return True
        except ImportError:
            return False

    def start(self, output_path: str) -> None:
        if self._threads:
            raise RuntimeError("Recording already in progress")

        self._output_path = output_path
        self._stop_event.clear()
        self._loopback_buffer.clear()
        self._mic_buffer.clear()
        self._error = None

        t_loopback = threading.Thread(target=self._capture_loopback, daemon=True)
        t_mic = threading.Thread(target=self._capture_mic, daemon=True)
        t_writer = threading.Thread(target=self._write_wav_incremental, daemon=True)

        self._threads = [t_loopback, t_mic, t_writer]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        if not self._threads:
            raise RuntimeError("No recording in progress")

        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=10)
        self._threads.clear()

        if self._error:
            raise self._error

    def _capture_loopback(self) -> None:
        """Capture system audio via WASAPI loopback."""
        try:
            import pyaudiowpatch as pyaudio

            pa = pyaudio.PyAudio()

            wasapi_info = None
            for i in range(pa.get_host_api_count()):
                info = pa.get_host_api_info_by_index(i)
                if info.get("name", "").lower().find("wasapi") >= 0:
                    wasapi_info = info
                    break

            if wasapi_info is None:
                self._error = RuntimeError("WASAPI host API not found")
                return

            default_speakers = pa.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
            loopback_device = None

            for loopback in pa.get_loopback_device_info_generator():
                if default_speakers["name"] in loopback["name"]:
                    loopback_device = loopback
                    break

            if loopback_device is None:
                self._error = RuntimeError("No WASAPI loopback device found")
                return

            self._samplerate = int(loopback_device["defaultSampleRate"])

            stream = pa.open(
                format=pyaudio.paFloat32,
                channels=1,
                rate=self._samplerate,
                input=True,
                input_device_index=loopback_device["index"],
                frames_per_buffer=1024,
            )

            while not self._stop_event.is_set():
                data = stream.read(1024, exception_on_overflow=False)
                audio = np.frombuffer(data, dtype=np.float32)
                if loopback_device.get("maxInputChannels", 1) > 1:
                    channels = int(loopback_device["maxInputChannels"])
                    audio = audio.reshape(-1, channels)[:, 0]
                self._loopback_buffer.append(audio)

            stream.stop_stream()
            stream.close()
            pa.terminate()

        except Exception as e:
            self._error = e

    def _capture_mic(self) -> None:
        """Capture microphone via sounddevice."""
        try:
            import sounddevice as sd

            def callback(indata: np.ndarray, frames: int, time_info: dict, status: int) -> None:
                self._mic_buffer.append(indata[:, 0].copy())

            with sd.InputStream(
                samplerate=self._samplerate,
                channels=1,
                dtype="float32",
                callback=callback,
                blocksize=1024,
            ):
                self._stop_event.wait()

        except Exception as e:
            self._error = e

    def _write_wav_incremental(self) -> None:
        """Incrementally write stereo WAV — flushes every 500ms instead of at the end.

        If the process crashes, we lose at most 500ms of audio instead of everything.
        """
        try:
            if self._output_path is None:
                return

            from .audio_processing import AudioProcessingState, process_stereo

            wav_file: sf.SoundFile | None = None
            audio_state = AudioProcessingState()

            while not self._stop_event.is_set() or self._loopback_buffer:
                if not self._loopback_buffer or not self._mic_buffer:
                    time.sleep(_FLUSH_INTERVAL)
                    continue

                # Drain available buffers
                left_chunks = []
                while self._loopback_buffer:
                    left_chunks.append(self._loopback_buffer.popleft())
                right_chunks = []
                while self._mic_buffer:
                    right_chunks.append(self._mic_buffer.popleft())

                if not left_chunks or not right_chunks:
                    continue

                left = np.concatenate(left_chunks)
                right = np.concatenate(right_chunks)

                min_len = min(len(left), len(right))
                if min_len == 0:
                    continue

                left_proc, right_proc = process_stereo(
                    left[:min_len],
                    right[:min_len],
                    sample_rate=self._samplerate,
                    state=audio_state,
                )
                stereo = np.column_stack([left_proc, right_proc])

                # Open file on first write
                if wav_file is None:
                    wav_file = sf.SoundFile(
                        self._output_path,
                        mode="w",
                        samplerate=self._samplerate,
                        channels=2,
                        subtype="PCM_16",
                    )

                wav_file.write(stereo)
                wav_file.flush()

            if wav_file is not None:
                wav_file.close()

        except Exception as e:
            self._error = e
