"""Tests for audio processing chain (normalize, compress, limit)."""

from __future__ import annotations

import numpy as np

from claude_meeting_mcp.capture.audio_processing import process_stereo


def test_process_stereo_equalizes_levels():
    """Loud system + quiet mic should be brought to similar levels."""
    sr = 44100
    t = np.linspace(0, 1, sr, dtype=np.float32)
    loud = np.sin(2 * np.pi * 440 * t) * 0.5  # system: loud
    quiet = np.sin(2 * np.pi * 440 * t) * 0.01  # mic: very quiet

    left, right = process_stereo(loud, quiet, sample_rate=sr)

    left_rms = np.sqrt(np.mean(left**2))
    right_rms = np.sqrt(np.mean(right**2))

    # Both should be in similar range (within 10x, was 50x before)
    ratio = max(left_rms, right_rms) / max(min(left_rms, right_rms), 1e-10)
    assert ratio < 10.0, f"Level ratio {ratio:.1f} still too large"


def test_process_stereo_limiter():
    """Output should never exceed limiter ceiling."""
    sr = 44100
    loud = np.ones(sr, dtype=np.float32) * 0.9
    quiet = np.zeros(sr, dtype=np.float32)

    left, right = process_stereo(loud, quiet, limiter_ceiling=0.95, sample_rate=sr)

    assert np.max(np.abs(left)) <= 0.95
    assert np.max(np.abs(right)) <= 0.95


def test_process_stereo_silence():
    """Silence in should produce silence out (no noise amplification beyond max_gain)."""
    sr = 44100
    silence = np.zeros(sr, dtype=np.float32)

    left, right = process_stereo(silence, silence, sample_rate=sr)

    assert np.max(np.abs(left)) == 0.0
    assert np.max(np.abs(right)) == 0.0


def test_process_stereo_no_mutation():
    """Input arrays should not be modified."""
    sr = 44100
    original_left = np.sin(np.linspace(0, 10, sr)).astype(np.float32) * 0.3
    original_right = np.sin(np.linspace(0, 10, sr)).astype(np.float32) * 0.1
    left_copy = original_left.copy()
    right_copy = original_right.copy()

    process_stereo(original_left, original_right, sample_rate=sr)

    np.testing.assert_array_equal(original_left, left_copy)
    np.testing.assert_array_equal(original_right, right_copy)
