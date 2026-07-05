"""Hidden background safety monitor.

Unlike user-defined tasks (``task_store.py`` / ``scheduler.py``), this loop
is never created by the orchestrator, never appears in ``Tasks.md``,
``/api/tasks``, or the Telegram ``/tasks`` list, and can't be paused or
edited by a user — it just always runs. It exists to catch things nobody
explicitly asked to be watched for.

Every ``SAFETY_TICK_SECONDS`` it walks every currently-connected camera+mic
pair and checks the latest frame/clip against a fixed, hardcoded checklist
of household dangers. "Dangerous or abnormal" has no agreed-upon definition,
so rather than let the model freelance a new definition every run — and
drift — the checklist below is the single source of truth for what counts
as a safety alert here: the categories are fixed in code, only the judgment
call of whether a given frame matches one is the model's job.
"""

from __future__ import annotations

import threading
from time import time
from typing import Any
from uuid import uuid4

from .llm_client import LLMClient, schema_instruction, validate_json
from .models import DangerCheck, SafetyAlert
from .stream_registry import StreamPayload, StreamRegistry

SAFETY_TICK_SECONDS = 5.0

HOUSEHOLD_DANGERS = [
    "fire or visible smoke",
    "a person lying motionless on the floor (possible fall or medical emergency)",
    "an unrecognized person forcing entry through a door or window (possible intruder)",
    "a stove, oven, or other open flame left on with nobody present in the room",
    "an unattended child near stairs, a pool, or a hot surface/appliance",
    "visible flooding or a large, spreading water leak",
    "screaming, calling for help, or other sounds of distress",
    "a smoke or carbon-monoxide alarm sounding",
    "the sound of glass breaking or a crash consistent with an accident",
    "a visible weapon",
]

_DANGER_SCHEMA = {
    "danger_detected": "boolean — true only if one of the listed dangers is clearly present right now",
    "danger_type": "the matching checklist item, verbatim, or null if danger_detected is false",
    "description": "one plain-language sentence describing exactly what was observed",
    "confidence": "number from 0.0 to 1.0",
    "urgency": "one of low|medium|high — how urgently a human should be notified",
}

_SYSTEM_PROMPT = (
    "You are a background safety monitor for a household. You are given the "
    "latest camera frame and/or microphone clip for one location. Compare "
    "only against this fixed checklist of dangers — do not flag anything "
    "outside of it, and do not invent new categories:\n"
    + "\n".join(f"- {item}" for item in HOUSEHOLD_DANGERS)
    + "\n\nEveryday, harmless activity (cooking with the stove attended, "
    "someone sleeping on a couch, ordinary conversation, pets moving "
    "around) is not a danger. Missing or ambiguous evidence means "
    "danger_detected must be false — never guess to be safe. "
    + schema_instruction("DangerCheck", _DANGER_SCHEMA)
)


class SafetyAlertStore:
    """In-memory feed of raised safety alerts, most-recent first."""

    def __init__(self, max_alerts: int = 100) -> None:
        self._lock = threading.Lock()
        self._alerts: list[SafetyAlert] = []
        self._max_alerts = max_alerts

    def add(self, alert: SafetyAlert) -> None:
        with self._lock:
            self._alerts.insert(0, alert)
            del self._alerts[self._max_alerts :]

    def list_recent(self) -> list[SafetyAlert]:
        return list(self._alerts)

    def dismiss(self, alert_id: str) -> SafetyAlert | None:
        with self._lock:
            for alert in self._alerts:
                if alert.alert_id == alert_id:
                    alert.status = "dismissed"
                    alert.dismissed_at = time()
                    return alert
        return None


class SafetyMonitor:
    def __init__(self, llm: LLMClient, streams: StreamRegistry, alerts: SafetyAlertStore) -> None:
        self.llm = llm
        self.streams = streams
        self.alerts = alerts
        # Set by app.py once the optional Telegram bridge exists; same duck-typed
        # pattern as TaskAgent.telegram in agent_runner.py.
        self.telegram: Any | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._mock_counts: dict[str, int] = {}

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._tick()
            self._stop.wait(SAFETY_TICK_SECONDS)

    def _tick(self) -> None:
        for pair in self.streams.list_pairs():
            try:
                self._check_pair(pair)
            except Exception as exc:  # noqa: BLE001 - one bad stream must not kill the loop
                print(f"[safety] check of {pair['name']} failed: {exc}")

    def _check_pair(self, pair: dict) -> None:
        camera = pair.get("camera")
        mic = pair.get("mic")
        payloads: list[StreamPayload] = []
        if camera:
            payloads.append(self.streams.get_payload(camera["stream_id"], "image"))
        if mic:
            payloads.append(self.streams.get_payload(mic["stream_id"], "audio"))
        if not any(p.connected for p in payloads):
            return

        check = self._mock_check(pair["name"]) if self.llm.settings.mock_mode else self._live_check(payloads)
        if check.danger_detected:
            self._raise_alert(pair["name"], check)

    def _live_check(self, payloads: list[StreamPayload]) -> DangerCheck:
        content: list[dict] = [{"type": "text", "text": "Latest evidence for this location."}]
        for payload in payloads:
            if not payload.connected or payload.data_url is None:
                continue
            if payload.kind == "image":
                content.append({"type": "image_url", "image_url": {"url": payload.data_url}})
            else:
                content.append({"type": "audio_url", "audio_url": {"url": payload.data_url}})

        raw = self.llm.chat_json(
            model=self.llm.settings.multimodal_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
        )
        return validate_json(raw, DangerCheck)

    def _mock_check(self, stream_name: str) -> DangerCheck:
        count = self._mock_counts.get(stream_name, 0)
        self._mock_counts[stream_name] = count + 1
        if count % 4 == 3:
            danger = HOUSEHOLD_DANGERS[count % len(HOUSEHOLD_DANGERS)]
            return DangerCheck(
                danger_detected=True,
                danger_type=danger,
                description=f"Mock detection: evidence consistent with '{danger}'.",
                confidence=0.82,
                urgency="high",
            )
        return DangerCheck(danger_detected=False, description="Nothing unusual.", confidence=0.9)

    def _raise_alert(self, stream_name: str, check: DangerCheck) -> None:
        alert = SafetyAlert(
            alert_id=str(uuid4())[:8],
            stream_name=stream_name,
            danger_type=check.danger_type or "unspecified",
            description=check.description,
            confidence=check.confidence,
            urgency=check.urgency,
            status="active",
            created_at=time(),
        )
        self.alerts.add(alert)
        print(f"[safety] ALERT at {stream_name}: {alert.danger_type} ({alert.urgency})")
        if self.telegram is not None:
            self.telegram.notify_safety_alert(alert)
