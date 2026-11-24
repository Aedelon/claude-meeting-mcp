"""Tests for monitor source selection on Linux."""

import subprocess
from unittest.mock import patch

from claude_meeting_mcp.capture._linux import _find_monitor_source

SOURCES = (
    "56\talsa_output.pci-0000_00_1f.3.analog-stereo.monitor\tPipeWire\ts32le 2ch 48000Hz\tIDLE\n"
    "57\talsa_input.pci-0000_00_1f.3.analog-stereo\tPipeWire\ts32le 2ch 48000Hz\tIDLE\n"
    "100\talsa_output.usb-headset.analog-stereo.monitor\tPipeWire\ts16le 2ch 48000Hz\tIDLE\n"
)


def _pactl(default_sink=None, info=""):
    """Fake subprocess.run for the pactl calls."""

    def run(cmd, **kwargs):
        if cmd[1:] == ["list", "sources", "short"]:
            return subprocess.CompletedProcess(cmd, 0, SOURCES, "")
        if cmd[1:] == ["get-default-sink"]:
            if default_sink is None:
                return subprocess.CompletedProcess(cmd, 1, "", "No valid command specified.")
            return subprocess.CompletedProcess(cmd, 0, default_sink + "\n", "")
        if cmd[1:] == ["info"]:
            return subprocess.CompletedProcess(cmd, 0, info, "")
        raise AssertionError(f"unexpected command {cmd}")

    return run


def test_prefers_default_sink_monitor():
    with patch("subprocess.run", _pactl("alsa_output.usb-headset.analog-stereo")):
        assert _find_monitor_source() == "alsa_output.usb-headset.analog-stereo.monitor"


def test_default_sink_from_pactl_info():
    info = "Server Name: PulseAudio\nDefault Sink: alsa_output.usb-headset.analog-stereo\n"
    with patch("subprocess.run", _pactl(info=info)):
        assert _find_monitor_source() == "alsa_output.usb-headset.analog-stereo.monitor"


def test_falls_back_to_first_monitor():
    with patch("subprocess.run", _pactl()):
        assert _find_monitor_source() == "alsa_output.pci-0000_00_1f.3.analog-stereo.monitor"


def test_no_pactl():
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert _find_monitor_source() is None
