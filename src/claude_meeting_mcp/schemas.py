"""JSON schemas for transcription output."""

from dataclasses import dataclass, field
from typing import Optional
import json
from datetime import datetime


@dataclass
class Segment:
    start: float
    end: float
    speaker: str
    text: str

    def to_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "speaker": self.speaker, "text": self.text}


@dataclass
class Transcription:
    meeting_id: str
    date: str
    duration_seconds: float
    speakers: dict[str, str] = field(default_factory=lambda: {"left": "Interlocuteur", "right": "Delanoe"})
    segments: list[Segment] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "meeting_id": self.meeting_id,
            "date": self.date,
            "duration_seconds": self.duration_seconds,
            "speakers": self.speakers,
            "segments": [s.to_dict() for s in self.segments],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_json(cls, data: str) -> "Transcription":
        d = json.loads(data)
        segments = [Segment(**s) for s in d.get("segments", [])]
        return cls(
            meeting_id=d["meeting_id"],
            date=d["date"],
            duration_seconds=d["duration_seconds"],
            speakers=d.get("speakers", {}),
            segments=segments,
        )
