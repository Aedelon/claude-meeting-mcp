"""macOS audio capture via audiocap Swift CLI (Core Audio Taps)."""

import shutil
import signal
import subprocess
from pathlib import Path

# Search paths for the audiocap binary
_BINARY_SEARCH_PATHS = [
    Path(__file__).parent.parent.parent / "audiocap" / ".build" / "release" / "audiocap",
]


class MacOSCapturer:
    """Capture system audio + microphone via Core Audio Taps (macOS 14.4+)."""

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._binary_path: Path | None = self._find_binary()

    def _find_binary(self) -> Path | None:
        """Find audiocap binary in known locations or PATH."""
        # Check PATH first
        path_binary = shutil.which("audiocap")
        if path_binary:
            return Path(path_binary)

        # Check known build locations
        for candidate in _BINARY_SEARCH_PATHS:
            if candidate.exists():
                return candidate

        return None

    def is_available(self) -> bool:
        return self._binary_path is not None

    def start(self, output_path: str) -> None:
        if self._process is not None:
            raise RuntimeError("Recording already in progress")

        if self._binary_path is None:
            raise RuntimeError(
                "audiocap binary not found. Run: cd src/audiocap && swift build -c release"
            )

        self._process = subprocess.Popen(
            [str(self._binary_path), "--output", output_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def stop(self) -> None:
        if self._process is None:
            raise RuntimeError("No recording in progress")

        self._process.send_signal(signal.SIGINT)
        self._process.wait(timeout=10)
        self._process = None
