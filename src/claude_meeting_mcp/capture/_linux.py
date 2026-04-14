"""Linux audio capture via PipeWire/PulseAudio monitor + microphone (sounddevice)."""

import subprocess
import threading
from collections import deque

import numpy as np
import soundfile as sf


def _detect_audio_server() -> str:
    """Detect whether PipeWire or PulseAudio is running."""
    try:
        result = subprocess.run(
            ["pactl", "info"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = result.stdout.lower()
        if "pipewire" in output:
            return "pipewire"
        if "pulseaudio" in output:
            return "pulseaudio"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def _find_monitor_source() -> str | None:
    """Find the monitor source for system audio capture."""
    try:
        result = subprocess.run(
            ["pactl", "list", "sources", "short"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) >= 2 and ".monitor" in parts[1]:
                return parts[1]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


class LinuxCapturer:
    """Capture system audio (PipeWire/PulseAudio monitor) + microphone on Linux."""

    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._output_path: str | None = None
        self._system_buffer: deque[np.ndarray] = deque()
        self._mic_buffer: deque[np.ndarray] = deque()
        self._samplerate = 44100
        self._error: Exception | None = None
        self._monitor_source: str | None = None

    def is_available(self) -> bool:
        try:
            import sounddevice  # noqa: F401
        except ImportError:
            return False

        server = _detect_audio_server()
        if server == "unknown":
            return False

        self._monitor_source = _find_monitor_source()
        return self._monitor_source is not None

    def start(self, output_path: str) -> None:
        if self._threads:
            raise RuntimeError("Recording already in progress")

        if self._monitor_source is None:
            self._monitor_source = _find_monitor_source()
            if self._monitor_source is None:
                raise RuntimeError(
                    "No monitor source found. Try: pactl load-module module-loopback"
                )

        self._output_path = output_path
        self._stop_event.clear()
        self._system_buffer.clear()
        self._mic_buffer.clear()
        self._error = None

        t_system = threading.Thread(target=self._capture_system, daemon=True)
        t_mic = threading.Thread(target=self._capture_mic, daemon=True)
        t_writer = threading.Thread(target=self._write_wav, daemon=True)

        self._threads = [t_system, t_mic, t_writer]
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

    def _capture_system(self) -> None:
        """Capture system audio via PipeWire/PulseAudio monitor source."""
        try:
            import sounddevice as sd

            # Find the monitor device index
            devices = sd.query_devices()
            monitor_idx = None
            for i, dev in enumerate(devices):
                if self._monitor_source and self._monitor_source in dev.get("name", ""):
                    monitor_idx = i
                    break

            if monitor_idx is None:
                # Try matching by "monitor" keyword
                for i, dev in enumerate(devices):
                    if "monitor" in dev.get("name", "").lower() and dev["max_input_channels"] > 0:
                        monitor_idx = i
                        break

            if monitor_idx is None:
                self._error = RuntimeError(
                    f"Monitor source '{self._monitor_source}' not found in sounddevice devices"
                )
                return

            def callback(indata: np.ndarray, frames: int, time_info: dict, status: int) -> None:
                self._system_buffer.append(indata[:, 0].copy())

            with sd.InputStream(
                device=monitor_idx,
                samplerate=self._samplerate,
                channels=1,
                dtype="float32",
                callback=callback,
                blocksize=1024,
            ):
                self._stop_event.wait()

        except Exception as e:
            self._error = e

    def _capture_mic(self) -> None:
        """Capture microphone via sounddevice (default input)."""
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
        """Interleave system + mic buffers and write stereo WAV."""
        try:
            self._stop_event.wait()

            if self._output_path is None:
                return

            left = (
                np.concatenate(list(self._system_buffer)) if self._system_buffer else np.array([])
            )
            right = np.concatenate(list(self._mic_buffer)) if self._mic_buffer else np.array([])

            min_len = min(len(left), len(right))
            if min_len == 0:
                self._error = RuntimeError("No audio data captured")
                return

            stereo = np.column_stack([left[:min_len], right[:min_len]])
            sf.write(self._output_path, stereo, self._samplerate)

        except Exception as e:
            self._error = e
