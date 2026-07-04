"""Deterministic manager for smart-home tap incidents."""

from __future__ import annotations

from time import time
from typing import Any
from uuid import uuid4

from .config import get_settings
from .memory import TapMemory
from .models import (
    Action,
    AudioObservation,
    DecisionRecommendation,
    TapEvent,
    VisionObservation,
)
from .specialists import AudioSpecialist, DecisionSpecialist, VisionSpecialist


class TapIncidentManager:
    def __init__(
        self,
        memory: TapMemory | None = None,
        audio_specialist: AudioSpecialist | None = None,
        vision_specialist: VisionSpecialist | None = None,
        decision_specialist: DecisionSpecialist | None = None,
    ) -> None:
        settings = get_settings()
        self.memory = memory or TapMemory(settings.sqlite_path)
        self.audio_specialist = audio_specialist or AudioSpecialist(settings)
        self.vision_specialist = vision_specialist or VisionSpecialist(settings)
        self.decision_specialist = decision_specialist or DecisionSpecialist(settings)
        self.active_event: TapEvent | None = None

    def process_tick(
        self,
        image_path: str | None,
        audio_path: str | None,
        flow_rate_lpm: float | None,
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        now = time() if timestamp is None else timestamp
        preferences = self.memory.get_preferences()
        threshold = float(preferences["minimum_flow_rate_lpm"])

        audio = self.audio_specialist.analyze(audio_path) if audio_path else None
        vision = self.vision_specialist.analyze(image_path) if image_path else None

        sensor_positive = flow_rate_lpm is not None and flow_rate_lpm >= threshold
        audio_positive = (
            audio is not None and audio.confidence >= 0.70 and audio.water_sound_detected
        )
        vision_positive = (
            vision is not None
            and vision.confidence >= 0.70
            and vision.water_stream_visible
        )
        overflow_visible = bool(vision and vision.sink_overflow_visible)
        tap_is_visible = vision is not None and vision.tap_visible
        water_probably_running = sensor_positive or vision_positive or (
            audio_positive and tap_is_visible
        ) or overflow_visible

        event = self._update_event(
            now=now,
            water_probably_running=water_probably_running,
            audio=audio,
            vision=vision,
            flow_rate_lpm=flow_rate_lpm,
            evidence={
                "timestamp": now,
                "sensor_positive": sensor_positive,
                "audio_positive": audio_positive,
                "vision_positive": vision_positive,
                "water_probably_running": water_probably_running,
            },
        )

        if event is None:
            return {
                "event_id": None,
                "action": "ignore",
                "duration_seconds": 0.0,
                "confidence": 0.0,
                "message": "No reliable evidence of running tap water.",
            }

        person_using_sink = bool(
            vision
            and vision.person_using_sink
            and preferences.get("ignore_when_person_using_sink", True)
        )
        ask_after = float(preferences["ask_after_seconds"])
        alert_after = float(preferences["alert_after_seconds"])

        if overflow_visible:
            recommendation = DecisionRecommendation(
                situation="possible_leak",
                recommended_action="send_alert",
                urgency="critical",
                confidence=max(vision.confidence if vision else 0.0, 0.9),
                evidence_summary="Visible sink overflow detected.",
                user_message="The kitchen sink appears to be overflowing. Please check it now.",
            )
            event.recommendation = recommendation
            self.memory.save_incident(event)
            self.active_event = None
            return self._response(event, recommendation)

        if person_using_sink:
            recommendation = DecisionRecommendation(
                situation="normal_use",
                recommended_action="ignore",
                urgency="low",
                confidence=vision.confidence if vision else 0.7,
                evidence_summary="A person is visibly using the sink.",
                user_message="The sink appears to be in active use.",
            )
            event.recommendation = recommendation
            return self._response(event, recommendation)

        if event.duration_seconds < ask_after:
            recommendation = DecisionRecommendation(
                situation="uncertain",
                recommended_action="continue_observing",
                urgency="low",
                confidence=self._evidence_confidence(audio, vision, sensor_positive),
                evidence_summary="Running water evidence has not persisted long enough to ask.",
                user_message="Observing the kitchen tap for a little longer.",
            )
            event.recommendation = recommendation
            return self._response(event, recommendation)

        previous = self.memory.get_recent_feedback_events(limit=5)
        recommendation = self.decision_specialist.recommend(event, preferences, previous)
        recommendation = self._enforce_rules(
            recommendation=recommendation,
            event=event,
            alert_after=alert_after,
            person_using_sink=person_using_sink,
        )
        event.recommendation = recommendation

        if recommendation.recommended_action in {"ask_resident", "send_alert"}:
            self.memory.save_incident(event)
        if recommendation.recommended_action == "send_alert":
            self.active_event = None
        return self._response(event, recommendation)

    def _update_event(
        self,
        *,
        now: float,
        water_probably_running: bool,
        audio: AudioObservation | None,
        vision: VisionObservation | None,
        flow_rate_lpm: float | None,
        evidence: dict[str, Any],
    ) -> TapEvent | None:
        if not water_probably_running:
            if self.active_event is not None:
                self.memory.save_incident(self.active_event)
            self.active_event = None
            return None

        if self.active_event is None:
            self.active_event = TapEvent(
                event_id=str(uuid4()),
                started_at=now,
                last_seen_at=now,
                consecutive_positive_ticks=0,
            )

        event = self.active_event
        event.last_seen_at = now
        event.consecutive_positive_ticks += 1
        event.audio = audio
        event.vision = vision
        event.flow_rate_lpm = flow_rate_lpm
        event.evidence_log.append(evidence)
        return event

    def _enforce_rules(
        self,
        *,
        recommendation: DecisionRecommendation,
        event: TapEvent,
        alert_after: float,
        person_using_sink: bool,
    ) -> DecisionRecommendation:
        if person_using_sink:
            return self._copy_recommendation(
                recommendation,
                update={
                    "situation": "normal_use",
                    "recommended_action": "ignore",
                    "urgency": "low",
                    "user_message": "The sink appears to be in active use.",
                }
            )
        if event.duration_seconds >= alert_after:
            return self._copy_recommendation(
                recommendation,
                update={
                    "recommended_action": "send_alert",
                    "urgency": "high",
                    "user_message": (
                        f"The kitchen tap appears to have been running unattended for "
                        f"{event.duration_seconds:.0f} seconds. Please check it now."
                    ),
                }
            )
        if recommendation.recommended_action == "send_alert":
            return self._copy_recommendation(
                recommendation, update={"recommended_action": "ask_resident"}
            )
        if recommendation.recommended_action == "ignore":
            return self._copy_recommendation(
                recommendation, update={"recommended_action": "continue_observing"}
            )
        return recommendation

    def _copy_recommendation(
        self, recommendation: DecisionRecommendation, *, update: dict[str, Any]
    ) -> DecisionRecommendation:
        if hasattr(recommendation, "model_copy"):
            return recommendation.model_copy(update=update)
        return recommendation.copy(update=update)

    def _evidence_confidence(
        self,
        audio: AudioObservation | None,
        vision: VisionObservation | None,
        sensor_positive: bool,
    ) -> float:
        values = []
        if audio:
            values.append(audio.confidence)
        if vision:
            values.append(vision.confidence)
        if sensor_positive:
            values.append(0.95)
        return max(values) if values else 0.0

    def _response(
        self, event: TapEvent, recommendation: DecisionRecommendation
    ) -> dict[str, Any]:
        action: Action = recommendation.recommended_action
        return {
            "event_id": event.event_id,
            "action": action,
            "duration_seconds": round(event.duration_seconds, 3),
            "confidence": recommendation.confidence,
            "message": recommendation.user_message,
        }
