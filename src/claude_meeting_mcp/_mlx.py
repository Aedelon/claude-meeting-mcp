"""MLX Metal buffer-cache bounding for a long-lived server process.

MLX caches freed GPU buffers for reuse and sizes that cache to the machine's
RAM by default (~30 GB on a 96 GB Mac). That is a sensible trade for a one-shot
CLI, which exits and lets the OS reclaim everything. This MCP server instead
lives as long as its Claude session - days - so the cache only ever ratchets
upwards. Observed 2026-08-10: one 12-day-old server idling on 38 GB, of which
23.7 GB was Metal buffer cache across 4,439 IOAccelerator regions. RSS reported
16 MB because 99% had been swapped out, so only `footprint`/`vmmap` showed it.

Every mlx_whisper call site must cap the cache up front and drain it afterwards.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Big enough to serve buffer reuse within a single transcription, small enough
# that an idle server never holds more than this. Raise only with a measurement.
CACHE_LIMIT_BYTES = 512 * 1024**2

_cache_limit_set = False


def cap_cache() -> None:
    """Bound the MLX Metal buffer cache. Idempotent; safe if MLX is absent."""
    global _cache_limit_set
    if _cache_limit_set:
        return
    try:
        import mlx.core as mx
    except ImportError:
        return
    previous = mx.set_cache_limit(CACHE_LIMIT_BYTES)
    _cache_limit_set = True
    logger.info(
        "MLX cache limit set to %d MB (was %d MB)",
        CACHE_LIMIT_BYTES // 1024**2,
        previous // 1024**2,
    )


def clear_cache() -> None:
    """Return cached MLX Metal buffers to the OS. Safe if MLX is absent."""
    try:
        import mlx.core as mx
    except ImportError:
        return
    mx.clear_cache()
