"""Audio recording orchestration via platform-specific capture backends."""

from __future__ import annotations

import logging
import threading

from .capture import get_capturer
from .storage import RECORDINGS_DIR, TRANSCRIPTIONS_DIR, ensure_dirs, generate_filename

logger = logging.getLogger(__name__)

MAX_RECORDING_SECONDS = 4 * 3600  # 4 hours default safety limit

_lock = threading.RLock()  # Reentrant: _auto_stop calls stop_recording from Timer thread
_capturer = None
_current_file: str | None = None
_timeout_timer: threading.Timer | None = None
_live_translator = None


def _auto_stop() -> None:
    """Called by the timeout timer to auto-stop a runaway recording."""
    logger.warning("Recording timeout (%ds) reached, auto-stopping", MAX_RECORDING_SECONDS)
    stop_recording()


def start_recording(live_translate: str | None = None) -> dict:
    """Start recording system audio + microphone.

    Args:
        live_translate: Target language for live translation (e.g., "en").
            None = no live translation.
    """
    global _capturer, _current_file, _timeout_timer, _live_translator

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

        result = {"status": "recording", "file": _current_file}

        # Start live translation if requested
        if live_translate:
            try:
                from .config import get_config
                from .live_translator import FileAudioSource, LiveTranslator

                config = get_config()
                meeting_id = filepath.stem
                live_file = str(TRANSCRIPTIONS_DIR / f"{meeting_id}_live.md")

                source = FileAudioSource(
                    _current_file,
                    sample_rate=config.recording.sample_rate,
                )
                _live_translator = LiveTranslator(
                    source=source,
                    output_path=live_file,
                    target_language=live_translate,
                    model=config.live_translation.model,
                    chunk_seconds=config.live_translation.chunk_seconds,
                    window_seconds=config.live_translation.window_seconds,
                )
                _live_translator.start()
                result["live_translation"] = True
                result["live_file"] = live_file
                logger.info("Live translation started: %s → %s", _current_file, live_file)
            except Exception as e:
                logger.error("Failed to start live translation: %s", e)
                result["live_translation_error"] = str(e)

        logger.info("Recording started: %s", _current_file)
        return result


def stop_recording() -> dict:
    """Stop current recording."""
    global _capturer, _current_file, _timeout_timer, _live_translator

    with _lock:
        if _capturer is None:
            return {"error": "No recording in progress"}

        # Cancel timeout timer
        if _timeout_timer is not None:
            _timeout_timer.cancel()
            _timeout_timer = None

        # Stop live translator
        if _live_translator is not None:
            try:
                _live_translator.stop()
            except Exception as e:
                logger.error("Error stopping live translator: %s", e)
            _live_translator = None

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


def get_live_status() -> dict | None:
    """Get live translation status, or None if not active."""
    if _live_translator is not None:
        return _live_translator.get_status()
    return None


def is_recording() -> bool:
    return _capturer is not None
