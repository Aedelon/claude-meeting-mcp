"""Windows audio capture via WASAPI loopback (PyAudioWPatch) + microphone (sounddevice)."""

import threading
from collections import deque

import numpy as np
import soundfile as sf


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
        t_writer = threading.Thread(target=self._write_wav, daemon=True)

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

            # Find WASAPI loopback device
            wasapi_info = None
            for i in range(pa.get_host_api_count()):
                info = pa.get_host_api_info_by_index(i)
                if info.get("name", "").lower().find("wasapi") >= 0:
                    wasapi_info = info
                    break

            if wasapi_info is None:
                self._error = RuntimeError("WASAPI host API not found")
                return

            # Find default loopback device
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
                # Take first channel if stereo
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

    def _write_wav(self) -> None:
        """Interleave loopback + mic buffers and write stereo WAV."""
        try:
            # Wait for recording to stop
            self._stop_event.wait()

            if self._output_path is None:
                return

            left = (
                np.concatenate(list(self._loopback_buffer))
                if self._loopback_buffer
                else np.array([])
            )
            right = np.concatenate(list(self._mic_buffer)) if self._mic_buffer else np.array([])

            # Align lengths
            min_len = min(len(left), len(right))
            if min_len == 0:
                self._error = RuntimeError("No audio data captured")
                return

            # Audio processing: normalize → compress → limit
            from .audio_processing import process_stereo

            left_proc, right_proc = process_stereo(
                left[:min_len], right[:min_len], sample_rate=self._samplerate
            )

            stereo = np.column_stack([left_proc, right_proc])
            sf.write(self._output_path, stereo, self._samplerate)

        except Exception as e:
            self._error = e
