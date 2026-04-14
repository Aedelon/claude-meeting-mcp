"""Audio recording orchestration via platform-specific capture backends."""

from __future__ import annotations

import logging
import threading

from .capture import get_capturer
from .storage import RECORDINGS_DIR, ensure_dirs, generate_filename

logger = logging.getLogger(__name__)

MAX_RECORDING_SECONDS = 4 * 3600  # 4 hours default safety limit

_lock = threading.Lock()
_capturer = None
_current_file: str | None = None
_timeout_timer: threading.Timer | None = None


def _auto_stop() -> None:
    """Called by the timeout timer to auto-stop a runaway recording."""
    logger.warning("Recording timeout (%ds) reached, auto-stopping", MAX_RECORDING_SECONDS)
    stop_recording()


def start_recording() -> dict:
    """Start recording system audio + microphone."""
    global _capturer, _current_file, _timeout_timer

    with _lock:
        if _capturer is not None:
            return {"error": "Recording already in progress", "file": _current_file}

        capturer = get_capturer()
        if not capturer.is_available():
            return {"error": "Audio capture not available on this platform. Check installation."}

        ensure_dirs()
        filename = generate_filename()
        filepath = RECORDINGS_DIR / filename
        _current_file = str(filepath)

        try:
            capturer.start(_current_file)
            _capturer = capturer
        except RuntimeError as e:
            logger.error("Failed to start recording: %s", e)
            _current_file = None
            return {"error": str(e)}

        # Start safety timeout timer
        _timeout_timer = threading.Timer(MAX_RECORDING_SECONDS, _auto_stop)
        _timeout_timer.daemon = True
        _timeout_timer.start()

        logger.info("Recording started: %s", _current_file)
        return {"status": "recording", "file": _current_file}


def stop_recording() -> dict:
    """Stop current recording."""
    global _capturer, _current_file, _timeout_timer

    with _lock:
        if _capturer is None:
            return {"error": "No recording in progress"}

        # Cancel timeout timer
        if _timeout_timer is not None:
            _timeout_timer.cancel()
            _timeout_timer = None

        file_path = _current_file

        try:
            _capturer.stop()
        except RuntimeError as e:
            logger.error("Error stopping recording: %s", e)
            _capturer = None
            _current_file = None
            return {"error": str(e), "file": file_path}

        logger.info("Recording stopped: %s", file_path)
        _capturer = None
        _current_file = None
        return {"status": "stopped", "file": file_path}


def is_recording() -> bool:
    return _capturer is not None
