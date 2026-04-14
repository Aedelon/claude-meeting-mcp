"""Audio recording via audiocap Swift CLI."""

import subprocess
import signal
import os
from pathlib import Path
from .storage import RECORDINGS_DIR, ensure_dirs, generate_filename

# Path to compiled audiocap binary
AUDIOCAP_BIN = Path(__file__).parent.parent / "audiocap" / ".build" / "release" / "audiocap"

_current_process: subprocess.Popen | None = None
_current_file: str | None = None


def is_audiocap_available() -> bool:
    return AUDIOCAP_BIN.exists()


def start_recording() -> dict:
    """Start recording system audio + microphone."""
    global _current_process, _current_file

    if _current_process is not None:
        return {"error": "Recording already in progress", "file": _current_file}

    if not is_audiocap_available():
        return {"error": f"audiocap binary not found at {AUDIOCAP_BIN}. Run: cd src/audiocap && swift build -c release"}

    ensure_dirs()
    filename = generate_filename()
    filepath = RECORDINGS_DIR / filename
    _current_file = str(filepath)

    _current_process = subprocess.Popen(
        [str(AUDIOCAP_BIN), "--output", str(filepath)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    return {"status": "recording", "file": _current_file, "pid": _current_process.pid}


def stop_recording() -> dict:
    """Stop current recording."""
    global _current_process, _current_file

    if _current_process is None:
        return {"error": "No recording in progress"}

    _current_process.send_signal(signal.SIGINT)
    _current_process.wait(timeout=10)

    result = {"status": "stopped", "file": _current_file}
    _current_process = None
    _current_file = None
    return result


def is_recording() -> bool:
    return _current_process is not None
