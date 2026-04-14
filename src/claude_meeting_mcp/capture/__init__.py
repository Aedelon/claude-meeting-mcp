"""Cross-platform audio capture backends."""

import sys
from typing import Protocol


class AudioCapturer(Protocol):
    """Interface for platform-specific audio capture."""

    def start(self, output_path: str) -> None:
        """Start recording system audio + microphone to a stereo WAV file."""
        ...

    def stop(self) -> None:
        """Stop the current recording."""
        ...

    def is_available(self) -> bool:
        """Check if this capture backend is available on the current system."""
        ...


def get_capturer() -> AudioCapturer:
    """Factory: return the appropriate audio capturer for the current platform."""
    if sys.platform == "darwin":
        from ._macos import MacOSCapturer

        return MacOSCapturer()
    elif sys.platform == "win32":
        from ._windows import WindowsCapturer

        return WindowsCapturer()
    else:
        from ._linux import LinuxCapturer

        return LinuxCapturer()
