"""LLM-backed evidence interpreters for audio, vision, and decisions."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any, TypeVar

from pydantic import ValidationError

from .config import Settings, get_settings
from .models import AudioObservation, DecisionRecommendation, TapEvent, VisionObservation


T = TypeVar("T")


def _data_url(path: str, allowed: set[str] | None = None) -> str:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Input file does not exist: {path}")
    mime_type, _ = mimetypes.guess_type(file_path.name)
    suffix = file_path.suffix.lower()
    if suffix == ".wav":
        mime_type = "audio/wav"
    elif suffix == ".mp3":
        mime_type = "audio/mpeg"
    if mime_type is None:
        mime_type = "application/octet-stream"
    if allowed is not None and mime_type not in allowed:
        allowed_list = ", ".join(sorted(allowed))
        raise ValueError(f"Unsupported file type {mime_type!r}; expected {allowed_list}")
    encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _validate_json(raw: str, model: type[T]) -> T:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Specialist returned invalid JSON: {exc.msg}") from exc
    payload = _normalize_payload(payload, model)
    try:
        if hasattr(model, "model_validate"):
            return model.model_validate(payload)  # type: ignore[attr-defined, return-value]
        return model.parse_obj(payload)  # type: ignore[attr-defined, return-value]
    except ValidationError as exc:
        raise ValueError(f"Specialist JSON failed validation: {exc}") from exc


def _normalize_payload(payload: Any, model: type[T]) -> Any:
    if not isinstance(payload, dict):
        return payload
    if model is DecisionRecommendation:
        normalized = payload.copy()
        action_aliases = {
            "ask": "ask_resident",
            "alert": "send_alert",
            "observe": "continue_observing",
            "continue": "continue_observing",
        }
        if "recommended_action" not in normalized and "action" in normalized:
            action = str(normalized["action"]).strip().lower()
            normalized["recommended_action"] = action_aliases.get(action, action)
        normalized.setdefault("situation", "uncertain")
        normalized.setdefault("urgency", "medium")
        normalized.setdefault("confidence", 0.5)
        if "user_message" not in normalized and "message" in normalized:
            normalized["user_message"] = normalized["message"]
        if "evidence_summary" not in normalized:
            normalized["evidence_summary"] = str(
                normalized.get("summary")
                or normalized.get("reason")
                or "Model returned a recommendation from supplied evidence."
            )
        return normalized
    return payload


def _schema_instruction(model_name: str, fields: dict[str, str]) -> str:
    lines = [
        "Return exactly one JSON object.",
        "Do not include markdown, extra keys, or prose outside JSON.",
        f"The JSON object must match this {model_name} schema:",
    ]
    lines.extend(f'- "{key}": {description}' for key, description in fields.items())
    return "\n".join(lines)


def _event_payload(event: TapEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "duration_seconds": event.duration_seconds,
        "consecutive_positive_ticks": event.consecutive_positive_ticks,
        "flow_rate_lpm": event.flow_rate_lpm,
        "audio": event.audio.as_dict() if event.audio else None,
        "vision": event.vision.as_dict() if event.vision else None,
        "evidence_log": event.evidence_log[-10:],
    }


class SpecialistBase:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.client: Any | None = None
        if not self.settings.mock_mode:
            try:
                from openai import OpenAI
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "The openai package is required for live Crusoe calls. "
                    "Install requirements.txt or set TAP_AGENT_MOCK=true."
                ) from exc
            api_key = self.settings.crusoe_api_key or os.environ.get("CRUSOE_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "CRUSOE_API_KEY is required unless TAP_AGENT_MOCK=true is set."
                )
            self.client = OpenAI(
                base_url=self.settings.crusoe_base_url,
                api_key=api_key,
            )

    def _json_chat(self, *, model: str, messages: list[dict[str, Any]]) -> str:
        if self.client is None:
            raise RuntimeError("OpenAI client is unavailable in mock mode.")
        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
            extra_body=_disable_thinking_body(model),
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("Specialist returned an empty response.")
        return content


def _disable_thinking_body(model: str) -> dict[str, Any]:
    lower = model.lower()
    if "nemotron" in lower:
        return {"chat_template_kwargs": {"enable_thinking": False}}
    if "kimi" in lower or "deepseek" in lower:
        return {"chat_template_kwargs": {"thinking": False}}
    return {}


class AudioSpecialist(SpecialistBase):
    def analyze(self, audio_path: str) -> AudioObservation:
        if self.settings.mock_mode:
            return AudioObservation(
                water_sound_detected=True,
                confidence=0.82,
                sound_type="running_tap",
                continuous_sound=True,
                explanation="Mock mode: steady broadband water sound consistent with a running tap.",
            )

        audio_url = _data_url(audio_path, {"audio/wav", "audio/mpeg"})
        raw = self._json_chat(
            model=self.settings.multimodal_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an audio evidence classifier. "
                        + _schema_instruction(
                            "AudioObservation",
                            {
                                "water_sound_detected": "boolean",
                                "confidence": "number from 0.0 to 1.0",
                                "sound_type": (
                                    "one of running_tap, shower, washing_machine, "
                                    "dishwasher, unknown_water, not_water"
                                ),
                                "continuous_sound": "boolean",
                                "explanation": "short string",
                            },
                        )
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "audio_url", "audio_url": {"url": audio_url}},
                        {
                            "type": "text",
                            "text": (
                                "Analyze this short smart-home kitchen audio recording and fill "
                                "the required JSON schema."
                            ),
                        },
                    ],
                },
            ],
        )
        return _validate_json(raw, AudioObservation)


class VisionSpecialist(SpecialistBase):
    def analyze(self, image_path: str) -> VisionObservation:
        if self.settings.mock_mode:
            return VisionObservation(
                tap_visible=True,
                water_stream_visible=True,
                confidence=0.86,
                person_near_tap=False,
                person_using_sink=False,
                sink_overflow_visible=False,
                explanation="Mock mode: tap and water stream visible with nobody using the sink.",
            )

        image_url = _data_url(image_path, {"image/jpeg", "image/png", "image/webp"})
        raw = self._json_chat(
            model=self.settings.multimodal_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a visual evidence classifier. "
                        + _schema_instruction(
                            "VisionObservation",
                            {
                                "tap_visible": "boolean",
                                "water_stream_visible": "boolean",
                                "confidence": "number from 0.0 to 1.0",
                                "person_near_tap": "boolean",
                                "person_using_sink": "boolean",
                                "sink_overflow_visible": "boolean",
                                "explanation": "short string",
                            },
                        )
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Analyze the kitchen camera frame and fill the required JSON schema."
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
        )
        return _validate_json(raw, VisionObservation)


class DecisionSpecialist(SpecialistBase):
    def recommend(
        self,
        event: TapEvent,
        preferences: dict[str, Any],
        previous_events: list[dict[str, Any]],
    ) -> DecisionRecommendation:
        if self.settings.mock_mode:
            return DecisionRecommendation(
                situation="probably_unattended",
                recommended_action="ask_resident",
                urgency="medium",
                confidence=0.86,
                evidence_summary="Mock mode: water is visible/audible and no sink use is visible.",
                user_message=(
                    f"The kitchen tap appears to have been running for "
                    f"{event.duration_seconds:.0f} seconds while nobody is using it. "
                    "Is this intentional?"
                ),
            )

        payload = {
            "event": _event_payload(event),
            "preferences": preferences,
            "previous_events": previous_events,
            "constraints": [
                "Never directly control or close a physical valve.",
                "Never claim more certainty than the evidence supports.",
                "Manager safety rules and duration thresholds are enforced outside this response.",
            ],
        }
        raw = self._json_chat(
            model=self.settings.reasoning_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You combine already-extracted smart-home evidence. "
                        + _schema_instruction(
                            "DecisionRecommendation",
                            {
                                "situation": (
                                    "one of normal_use, probably_unattended, "
                                    "possible_leak, uncertain"
                                ),
                                "recommended_action": (
                                    "one of ignore, continue_observing, ask_resident, send_alert"
                                ),
                                "urgency": "one of low, medium, high, critical",
                                "confidence": "number from 0.0 to 1.0",
                                "evidence_summary": "short string summarizing evidence",
                                "user_message": "short resident-facing string",
                            },
                        )
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Recommend a resident-facing action and concise message. "
                        f"Input JSON:\n{json.dumps(payload, sort_keys=True)}"
                    ),
                },
            ],
        )
        return _validate_json(raw, DecisionRecommendation)
