"""FastAPI application wiring every piece together and serving the UI.

Run with:

    python -m home_agents.app

which starts an HTTP server the browser (desktop or phone) can open
directly — see home_agents/README.md for the live-camera walkthrough.
"""

from __future__ import annotations

import base64
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent_runner import TaskAgent
from .approvals import ApprovalStore
from .config import get_settings
from .google_actions import GoogleWorkspaceActions
from .llm_client import LLMClient
from .memory_store import MemoryStore
from .models import ActionProposal, TaskUpdateDraft
from .orchestrator import Orchestrator
from .scheduler import LatestResults, Scheduler
from .stream_registry import StreamRegistry
from .task_store import TaskStore

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"

settings = get_settings()
llm = LLMClient(settings)
task_store = TaskStore(settings)
memory_store = MemoryStore(settings)
stream_registry = StreamRegistry(settings)
if settings.mock_mode:
    # Demo streams exist so the tap scenario works with zero hardware; in live
    # mode real devices push their own streams, so don't seed fake ones.
    stream_registry.seed_demo_streams(REPO_ROOT / "data")
approval_store = ApprovalStore()
google_actions = GoogleWorkspaceActions(settings)
orchestrator = Orchestrator(llm, task_store, stream_registry, memory_store)
task_agent = TaskAgent(llm, memory_store, stream_registry, approval_store)
latest_results = LatestResults()
scheduler = Scheduler(task_store, task_agent, latest_results, settings.tick_seconds)

@asynccontextmanager
async def _lifespan(_: FastAPI):
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="Home Agents", lifespan=_lifespan)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/api/status")
def status() -> dict:
    return {"mock_mode": settings.mock_mode, "tick_seconds": settings.tick_seconds}


# --------------------------------------------------------- google account


def _google_redirect_uri(request: Request) -> str:
    return str(request.url_for("google_callback"))


@app.get("/api/google/status")
def google_status() -> dict:
    return google_actions.status()


@app.get("/api/google/auth/start")
def google_auth_start(request: Request) -> dict:
    try:
        return {"auth_url": google_actions.authorization_url(_google_redirect_uri(request))}
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/google/auth/callback")
def google_callback(request: Request, state: str, code: str) -> RedirectResponse:
    try:
        google_actions.handle_callback(_google_redirect_uri(request), state, code)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse("/")


# ---------------------------------------------------------------- chat


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


def _extract_email(message: str) -> str | None:
    match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", message)
    return match.group(0).lower() if match else None


def _extract_google_task_title(message: str) -> str | None:
    text = message.strip()
    lower = text.lower()
    if not any(term in lower for term in ("to do", "todo", "to-do", "google task", "tasks")):
        return None
    if not any(term in lower for term in ("add", "create", "put")):
        return None
    patterns = [
        r"(?:add|create|put)\s+(?P<title>.+?)\s+(?:to|on|in)\s+(?:my\s+)?(?:gmail|google)?\s*(?:to[- ]?do\s+list|todo\s+list|tasks?)",
        r"(?:add|create|put)\s+(?:to|on|in)\s+(?:my\s+)?(?:gmail|google)?\s*(?:to[- ]?do\s+list|todo\s+list|tasks?)\s+(?:that\s+)?(?P<title>.+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            title = re.sub(r"^(that\s+)?i\s+(have|need)\s+to\s+", "", match.group("title").strip(), flags=re.IGNORECASE)
            return title.strip(" .?!") or None
    fallback = re.search(
        r"(?:to[- ]?do\s+list|todo\s+list|tasks?).*?(?:that\s+)?i\s+(?:have|need)\s+to\s+(?P<title>.+)",
        text,
        flags=re.IGNORECASE,
    )
    if fallback:
        return fallback.group("title").strip(" .?!") or None
    return None


def _extract_calendar_request(message: str) -> dict | None:
    text = message.strip()
    lower = text.lower()
    if "calendar" not in lower and "meeting" not in lower:
        return None
    if not any(term in lower for term in ("add", "create", "schedule", "put")):
        return None

    title = "Meeting"
    title_match = re.search(
        r"(?:meeting|event)\s+(?:with|about)\s+(?P<title>.+?)(?:\s+(?:today|tomorrow|on|at)\b|$)",
        text,
        flags=re.IGNORECASE,
    )
    if title_match:
        title = f"Meeting with {title_match.group('title').strip(' .?!')}"
    elif "manager" in lower:
        title = "Meeting with manager"

    start = _extract_calendar_start(text)
    if start is None:
        return {"title": title, "needs_time": True}
    end = start + timedelta(hours=1)
    return {
        "title": title,
        "start": start.isoformat(timespec="seconds"),
        "end": end.isoformat(timespec="seconds"),
        "timezone": "Europe/Paris",
    }


def _extract_calendar_start(message: str) -> datetime | None:
    lower = message.lower()
    date = None
    now = datetime.now().replace(second=0, microsecond=0)
    if "tomorrow" in lower:
        date = now.date() + timedelta(days=1)
    elif "today" in lower:
        date = now.date()
    explicit_date = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", lower)
    if explicit_date:
        date = datetime(
            int(explicit_date.group(1)),
            int(explicit_date.group(2)),
            int(explicit_date.group(3)),
        ).date()
    time_match = re.search(r"\b(?:at\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", lower)
    if date is None or time_match is None:
        return None
    hour = int(time_match.group(1))
    minute = int(time_match.group(2) or "0")
    suffix = time_match.group(3)
    if suffix == "pm" and hour < 12:
        hour += 12
    if suffix == "am" and hour == 12:
        hour = 0
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return datetime.combine(date, datetime.min.time()).replace(hour=hour, minute=minute)


@app.post("/api/chat", response_model=ChatResponse)
def chat(chat_request: ChatRequest, request: Request) -> ChatResponse:
    message = chat_request.message
    email = _extract_email(message)
    google_words = ("gmail", "google", "calendar", "tasks", "keep", "account", "email")
    if email and any(word in message.lower() for word in google_words):
        google_actions.save_account_email(email)
    google_status_info = google_actions.status()
    calendar_request = _extract_calendar_request(message)
    if calendar_request:
        if calendar_request.get("needs_time"):
            return ChatResponse(
                reply=(
                    f"I can add '{calendar_request['title']}' to Google Calendar. "
                    "What date and time should I use? For example: tomorrow at 3pm."
                )
            )
        if not google_status_info["connected"]:
            return ChatResponse(
                reply=(
                    f"I can add '{calendar_request['title']}' to Google Calendar after Google is connected. "
                    "Click Connect Google first."
                )
            )
        try:
            result = google_actions.execute(
                ActionProposal(
                    action=f"Create Google Calendar event: {calendar_request['title']}",
                    reason="Direct user request from chat.",
                    risk="low",
                    action_type="create_calendar_event",
                    action_payload={
                        "summary": calendar_request["title"],
                        "start": calendar_request["start"],
                        "end": calendar_request["end"],
                        "timezone": calendar_request["timezone"],
                        "attendees": [],
                        "create_meet": True,
                    },
                )
            )
        except Exception as exc:
            return ChatResponse(reply=f"I couldn't add that calendar event: {type(exc).__name__}: {exc}")
        return ChatResponse(reply=result)
    google_task_title = _extract_google_task_title(message)
    if google_task_title:
        if not google_status_info["connected"]:
            return ChatResponse(
                reply=(
                    f"I can add '{google_task_title}' to Google Tasks after Google is connected. "
                    "Click Connect Google first."
                )
            )
        try:
            result = google_actions.execute(
                ActionProposal(
                    action=f"Create Google Task: {google_task_title}",
                    reason="Direct user request from chat.",
                    risk="low",
                    action_type="create_task",
                    action_payload={"title": google_task_title},
                )
            )
        except Exception as exc:
            return ChatResponse(reply=f"I couldn't add that Google Task: {type(exc).__name__}: {exc}")
        return ChatResponse(reply=result)
    google_auth_url = None
    if (
        google_status_info["configured"]
        and google_status_info.get("account_email")
        and not google_status_info["connected"]
    ):
        try:
            google_auth_url = google_actions.authorization_url(_google_redirect_uri(request))
        except Exception:
            google_auth_url = None
    reply = orchestrator.handle_message(
        message,
        google_auth_url=google_auth_url,
        google_configured=google_status_info["configured"],
        google_account_email=google_status_info.get("account_email"),
        google_connected=google_status_info["connected"],
    )
    return ChatResponse(reply=reply)


class GoogleAccountRequest(BaseModel):
    email: str


@app.post("/api/google/account")
def save_google_account(account: GoogleAccountRequest) -> dict:
    try:
        google_actions.save_account_email(account.email)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return google_actions.status()


# ---------------------------------------------------------------- tasks


def _task_view(task) -> dict:
    result = latest_results.get(task.task_id)
    pending = [a for a in approval_store.list_for_task(task.task_id) if a.status == "pending"]
    return {
        "task": task.as_dict(),
        "last_result": result.as_dict() if result else None,
        "pending_approval": pending[0].as_dict() if pending else None,
    }


@app.get("/api/tasks")
def list_tasks() -> list[dict]:
    return [_task_view(t) for t in task_store.list()]


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    view = _task_view(task)
    view["agent_memory"] = memory_store.read_agent_memory(task.task_id, task.title)
    if task.subject_id:
        view["subject_memory"] = memory_store.read_subject_memory(task.subject_id)
    return view


@app.post("/api/tasks/{task_id}/pause")
def pause_task(task_id: str) -> dict:
    task = task_store.set_status(task_id, "paused")
    if task is None:
        raise HTTPException(404, "task not found")
    return _task_view(task)


@app.post("/api/tasks/{task_id}/resume")
def resume_task(task_id: str) -> dict:
    task = task_store.set_status(task_id, "active")
    if task is None:
        raise HTTPException(404, "task not found")
    return _task_view(task)


@app.post("/api/tasks/{task_id}/run_now")
def run_task_now(task_id: str) -> dict:
    result = scheduler.run_task_now(task_id)
    if result is None:
        raise HTTPException(404, "task not found")
    return _task_view(task_store.get(task_id))


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: str, update: TaskUpdateDraft) -> dict:
    if update.subject_id or update.subject_label:
        label = update.subject_label or update.subject_id or "subject"
        update = update.model_copy(
            update={"subject_id": memory_store.resolve_subject_id(label, update.subject_id)}
        )
    task = task_store.patch(task_id, update)
    if task is None:
        raise HTTPException(404, "task not found")
    return _task_view(task)


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str) -> dict:
    if not task_store.delete(task_id):
        raise HTTPException(404, "task not found")
    return {"deleted": task_id}


# ------------------------------------------------------------ approvals


class ApprovalDecision(BaseModel):
    approve: bool


@app.get("/api/approvals")
def list_approvals() -> list[dict]:
    return [a.as_dict() for a in approval_store.list_pending()]


@app.post("/api/approvals/{approval_id}/decision")
def decide_approval(approval_id: str, decision: ApprovalDecision) -> dict:
    approval = approval_store.decide(approval_id, decision.approve)
    if approval is None:
        raise HTTPException(404, "approval not found")
    execution_note = ""
    if decision.approve and approval.action_type:
        try:
            result = google_actions.execute(
                ActionProposal(
                    action=approval.action,
                    reason=approval.reason,
                    risk=approval.risk,
                    action_type=approval.action_type,
                    action_payload=approval.action_payload,
                )
            )
            approval_store.record_execution(approval.approval_id, True, result)
            execution_note = f" Executed Google action: {result}"
        except Exception as exc:
            result = f"{type(exc).__name__}: {exc}"
            approval_store.record_execution(approval.approval_id, False, result)
            execution_note = f" Google action failed: {result}"
            approval = approval_store.get(approval_id) or approval
    memory_store.append_agent_log(
        approval.task_id,
        approval.task_title,
        f"User {'approved' if decision.approve else 'denied'} proposed action: "
        f"{approval.action}.{execution_note}",
    )
    return approval.as_dict()


# --------------------------------------------------------------- streams


class ImageUpload(BaseModel):
    data_url: str


@app.get("/api/streams")
def list_streams() -> list[dict]:
    return stream_registry.list_streams()


@app.get("/api/streams/pairs")
def list_stream_pairs() -> list[dict]:
    """Streams grouped into camera+voice pairs (one entry per place watched)."""
    return stream_registry.list_pairs()


@app.get("/api/streams/{stream_id}/latest")
def stream_latest(stream_id: str) -> Response:
    """Latest frame/clip bytes for a stream, so the UI can show every ingested
    stream (from any phone or script), not just this device's own camera."""
    latest = stream_registry.get_latest_bytes(stream_id)
    if latest is None:
        raise HTTPException(404, "no data for stream")
    data, mime_type = latest
    return Response(content=data, media_type=mime_type, headers={"Cache-Control": "no-store"})


@app.post("/api/streams/{stream_id}/image")
def upload_image(stream_id: str, upload: ImageUpload) -> dict:
    try:
        header, encoded = upload.data_url.split(",", 1)
        mime_type = header.split(":")[1].split(";")[0]
        data = base64.b64decode(encoded)
    except (ValueError, IndexError) as exc:
        raise HTTPException(400, f"invalid data url: {exc}") from exc
    stream_registry.put(stream_id, "image", mime_type, data)
    return {"stream_id": stream_id, "kind": "image", "bytes": len(data)}


@app.post("/api/streams/{stream_id}/audio")
async def upload_audio(stream_id: str, file: UploadFile) -> dict:
    data = await file.read()
    mime_type = file.content_type or "audio/webm"
    stream_registry.put(stream_id, "audio", mime_type, data)
    return {"stream_id": stream_id, "kind": "audio", "bytes": len(data)}


@app.delete("/api/streams/{stream_id}")
def delete_stream(stream_id: str) -> dict:
    """Drop a stream now (a card was stopped); idempotent, so unknown ids are ok."""
    return {"stream_id": stream_id, "removed": stream_registry.remove(stream_id)}


# -------------------------------------------------------------- subjects


@app.get("/api/subjects")
def list_subjects() -> list[dict]:
    return [
        {"subject_id": s, "memory": memory_store.read_subject_memory(s)}
        for s in memory_store.list_subjects()
    ]


def main() -> None:
    import uvicorn

    uvicorn.run("home_agents.app:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
