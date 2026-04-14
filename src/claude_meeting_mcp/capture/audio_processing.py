"""Audio processing chain: normalize → compress → limit.

Shared across all platform capture backends (Windows, Linux).
macOS uses the same chain in Swift (audiocap).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.signal import lfilter


@dataclass
class AudioProcessingState:
    """Persistent state for incremental audio processing.

    Maintains compressor envelope and normalization RMS between calls
    so that chunked processing (e.g., every 500ms) behaves identically
    to processing the entire signal at once.
    """

    left_attack_zi: list[float] = field(default_factory=lambda: [0.0])
    left_release_zi: list[float] = field(default_factory=lambda: [0.0])
    right_attack_zi: list[float] = field(default_factory=lambda: [0.0])
    right_release_zi: list[float] = field(default_factory=lambda: [0.0])


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
    state: AudioProcessingState | None = None,
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
        state: Persistent state for incremental mode (pass between calls)

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

    if state is not None:
        left, state.left_attack_zi, state.left_release_zi = _compress(
            left,
            comp_threshold,
            comp_ratio,
            attack_coeff,
            release_coeff,
            state.left_attack_zi,
            state.left_release_zi,
        )
        right, state.right_attack_zi, state.right_release_zi = _compress(
            right,
            comp_threshold,
            comp_ratio,
            attack_coeff,
            release_coeff,
            state.right_attack_zi,
            state.right_release_zi,
        )
    else:
        left, _, _ = _compress(left, comp_threshold, comp_ratio, attack_coeff, release_coeff)
        right, _, _ = _compress(right, comp_threshold, comp_ratio, attack_coeff, release_coeff)

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
    attack_zi: list[float] | None = None,
    release_zi: list[float] | None = None,
) -> tuple[np.ndarray, list[float], list[float]]:
    """Vectorized envelope-following compressor with attack/release.

    Uses scipy.signal.lfilter with initial conditions (zi) for stateful
    processing across chunks.

    Returns:
        (compressed_audio, attack_zi_out, release_zi_out)
    """
    abs_audio = np.abs(audio)

    a_zi = np.array(attack_zi or [0.0])
    r_zi = np.array(release_zi or [0.0])

    envelope_attack, a_zi_out = lfilter(
        [1.0 - attack_coeff], [1.0, -attack_coeff], abs_audio, zi=a_zi
    )
    envelope_release, r_zi_out = lfilter(
        [1.0 - release_coeff], [1.0, -release_coeff], abs_audio, zi=r_zi
    )
    envelope = np.maximum(envelope_attack, envelope_release).astype(np.float32)

    # Compute gain reduction: only where envelope exceeds threshold
    gain = np.ones_like(audio)
    above = envelope > threshold
    if np.any(above):
        over_db = 20.0 * np.log10(envelope[above] / threshold)
        reduced_db = over_db / ratio
        target_level = threshold * np.power(10.0, reduced_db / 20.0)
        gain[above] = target_level / envelope[above]

    return audio * gain, a_zi_out.tolist(), r_zi_out.tolist()
