"""Audio recording orchestration via platform-specific capture backends."""

from .capture import AudioCapturer, get_capturer
from .storage import RECORDINGS_DIR, ensure_dirs, generate_filename

_capturer: AudioCapturer | None = None
_current_file: str | None = None


def is_audiocap_available() -> bool:
    """Check if audio capture is available on this platform."""
    return get_capturer().is_available()


def start_recording() -> dict:
    """Start recording system audio + microphone."""
    global _capturer, _current_file

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
        _current_file = None
        return {"error": str(e)}

    return {"status": "recording", "file": _current_file}


def stop_recording() -> dict:
    """Stop current recording."""
    global _capturer, _current_file

    if _capturer is None:
        return {"error": "No recording in progress"}

    try:
        _capturer.stop()
    except RuntimeError as e:
        return {"error": str(e)}

    result = {"status": "stopped", "file": _current_file}
    _capturer = None
    _current_file = None
    return result


def is_recording() -> bool:
    return _capturer is not None
