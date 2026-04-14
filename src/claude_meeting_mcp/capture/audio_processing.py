"""Audio processing chain: normalize → compress → limit.

Shared across all platform capture backends (Windows, Linux).
macOS uses the same chain in Swift (audiocap).
"""

from __future__ import annotations

import numpy as np
from scipy.signal import lfilter


def process_stereo(
    left: np.ndarray,
    right: np.ndarray,
    target_rms: float = 0.1,
    max_gain: float = 20.0,
    comp_threshold: float = 0.15,
    comp_ratio: float = 4.0,
    attack_ms: float = 5.0,
    release_ms: float = 100.0,
    limiter_ceiling: float = 0.95,
    sample_rate: int = 44100,
) -> tuple[np.ndarray, np.ndarray]:
    """Apply normalize → compress → limit to stereo channels.

    Args:
        left: System audio channel (float32)
        right: Microphone channel (float32)
        target_rms: Normalization target level (~-20dB)
        max_gain: Maximum normalization gain (prevents noise amplification)
        comp_threshold: Compressor threshold (~-16dB)
        comp_ratio: Compression ratio (4:1)
        attack_ms: Compressor attack time in milliseconds
        release_ms: Compressor release time in milliseconds
        limiter_ceiling: Hard limiter ceiling (~-0.5dB)
        sample_rate: Audio sample rate for envelope coefficients

    Returns:
        Tuple of (processed_left, processed_right) as float32 arrays
    """
    left = left.astype(np.float32, copy=True)
    right = right.astype(np.float32, copy=True)

    # Step 1: Normalize — bring both channels to target RMS
    left = _normalize(left, target_rms, max_gain)
    right = _normalize(right, target_rms, max_gain)

    # Step 2: Compress — reduce dynamic range
    attack_coeff = float(np.exp(-1.0 / (sample_rate * attack_ms / 1000.0)))
    release_coeff = float(np.exp(-1.0 / (sample_rate * release_ms / 1000.0)))
    left = _compress(left, comp_threshold, comp_ratio, attack_coeff, release_coeff)
    right = _compress(right, comp_threshold, comp_ratio, attack_coeff, release_coeff)

    # Step 3: Limit — hard ceiling
    np.clip(left, -limiter_ceiling, limiter_ceiling, out=left)
    np.clip(right, -limiter_ceiling, limiter_ceiling, out=right)

    return left, right


def _normalize(audio: np.ndarray, target_rms: float, max_gain: float) -> np.ndarray:
    """RMS normalization with gain cap."""
    rms = float(np.sqrt(np.mean(audio**2)))
    if rms > 0.001:
        gain = min(target_rms / rms, max_gain)
        audio *= gain
    return audio


def _compress(
    audio: np.ndarray,
    threshold: float,
    ratio: float,
    attack_coeff: float,
    release_coeff: float,
) -> np.ndarray:
    """Vectorized envelope-following compressor with attack/release.

    Uses scipy.signal.lfilter for the envelope follower (vectorized IIR filter)
    instead of a Python for-loop over samples.
    """
    abs_audio = np.abs(audio)

    # Envelope follower via 1-pole IIR filter on absolute signal
    # attack path: fast response to transients
    # release path: slow decay
    # Approximate: use release coeff for the main envelope, then take max with attack
    # This is the standard "peak detector" approach used in audio compressors
    envelope_release = lfilter([1.0 - release_coeff], [1.0, -release_coeff], abs_audio)
    envelope_attack = lfilter([1.0 - attack_coeff], [1.0, -attack_coeff], abs_audio)
    envelope = np.maximum(envelope_attack, envelope_release).astype(np.float32)

    # Compute gain reduction: only where envelope exceeds threshold
    gain = np.ones_like(audio)
    above = envelope > threshold
    if np.any(above):
        over_db = 20.0 * np.log10(envelope[above] / threshold)
        reduced_db = over_db / ratio
        target_level = threshold * np.power(10.0, reduced_db / 20.0)
        gain[above] = target_level / envelope[above]

    return audio * gain
