"""Google Workspace action execution behind the human approval gate."""

from __future__ import annotations

import base64
import json
import re
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import Settings
from .models import ActionProposal

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/tasks",
]


class GoogleWorkspaceActions:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._code_verifiers_by_state: dict[str, str] = {}

    def status(self) -> dict[str, Any]:
        profile = self.account_profile()
        return {
            "account_email": profile.get("email"),
            "configured": self.settings.google_oauth_client_secrets is not None
            and self.settings.google_oauth_client_secrets.exists(),
            "connected": self._token_path.exists(),
            "client_secrets_path": str(self.settings.google_oauth_client_secrets)
            if self.settings.google_oauth_client_secrets
            else None,
            "token_path": str(self._token_path),
            "scopes": GOOGLE_SCOPES,
            "supported_actions": [
                "send_email",
                "create_calendar_event",
                "create_task",
                "create_keep_note",
            ],
        }

    def account_profile(self) -> dict[str, Any]:
        if not self.settings.google_account_profile.exists():
            return {}
        try:
            return json.loads(self.settings.google_account_profile.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def save_account_email(self, email: str) -> dict[str, Any]:
        email = email.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Enter a valid email address.")
        self.settings.google_account_profile.parent.mkdir(parents=True, exist_ok=True)
        profile = {**self.account_profile(), "email": email}
        self.settings.google_account_profile.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        return profile

    def authorization_url(self, redirect_uri: str) -> str:
        flow = self._flow(redirect_uri)
        state = str(uuid4())
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=state,
        )
        if not flow.code_verifier:
            raise RuntimeError("Google OAuth did not create a code verifier.")
        self._code_verifiers_by_state[state] = flow.code_verifier
        return auth_url

    def handle_callback(self, redirect_uri: str, state: str, code: str) -> None:
        code_verifier = self._code_verifiers_by_state.pop(state, None)
        if code_verifier is None:
            raise ValueError("Unknown OAuth state.")
        flow = self._flow(redirect_uri, state=state, code_verifier=code_verifier)
        flow.fetch_token(code=code)
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_path.write_text(flow.credentials.to_json(), encoding="utf-8")

    def execute(self, proposal: ActionProposal) -> str:
        if proposal.action_type is None:
            return "Approved, but this proposal did not include an executable Google action type."
        creds = self._credentials()
        if proposal.action_type == "send_email":
            return self._send_email(creds, proposal.action_payload)
        if proposal.action_type == "create_calendar_event":
            return self._create_calendar_event(creds, proposal.action_payload)
        if proposal.action_type == "create_task":
            return self._create_task(creds, proposal.action_payload)
        if proposal.action_type == "create_keep_note":
            return self._create_keep_note(creds, proposal.action_payload)
        return f"Unsupported Google action type: {proposal.action_type}"

    @property
    def _token_path(self) -> Path:
        return self.settings.google_oauth_token

    def _flow(
        self,
        redirect_uri: str,
        state: str | None = None,
        code_verifier: str | None = None,
    ):
        self._require_google_packages()
        from google_auth_oauthlib.flow import Flow

        path = self.settings.google_oauth_client_secrets
        if path is None or not path.exists():
            raise RuntimeError(
                "Set GOOGLE_OAUTH_CLIENT_SECRETS to a Google OAuth client JSON file."
            )
        return Flow.from_client_secrets_file(
            str(path),
            scopes=GOOGLE_SCOPES,
            redirect_uri=redirect_uri,
            state=state,
            code_verifier=code_verifier,
        )

    def _credentials(self):
        self._require_google_packages()
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        if not self._token_path.exists():
            raise RuntimeError("Connect Google first from the web UI.")
        creds = Credentials.from_authorized_user_file(str(self._token_path), GOOGLE_SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self._token_path.write_text(creds.to_json(), encoding="utf-8")
        if not creds.valid:
            raise RuntimeError("Google credentials are invalid. Reconnect Google from the web UI.")
        return creds

    def _service(self, creds, api: str, version: str):
        self._require_google_packages()
        from googleapiclient.discovery import build

        return build(api, version, credentials=creds, cache_discovery=False)

    def _send_email(self, creds, payload: dict[str, Any]) -> str:
        to = self._required(payload, "to")
        subject = self._required(payload, "subject")
        body = self._required(payload, "body")
        message = EmailMessage()
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        encoded = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        result = (
            self._service(creds, "gmail", "v1")
            .users()
            .messages()
            .send(userId="me", body={"raw": encoded})
            .execute()
        )
        return f"Sent email to {to}. Gmail message id: {result.get('id', 'unknown')}"

    def _create_calendar_event(self, creds, payload: dict[str, Any]) -> str:
        summary = payload.get("summary") or payload.get("title") or "Home Agents event"
        start = self._required(payload, "start")
        end = self._required(payload, "end")
        timezone = payload.get("timezone") or "UTC"
        attendees = [
            {"email": email}
            for email in payload.get("attendees", [])
            if isinstance(email, str) and email.strip()
        ]
        event: dict[str, Any] = {
            "summary": summary,
            "description": payload.get("description", ""),
            "start": {"dateTime": start, "timeZone": timezone},
            "end": {"dateTime": end, "timeZone": timezone},
        }
        if attendees:
            event["attendees"] = attendees
        conference_version = 0
        if payload.get("create_meet", True):
            event["conferenceData"] = {
                "createRequest": {
                    "requestId": str(uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
            conference_version = 1
        result = (
            self._service(creds, "calendar", "v3")
            .events()
            .insert(
                calendarId=payload.get("calendar_id", "primary"),
                body=event,
                sendUpdates="all" if attendees else "none",
                conferenceDataVersion=conference_version,
            )
            .execute()
        )
        return f"Created calendar event: {result.get('htmlLink', result.get('id', 'unknown'))}"

    def _create_task(self, creds, payload: dict[str, Any]) -> str:
        body = {
            "title": self._required(payload, "title"),
            "notes": payload.get("notes", ""),
        }
        if payload.get("due"):
            body["due"] = payload["due"]
        result = (
            self._service(creds, "tasks", "v1")
            .tasks()
            .insert(tasklist=payload.get("tasklist", "@default"), body=body)
            .execute()
        )
        return f"Created Google Task: {result.get('webViewLink', result.get('id', 'unknown'))}"

    def _create_keep_note(self, creds, payload: dict[str, Any]) -> str:
        body = {
            "title": self._required(payload, "title"),
            "body": {"text": {"text": payload.get("text") or payload.get("body") or ""}},
        }
        result = self._service(creds, "keep", "v1").notes().create(body=body).execute()
        return f"Created Google Keep note: {result.get('name', 'unknown')}"

    def _required(self, payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Google action payload is missing required field '{key}'.")
        return value.strip()

    def _require_google_packages(self) -> None:
        try:
            import google.auth  # noqa: F401
            import google_auth_oauthlib  # noqa: F401
            import googleapiclient  # noqa: F401
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Install Google dependencies from home_agents/requirements.txt first."
            ) from exc
