"""Regression tests for the MLX Metal buffer-cache bound.

Guards the 2026-08-10 leak: a 12-day-old server idling on 38 GB, 23.7 GB of it
MLX buffer cache, because MLX defaults its cache limit to machine RAM and this
server never exits. See src/claude_meeting_mcp/_mlx.py.
"""

from __future__ import annotations

import pytest

from claude_meeting_mcp import _mlx

mx = pytest.importorskip("mlx.core", reason="MLX only available on Apple Silicon")


@pytest.fixture(autouse=True)
def _restore_mlx_state():
    """Each test starts from a clean, unbounded cache and leaves no residue."""
    _mlx._cache_limit_set = False
    original = mx.set_cache_limit(2**63 - 1)
    mx.clear_cache()
    yield
    _mlx._cache_limit_set = False
    mx.set_cache_limit(original)
    mx.clear_cache()


def _churn_varied_shapes() -> None:
    """Allocate a spread of buffer sizes.

    Variable shapes are the point: uniform shapes are served from the reuse
    cache and never grow it, which is why real variable-length audio chunks
    leak where a synthetic fixed-size benchmark does not.
    """
    for n in range(200, 1400, 8):
        a = mx.random.normal((n, n))
        mx.eval(a @ a.T)


def test_cache_grows_unbounded_without_cap():
    """Without the cap, varied-shape churn exceeds the limit we intend to enforce.

    This is the leak. If this ever fails, MLX changed its defaults and the
    rest of this module deserves a fresh look.
    """
    _churn_varied_shapes()
    assert mx.get_cache_memory() > _mlx.CACHE_LIMIT_BYTES


def test_cap_cache_bounds_the_cache():
    _mlx.cap_cache()
    _churn_varied_shapes()
    assert mx.get_cache_memory() <= _mlx.CACHE_LIMIT_BYTES


def test_clear_cache_returns_buffers():
    _mlx.cap_cache()
    _churn_varied_shapes()
    before = mx.get_cache_memory()
    assert before > 0
    _mlx.clear_cache()
    # MLX keeps a token residual (16 bytes observed on 0.31.1), so assert the
    # cache was drained rather than demanding a literal zero.
    assert mx.get_cache_memory() < 1024**2
    assert mx.get_cache_memory() < before / 100


def test_cap_cache_is_idempotent():
    """Called on every transcription, so repeat calls must stay cheap and stable."""
    _mlx.cap_cache()
    _mlx.cap_cache()
    # A second real set_cache_limit would return our own limit, not the default;
    # assert the guard held by checking the limit is still ours.
    previous = mx.set_cache_limit(_mlx.CACHE_LIMIT_BYTES)
    assert previous == _mlx.CACHE_LIMIT_BYTES
