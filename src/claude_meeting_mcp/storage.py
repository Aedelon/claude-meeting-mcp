"""File storage management for recordings, transcriptions, and PVs."""

import os
from datetime import datetime, timedelta
from pathlib import Path

import platformdirs

APP_NAME = "claude-meeting-mcp"
RETENTION_DAYS = 30


def _get_data_dir() -> Path:
    """Platform-appropriate data directory.

    Override with CLAUDE_MEETING_DATA_DIR environment variable.
    Defaults to XDG data dir on Linux, ~/Library/Application Support on macOS,
    %LOCALAPPDATA% on Windows.
    """
    env_override = os.environ.get("CLAUDE_MEETING_DATA_DIR")
    if env_override:
        return Path(env_override)
    return Path(platformdirs.user_data_dir(APP_NAME))


RECORDINGS_DIR = _get_data_dir() / "recordings"
TRANSCRIPTIONS_DIR = _get_data_dir() / "transcriptions"
PV_DIR = _get_data_dir() / "pv"


def ensure_dirs() -> None:
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    TRANSCRIPTIONS_DIR.mkdir(parents=True, exist_ok=True)
    PV_DIR.mkdir(parents=True, exist_ok=True)


def generate_filename(prefix: str = "meeting", ext: str = "wav") -> str:
    now = datetime.now()
    return now.strftime(f"%Y-%m-%d_%Hh%M_{prefix}.{ext}")


def list_recordings() -> list[dict]:
    ensure_dirs()
    recordings = []
    for f in sorted(RECORDINGS_DIR.glob("*.wav"), reverse=True):
        stat = f.stat()
        recordings.append(
            {
                "meeting_id": f.stem,
                "filename": f.name,
                "path": str(f),
                "size_mb": round(stat.st_size / (1024 * 1024), 1),
                "created": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            }
        )
    return recordings


def list_transcriptions() -> list[dict]:
    ensure_dirs()
    transcriptions = []
    for f in sorted(TRANSCRIPTIONS_DIR.glob("*.json"), reverse=True):
        transcriptions.append(
            {
                "meeting_id": f.stem,
                "filename": f.name,
                "path": str(f),
                "created": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            }
        )
    return transcriptions


def list_pvs() -> list[dict]:
    """List all available PV (meeting minutes) files."""
    ensure_dirs()
    pvs = []
    for f in sorted(PV_DIR.glob("*.md"), reverse=True):
        # Remove _pv suffix to get meeting_id
        stem = f.stem
        meeting_id = stem.removesuffix("_pv")
        pvs.append(
            {
                "meeting_id": meeting_id,
                "filename": f.name,
                "path": str(f),
                "created": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            }
        )
    return pvs


def cleanup_old_recordings() -> list[str]:
    """Remove recordings older than RETENTION_DAYS."""
    ensure_dirs()
    cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
    removed = []
    for f in RECORDINGS_DIR.glob("*.wav"):
        if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
            f.unlink()
            removed.append(f.name)
    return removed
