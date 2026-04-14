#!/usr/bin/env python3
"""Standalone cleanup script for old recordings. Can be run via cron."""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "src"))
from claude_meeting_mcp.storage import cleanup_old_recordings

removed = cleanup_old_recordings()
if removed:
    print(f"Removed {len(removed)} old recordings: {', '.join(removed)}")
else:
    print("No recordings to clean up.")
