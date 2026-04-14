"""macOS audio capture via audiocap Swift CLI (Core Audio Taps)."""

import logging
import shutil
import signal
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

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
        path_binary = shutil.which("audiocap")
        if path_binary:
            return Path(path_binary)

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
        logger.info("audiocap started (pid=%d)", self._process.pid)

    def stop(self) -> None:
        if self._process is None:
            raise RuntimeError("No recording in progress")

        # Check if process is still alive before sending signal
        if self._process.poll() is not None:
            rc = self._process.returncode
            stderr = self._process.stderr.read().decode() if self._process.stderr else ""
            self._process = None
            logger.error("audiocap already dead (rc=%d): %s", rc, stderr.strip())
            raise RuntimeError(f"audiocap process died (exit code {rc}): {stderr.strip()}")

        self._process.send_signal(signal.SIGINT)
        self._process.wait(timeout=10)

        # Log stderr warnings
        rc = self._process.returncode
        if rc != 0:
            stderr = self._process.stderr.read().decode() if self._process.stderr else ""
            logger.warning("audiocap exited with code %d: %s", rc, stderr.strip())

        self._process = None
