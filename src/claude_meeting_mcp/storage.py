"""File storage management for recordings and transcriptions."""

import os
from pathlib import Path
from datetime import datetime, timedelta

RECORDINGS_DIR = Path(__file__).parent.parent.parent / "recordings"
TRANSCRIPTIONS_DIR = Path(__file__).parent.parent.parent / "transcriptions"
RETENTION_DAYS = 30


def ensure_dirs():
    RECORDINGS_DIR.mkdir(exist_ok=True)
    TRANSCRIPTIONS_DIR.mkdir(exist_ok=True)


def generate_filename(prefix: str = "meeting", ext: str = "wav") -> str:
    now = datetime.now()
    return now.strftime(f"%Y-%m-%d_%Hh%M_{prefix}.{ext}")


def list_recordings() -> list[dict]:
    ensure_dirs()
    recordings = []
    for f in sorted(RECORDINGS_DIR.glob("*.wav"), reverse=True):
        stat = f.stat()
        recordings.append({
            "filename": f.name,
            "path": str(f),
            "size_mb": round(stat.st_size / (1024 * 1024), 1),
            "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
        })
    return recordings


def list_transcriptions() -> list[dict]:
    ensure_dirs()
    transcriptions = []
    for f in sorted(TRANSCRIPTIONS_DIR.glob("*.json"), reverse=True):
        transcriptions.append({
            "filename": f.name,
            "path": str(f),
            "created": datetime.fromtimestamp(f.stat().st_ctime).isoformat(),
        })
    return transcriptions


def cleanup_old_recordings():
    """Remove recordings older than RETENTION_DAYS."""
    ensure_dirs()
    cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
    removed = []
    for f in RECORDINGS_DIR.glob("*.wav"):
        if datetime.fromtimestamp(f.stat().st_ctime) < cutoff:
            f.unlink()
            removed.append(f.name)
    return removed
