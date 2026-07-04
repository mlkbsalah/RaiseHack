"""Structured data models shared by specialists and manager."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time
from typing import Any, Literal

from pydantic import BaseModel, Field


Action = Literal["ignore", "continue_observing", "ask_resident", "send_alert"]


class SerializableModel(BaseModel):
    def as_dict(self) -> dict[str, Any]:
        if hasattr(self, "model_dump"):
            return self.model_dump()
        return self.dict()


class AudioObservation(SerializableModel):
    water_sound_detected: bool
    confidence: float = Field(ge=0.0, le=1.0)
    sound_type: Literal[
        "running_tap",
        "shower",
        "washing_machine",
        "dishwasher",
        "unknown_water",
        "not_water",
    ]
    continuous_sound: bool
    explanation: str


class VisionObservation(SerializableModel):
    tap_visible: bool
    water_stream_visible: bool
    confidence: float = Field(ge=0.0, le=1.0)
    person_near_tap: bool
    person_using_sink: bool
    sink_overflow_visible: bool
    explanation: str


class DecisionRecommendation(SerializableModel):
    situation: Literal[
        "normal_use", "probably_unattended", "possible_leak", "uncertain"
    ]
    recommended_action: Action
    urgency: Literal["low", "medium", "high", "critical"]
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_summary: str
    user_message: str


@dataclass
class TapEvent:
    event_id: str
    started_at: float
    last_seen_at: float
    consecutive_positive_ticks: int = 0
    audio: AudioObservation | None = None
    vision: VisionObservation | None = None
    flow_rate_lpm: float | None = None
    recommendation: DecisionRecommendation | None = None
    evidence_log: list[dict[str, Any]] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.last_seen_at - self.started_at)

    @classmethod
    def with_elapsed(
        cls, event_id: str, elapsed_seconds: float, timestamp: float | None = None
    ) -> "TapEvent":
        now = time() if timestamp is None else timestamp
        return cls(event_id=event_id, started_at=now - elapsed_seconds, last_seen_at=now)

