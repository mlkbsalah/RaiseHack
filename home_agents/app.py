"""FastAPI application wiring every piece together and serving the UI.

Run with:

    python -m home_agents.app

which starts an HTTP server the browser (desktop or phone) can open
directly — see home_agents/README.md for the live-camera walkthrough.
"""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent_runner import TaskAgent
from .approvals import ApprovalStore
from .config import get_settings
from .debug_log import DebugLog
from .llm_client import LLMClient
from .memory_store import MemoryStore
from .models import TaskUpdateDraft
from .orchestrator import Orchestrator
from .safety_monitor import SafetyAlertStore, SafetyMonitor
from .scheduler import LatestResults, Scheduler
from .stream_registry import StreamRegistry
from .synthesis import get_synthesizer
from .task_store import TaskStore
from .transcription import get_transcriber
from .telegram_bot import TelegramBot

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"

settings = get_settings()
debug_log = DebugLog(settings.data_dir)
llm = LLMClient(settings)
task_store = TaskStore(settings)
memory_store = MemoryStore(settings, debug_log)
stream_registry = StreamRegistry(settings)
if settings.mock_mode:
    # Demo streams exist so the tap scenario works with zero hardware; in live
    # mode real devices push their own streams, so don't seed fake ones.
    stream_registry.seed_demo_streams(REPO_ROOT / "data")
approval_store = ApprovalStore()
orchestrator = Orchestrator(llm, task_store, stream_registry, memory_store, debug_log)
transcriber = get_transcriber(settings)
synthesizer = get_synthesizer(settings)
task_agent = TaskAgent(llm, memory_store, stream_registry, approval_store, debug_log)
latest_results = LatestResults()
scheduler = Scheduler(task_store, task_agent, latest_results, settings.tick_seconds)
safety_alert_store = SafetyAlertStore()
safety_monitor = SafetyMonitor(llm, stream_registry, safety_alert_store)
telegram = TelegramBot(settings, orchestrator, task_store, approval_store, scheduler, memory_store)
orchestrator.telegram = telegram
task_agent.telegram = telegram
safety_monitor.telegram = telegram

@asynccontextmanager
async def _lifespan(_: FastAPI):
    scheduler.start()
    safety_monitor.start()
    telegram.start()
    yield
    scheduler.stop()
    safety_monitor.stop()
    telegram.stop()


app = FastAPI(title="Home Agents", lifespan=_lifespan)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/api/status")
def status() -> dict:
    return {
        "mock_mode": settings.mock_mode,
        "tick_seconds": settings.tick_seconds,
        "stt": transcriber.provider,
        "tts": "gradium" if synthesizer is not None else "browser",
        "telegram_enabled": telegram.enabled,
        "console": True,
        "debug_log_path": debug_log.path,
    }


@app.get("/api/debug/log")
def debug_events(after: int = 0) -> dict:
    """Tail of the in-memory console log for this server session."""
    return {"events": debug_log.recent(after)}


@app.get("/api/debug/memory")
def debug_memory() -> dict:
    """Current memory tails shown in the bottom console."""
    agents = [
        {
            "task_id": task.task_id,
            "title": task.title,
            "status": task.status,
            "memory": memory_store.read_agent_memory(task.task_id, task.title),
        }
        for task in task_store.list()
    ]
    subjects = [
        {
            "subject_id": subject_id,
            "memory": memory_store.read_subject_memory(subject_id),
        }
        for subject_id in memory_store.list_subjects()
    ]
    return {"agents": agents, "subjects": subjects}


# ---------------------------------------------------------------- chat


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    reply = orchestrator.handle_message(request.message)
    return ChatResponse(reply=reply)


class VoiceChatResponse(BaseModel):
    transcript: str
    reply: str


@app.post("/api/chat/voice", response_model=VoiceChatResponse)
async def chat_voice(file: UploadFile) -> VoiceChatResponse:
    """Voice mode: transcribe a spoken clip, then run it through the SAME
    orchestrator as typed chat. The transcript is returned alongside the reply
    so the UI can show what was heard."""
    audio = await file.read()
    if not audio:
        raise HTTPException(400, "empty audio upload")
    mime_type = file.content_type or "audio/wav"
    try:
        transcript = transcriber.transcribe(audio, mime_type).strip()
    except Exception as exc:  # surface provider/network failures legibly in the UI
        raise HTTPException(502, f"transcription failed: {exc}") from exc
    if not transcript:
        raise HTTPException(422, "no speech recognized")
    reply = orchestrator.handle_message(transcript)
    return VoiceChatResponse(transcript=transcript, reply=reply)


class TTSRequest(BaseModel):
    text: str


@app.post("/api/tts")
def tts(request: TTSRequest) -> Response:
    """Synthesize the orchestrator's reply with Gradium and return raw audio.

    Only available when a Gradium key + voice are configured; otherwise the
    frontend speaks replies with the browser's own voice and never calls this."""
    if synthesizer is None:
        raise HTTPException(404, "gradium tts not configured")
    if not request.text.strip():
        raise HTTPException(400, "empty text")
    try:
        audio, mime_type = synthesizer.synthesize(request.text)
    except Exception as exc:  # surface provider/network failures legibly in the UI
        raise HTTPException(502, f"tts failed: {exc}") from exc
    return Response(content=audio, media_type=mime_type, headers={"Cache-Control": "no-store"})


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
    memory_store.append_agent_log(
        approval.task_id,
        approval.task_title,
        f"User {'approved' if decision.approve else 'denied'} proposed action: {approval.action}",
    )
    telegram.notify_approval_resolved(approval, origin="web")
    return approval.as_dict()


# ----------------------------------------------------------------- safety


@app.get("/api/safety/alerts")
def list_safety_alerts() -> list[dict]:
    return [a.as_dict() for a in safety_alert_store.list_recent()]


@app.post("/api/safety/alerts/{alert_id}/dismiss")
def dismiss_safety_alert(alert_id: str) -> dict:
    alert = safety_alert_store.dismiss(alert_id)
    if alert is None:
        raise HTTPException(404, "alert not found")
    return alert.as_dict()


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
    try:
        stream_registry.put(stream_id, "image", mime_type, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"stream_id": stream_id, "kind": "image", "bytes": len(data)}


@app.post("/api/streams/{stream_id}/audio")
async def upload_audio(stream_id: str, file: UploadFile) -> dict:
    data = await file.read()
    mime_type = file.content_type or "audio/webm"
    try:
        stream_registry.put(stream_id, "audio", mime_type, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
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
