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

# If loopback delivers nothing for this long (seconds), fill the gap with silence
# so it stays aligned with the microphone channel.
_SILENCE_GAP_THRESHOLD = 0.3


class WindowsCapturer:
    """Capture system audio (WASAPI loopback) + microphone on Windows."""

    def __init__(self) -> None:
        self._stop_event = threading.Event()
        # Set once the loopback thread knows the real sample rate (or has failed),
        # so the mic thread never opens its stream with a guessed rate.
        self._rate_ready = threading.Event()
        # Set when a capture thread has finished (normally or because of an error).
        self._loopback_done = threading.Event()
        self._mic_done = threading.Event()

        self._threads: list[threading.Thread] = []
        self._output_path: str | None = None
        self._loopback_buffer: deque[np.ndarray] = deque()
        self._mic_buffer: deque[np.ndarray] = deque()
        self._samplerate = 44100

        self._error: Exception | None = None  # fatal (writer) error
        self._loopback_error: Exception | None = None
        self._mic_error: Exception | None = None

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
        self._rate_ready.clear()
        self._loopback_done.clear()
        self._mic_done.clear()
        self._loopback_buffer.clear()
        self._mic_buffer.clear()
        self._error = None
        self._loopback_error = None
        self._mic_error = None

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

        if self._mic_error:
            # Non-fatal: the mic channel was recorded as silence.
            logger.warning("Microphone capture failed: %s", self._mic_error)

        err = self._error or self._loopback_error
        if err:
            raise err

    # ------------------------------------------------------------------ capture

    def _capture_loopback(self) -> None:
        """Capture system audio via WASAPI loopback."""
        pa = None
        stream = None
        try:
            import pyaudiowpatch as pyaudio

            pa = pyaudio.PyAudio()

            wasapi_info = None
            for i in range(pa.get_host_api_count()):
                info = pa.get_host_api_info_by_index(i)
                if "wasapi" in info.get("name", "").lower():
                    wasapi_info = info
                    break

            if wasapi_info is None:
                raise RuntimeError("WASAPI host API not found")

            default_speakers = pa.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
            loopback_device = None

            for loopback in pa.get_loopback_device_info_generator():
                if default_speakers["name"] in loopback["name"]:
                    loopback_device = loopback
                    break

            if loopback_device is None:
                raise RuntimeError("No WASAPI loopback device found")

            self._samplerate = int(loopback_device["defaultSampleRate"])
            channels = max(1, int(loopback_device["maxInputChannels"]))
            self._rate_ready.set()

            # Must open with the device's real channel count; WASAPI loopback
            # does not accept a different one.
            stream = pa.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=self._samplerate,
                input=True,
                input_device_index=loopback_device["index"],
                frames_per_buffer=1024,
            )

            # WASAPI loopback delivers NO data while nothing is playing, whereas the
            # mic keeps streaming. To keep both channels on the same timeline we
            # track how many frames we've delivered vs. how many the wall clock says
            # should exist, and inject silence to cover the gap.
            t0 = time.monotonic()
            delivered = 0
            max_gap = int(self._samplerate * _SILENCE_GAP_THRESHOLD)

            while not self._stop_event.is_set():
                # Poll instead of a blocking read(): a blocking read would hang
                # stop() during silence.
                if stream.get_read_available() >= 1024:
                    data = stream.read(1024, exception_on_overflow=False)
                    audio = np.frombuffer(data, dtype=np.float32)
                    if channels > 1:
                        audio = audio.reshape(-1, channels).mean(axis=1)
                    self._loopback_buffer.append(audio.copy())
                    delivered += len(audio)
                    continue

                deficit = int((time.monotonic() - t0) * self._samplerate) - delivered
                if deficit > max_gap:
                    self._loopback_buffer.append(np.zeros(deficit, dtype=np.float32))
                    delivered += deficit
                else:
                    time.sleep(0.005)

        except Exception as e:
            logger.error("Loopback capture failed: %s", e)
            self._loopback_error = e
        finally:
            self._rate_ready.set()  # never leave the mic thread waiting
            try:
                if stream is not None:
                    stream.stop_stream()
                    stream.close()
                if pa is not None:
                    pa.terminate()
            except Exception:
                pass
            self._loopback_done.set()

    def _capture_mic(self) -> None:
        """Capture microphone via sounddevice."""
        try:
            import sounddevice as sd

            # Wait for the loopback thread to publish the real sample rate.
            self._rate_ready.wait(timeout=5)

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
            logger.error("Microphone capture failed: %s", e)
            self._mic_error = e
        finally:
            self._mic_done.set()

    # ------------------------------------------------------------------- writer

    @staticmethod
    def _drain(buf: deque) -> np.ndarray:
        chunks = []
        while buf:
            chunks.append(buf.popleft())
        if not chunks:
            return np.empty(0, dtype=np.float32)
        return np.concatenate(chunks).astype(np.float32, copy=False)

    @staticmethod
    def _take(x: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
        """Return (first n samples zero-padded if short, leftover)."""
        if len(x) >= n:
            return x[:n], x[n:]
        pad = np.zeros(n - len(x), dtype=np.float32)
        return np.concatenate([x, pad]), np.empty(0, dtype=np.float32)

    def _write_wav_incremental(self) -> None:
        """Incrementally write stereo WAV — flushes every 500ms instead of at the end.

        If the process crashes, we lose at most 500ms of audio instead of everything.
        If one source dies (e.g. mic unavailable) its channel is written as silence
        instead of blocking the whole recording.
        """
        wav_file: sf.SoundFile | None = None
        try:
            if self._output_path is None:
                return

            from .audio_processing import AudioProcessingState, process_stereo

            audio_state = AudioProcessingState()
            left = np.empty(0, dtype=np.float32)
            right = np.empty(0, dtype=np.float32)

            while True:
                stopping = self._stop_event.is_set()
                l_dead = self._loopback_done.is_set() and not self._loopback_buffer
                m_dead = self._mic_done.is_set() and not self._mic_buffer

                left = np.concatenate([left, self._drain(self._loopback_buffer)])
                right = np.concatenate([right, self._drain(self._mic_buffer)])

                if stopping or (l_dead and m_dead):
                    stopping = True
                    n = max(len(left), len(right))  # flush everything left
                elif l_dead:
                    n = len(right)
                elif m_dead:
                    n = len(left)
                else:
                    n = min(len(left), len(right))

                if n > 0:
                    # Keep the unused tail instead of discarding it, so the two
                    # channels stay time-aligned.
                    left_out, left = self._take(left, n)
                    right_out, right = self._take(right, n)

                    left_proc, right_proc = process_stereo(
                        left_out,
                        right_out,
                        sample_rate=self._samplerate,
                        state=audio_state,
                    )
                    stereo = np.column_stack([left_proc, right_proc])

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

                if stopping:
                    break
                time.sleep(_FLUSH_INTERVAL)

        except Exception as e:
            logger.error("WAV writer failed: %s", e)
            self._error = e
        finally:
            if wav_file is not None:
                wav_file.close()
