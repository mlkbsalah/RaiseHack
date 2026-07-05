"""Turn direct chat requests into structured Google Workspace actions."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from .llm_client import LLMClient, schema_instruction, validate_json
from .models import GoogleActionPlan

_PLAN_SCHEMA = {
    "is_google_action": (
        "true only if the user is asking to perform a Google Workspace action "
        "such as sending Gmail, creating a Calendar event, or creating a Google Task"
    ),
    "action_type": (
        "null when is_google_action is false, otherwise one of send_email, "
        "create_calendar_event, create_task, create_keep_note"
    ),
    "action_payload": (
        "object with known fields. send_email needs {to, subject, body}; "
        "create_calendar_event needs {summary, start, end, timezone, attendees, create_meet}; "
        "create_task needs {title, notes, due}; create_keep_note needs {title, text}. "
        "Use ISO datetime strings for calendar start/end and due dates when known."
    ),
    "missing_fields": "array of required fields still missing from action_payload",
    "clarification_question": "question to ask if required fields are missing, otherwise null",
    "summary": "short plain-language description of the action, or null",
}


class GoogleActionPlanner:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def plan(
        self,
        message: str,
        *,
        pending: GoogleActionPlan | None = None,
        timezone: str = "Europe/Paris",
    ) -> GoogleActionPlan:
        if self.llm.settings.mock_mode:
            return self._mock_plan(message, pending=pending, timezone=timezone)
        return self._live_plan(message, pending=pending, timezone=timezone)

    def _live_plan(
        self,
        message: str,
        *,
        pending: GoogleActionPlan | None,
        timezone: str,
    ) -> GoogleActionPlan:
        now = datetime.now().astimezone()
        system = (
            "You extract direct Google Workspace actions from chat. "
            "If there is a pending action, merge the new user message into it. "
            "Do not ask the user to reconnect Google. Do not invent email addresses, "
            "attendees, or dates/times. Use the supplied current date/time to resolve "
            "relative dates like today, tomorrow, or Monday. Default event length is "
            "one hour when start is known and end is not. For a calendar request with "
            "only a time, keep that time in action_payload and ask for the missing date. "
            "For a calendar request with only a date, keep that date and ask for the "
            "missing time. "
            + schema_instruction("GoogleActionPlan", _PLAN_SCHEMA)
        )
        user = json.dumps(
            {
                "message": message,
                "pending": pending.as_dict() if pending else None,
                "current_datetime": now.isoformat(timespec="seconds"),
                "timezone": timezone,
            },
            sort_keys=True,
        )
        raw = self.llm.chat_json(
            model=self.llm.settings.reasoning_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return validate_json(raw, GoogleActionPlan)

    def _mock_plan(
        self,
        message: str,
        *,
        pending: GoogleActionPlan | None,
        timezone: str,
    ) -> GoogleActionPlan:
        text = message.strip()
        lower = text.lower()
        if pending and pending.is_google_action:
            return self._mock_merge_pending(text, pending, timezone)
        if "calendar" in lower or "meeting" in lower or "appointment" in lower:
            return self._mock_calendar_plan(text, timezone)
        if any(term in lower for term in ("to do", "todo", "to-do", "google task", "tasks")):
            return self._mock_task_plan(text)
        return GoogleActionPlan(is_google_action=False)

    def _mock_task_plan(self, text: str) -> GoogleActionPlan:
        title = text
        patterns = [
            r"(?:add|create|put)\s+(?P<title>.+?)\s+(?:to|on|in)\s+(?:my\s+)?(?:gmail|google)?\s*(?:to[- ]?do\s+list|todo\s+list|tasks?)",
            r"(?:add|create|put)\s+(?:to|on|in)\s+(?:my\s+)?(?:gmail|google)?\s*(?:to[- ]?do\s+list|todo\s+list|tasks?)\s+(?:that\s+)?(?P<title>.+)",
            r"(?:to[- ]?do\s+list|todo\s+list|tasks?).*?(?:that\s+)?i\s+(?:have|need)\s+to\s+(?P<title>.+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                title = re.sub(
                    r"^(that\s+)?i\s+(have|need)\s+to\s+",
                    "",
                    match.group("title").strip(),
                    flags=re.IGNORECASE,
                )
                break
        title = title.strip(" .?!")
        if not title:
            return GoogleActionPlan(
                is_google_action=True,
                action_type="create_task",
                missing_fields=["title"],
                clarification_question="What should I add to Google Tasks?",
            )
        return GoogleActionPlan(
            is_google_action=True,
            action_type="create_task",
            action_payload={"title": title},
            summary=f"Create Google Task: {title}",
        )

    def _mock_calendar_plan(self, text: str, timezone: str) -> GoogleActionPlan:
        lower = text.lower()
        title = "Meeting" if "meeting" in lower else "Appointment"
        title_match = re.search(
            r"(?:meeting|event)\s+(?:with|about)\s+(?P<title>.+?)(?:\s+(?:today|tomorrow|on|at)\b|$)",
            text,
            flags=re.IGNORECASE,
        )
        if title_match:
            title = f"Meeting with {title_match.group('title').strip(' .?!')}"
        elif "manager" in lower:
            title = "Meeting with manager"
        payload: dict[str, Any] = {"summary": title, "timezone": timezone, "attendees": [], "create_meet": True}
        date = self._mock_date(text)
        time_value = self._mock_time(text)
        missing = []
        if date is None:
            missing.append("date")
        if time_value is None:
            missing.append("time")
        if missing:
            if date:
                payload["date"] = date.isoformat()
            if time_value:
                payload["time"] = f"{time_value[0]:02d}:{time_value[1]:02d}"
            question = (
                f"What time should I use for '{title}'? For example: 3pm."
                if missing == ["time"]
                else f"What date should I use for '{title}'? For example: tomorrow or Monday."
                if missing == ["date"]
                else f"What date and time should I use for '{title}'? For example: tomorrow at 3pm."
            )
            return GoogleActionPlan(
                is_google_action=True,
                action_type="create_calendar_event",
                action_payload=payload,
                missing_fields=missing,
                clarification_question=question,
                summary=f"Create Google Calendar event: {title}",
            )
        assert date is not None and time_value is not None
        start = datetime.combine(date, datetime.min.time()).replace(
            hour=time_value[0], minute=time_value[1]
        )
        payload["start"] = start.isoformat(timespec="seconds")
        payload["end"] = (start + timedelta(hours=1)).isoformat(timespec="seconds")
        return GoogleActionPlan(
            is_google_action=True,
            action_type="create_calendar_event",
            action_payload=payload,
            summary=f"Create Google Calendar event: {title}",
        )

    def _mock_merge_pending(
        self,
        text: str,
        pending: GoogleActionPlan,
        timezone: str,
    ) -> GoogleActionPlan:
        if pending.action_type != "create_calendar_event":
            return pending
        payload = dict(pending.action_payload)
        date = self._mock_date(text)
        time_value = self._mock_time(text)
        if date:
            payload["date"] = date.isoformat()
        if time_value:
            payload["time"] = f"{time_value[0]:02d}:{time_value[1]:02d}"
        date_text = payload.get("date")
        time_text = payload.get("time")
        missing = []
        if not date_text:
            missing.append("date")
        if not time_text:
            missing.append("time")
        title = payload.get("summary", "Meeting")
        if missing:
            question = (
                f"What time should I use for '{title}'? For example: 3pm."
                if missing == ["time"]
                else f"What date should I use for '{title}'? For example: tomorrow or Monday."
                if missing == ["date"]
                else f"What date and time should I use for '{title}'? For example: tomorrow at 3pm."
            )
            return GoogleActionPlan(
                is_google_action=True,
                action_type="create_calendar_event",
                action_payload=payload,
                missing_fields=missing,
                clarification_question=question,
                summary=pending.summary,
            )
        hour, minute = (int(part) for part in str(time_text).split(":", 1))
        start = datetime.fromisoformat(str(date_text)).replace(hour=hour, minute=minute)
        payload.pop("date", None)
        payload.pop("time", None)
        payload["timezone"] = payload.get("timezone", timezone)
        payload["attendees"] = payload.get("attendees", [])
        payload["create_meet"] = payload.get("create_meet", True)
        payload["start"] = start.isoformat(timespec="seconds")
        payload["end"] = (start + timedelta(hours=1)).isoformat(timespec="seconds")
        return GoogleActionPlan(
            is_google_action=True,
            action_type="create_calendar_event",
            action_payload=payload,
            summary=pending.summary,
        )

    def _mock_date(self, text: str):
        lower = text.lower()
        now = datetime.now()
        if "tomorrow" in lower:
            return now.date() + timedelta(days=1)
        if "today" in lower:
            return now.date()
        explicit = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", lower)
        if explicit:
            return datetime(
                int(explicit.group(1)),
                int(explicit.group(2)),
                int(explicit.group(3)),
            ).date()
        weekdays = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }
        for name, weekday in weekdays.items():
            if name in lower:
                days = (weekday - now.date().weekday()) % 7
                return now.date() + timedelta(days=days or 7)
        return None

    def _mock_time(self, text: str) -> tuple[int, int] | None:
        match = re.search(r"\b(?:at\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", text.lower())
        if not match:
            return None
        hour = int(match.group(1))
        minute = int(match.group(2) or "0")
        suffix = match.group(3)
        if suffix == "pm" and hour < 12:
            hour += 12
        if suffix == "am" and hour == 12:
            hour = 0
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            return None
        return hour, minute
