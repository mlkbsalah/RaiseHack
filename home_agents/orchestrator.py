"""Chat-facing orchestrator: turns free text into task operations.

One call in, one structured decision out — the orchestrator never loops or
calls itself; it classifies the message's intent, and deterministic Python
(``_apply``) carries it out against the task store. In mock mode the same
intents are produced by keyword heuristics so the whole framework is
demoable without an API key, mirroring ``tap_agent``'s mock mode.
"""

from __future__ import annotations

import json

from .llm_client import LLMClient, schema_instruction, validate_json
from .memory_store import MemoryStore
from .models import OrchestratorReply, StreamRequirement, TaskDraft, TaskSpec, TaskUpdateDraft
from .stream_registry import StreamRegistry
from .task_store import TaskStore

_REPLY_SCHEMA = {
    "intent": (
        "one of create_task, update_task, pause_task, resume_task, "
        "delete_task, list_tasks, list_streams, connect_google, chat"
    ),
    "reply": "short conversational string shown to the user immediately",
    "task": (
        "null unless intent is create_task or update_task, else an object: "
        "For create_task, include all fields: {title, description, focus "
        "(what the agent should look for each run), interval_seconds "
        "(integer >= 1), "
        "subject_id (an id from context.known_subjects, or null), "
        "subject_label (human name for a brand-new subject, or null), "
        "streams (array of {stream_id, kind: image|audio}, prefer ids from "
        "context.known_streams; each place in context.known_stream_pairs "
        "exposes a camera '<name>-cam' (image) and a microphone '<name>-mic' "
        "(audio) — include BOTH when a task should watch and listen to that "
        "place), requires_approval (bool, default true)}. "
        "For update_task, include only the fields the user wants changed."
    ),
    "target_task_id": (
        "null unless intent is update_task/pause_task/resume_task/delete_task, "
        "else the task_id or title text of the task to target, taken from "
        "context.existing_tasks. If the user says 'that task', 'it', or asks "
        "for a detail change after creating a task, use context.most_recent_task."
    ),
}


class Orchestrator:
    def __init__(
        self,
        llm: LLMClient,
        task_store: TaskStore,
        stream_registry: StreamRegistry,
        memory_store: MemoryStore,
    ) -> None:
        self.llm = llm
        self.task_store = task_store
        self.stream_registry = stream_registry
        self.memory_store = memory_store

    def handle_message(
        self,
        message: str,
        google_auth_url: str | None = None,
        google_configured: bool | None = None,
    ) -> str:
        context = self._build_context()
        if google_configured is not None:
            context["google_configured"] = google_configured
        if self.llm.settings.mock_mode:
            reply = self._mock_reply(message)
        else:
            reply = self._live_reply(message, context)
        return self._apply(reply, google_auth_url=google_auth_url, google_configured=google_configured)

    def _build_context(self) -> dict:
        tasks = self.task_store.list()
        most_recent = max(tasks, key=lambda t: t.updated_at, default=None)
        return {
            "existing_tasks": [
                {
                    "task_id": t.task_id,
                    "title": t.title,
                    "status": t.status,
                    "description": t.description,
                    "focus": t.focus,
                    "interval_seconds": t.interval_seconds,
                    "streams": [s.as_dict() for s in t.streams],
                    "updated_at": t.updated_at,
                }
                for t in tasks
            ],
            "most_recent_task": (
                {"task_id": most_recent.task_id, "title": most_recent.title}
                if most_recent
                else None
            ),
            "known_streams": self.stream_registry.list_streams(),
            "known_stream_pairs": self.stream_registry.list_pairs(),
            "known_subjects": self.memory_store.list_subjects(),
        }

    def _live_reply(self, message: str, context: dict) -> OrchestratorReply:
        system = (
            "You are the orchestrator of a home multi-agent monitoring system. "
            "Residents describe in plain language what they want watched; you "
            "convert that into exactly one structured operation. Never invent "
            "stream ids that are not in context.known_streams unless the user "
            "is clearly asking about a brand-new device they intend to connect. "
            "If the user asks to connect Gmail, Google, Calendar, Tasks, Keep, "
            "or their Google account, use intent connect_google. "
            + schema_instruction("OrchestratorReply", _REPLY_SCHEMA)
        )
        user = json.dumps({"message": message, "context": context}, sort_keys=True)
        raw = self.llm.chat_json(
            model=self.llm.settings.reasoning_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return validate_json(raw, OrchestratorReply)

    def _apply(
        self,
        reply: OrchestratorReply,
        google_auth_url: str | None = None,
        google_configured: bool | None = None,
    ) -> str:
        if reply.intent == "create_task" and reply.task is not None:
            return self._create_task(reply)
        if reply.intent == "update_task":
            return self._update_task(reply)
        if reply.intent in {"pause_task", "resume_task", "delete_task"} and reply.target_task_id:
            return self._change_task_status(reply)
        if reply.intent == "list_tasks":
            return self._list_tasks(reply.reply)
        if reply.intent == "list_streams":
            return self._list_streams(reply.reply)
        if reply.intent == "connect_google":
            return self._connect_google(reply.reply, google_auth_url, google_configured)
        return reply.reply

    def _create_task(self, reply: OrchestratorReply) -> str:
        draft = reply.task
        if not isinstance(draft, TaskDraft):
            return "I couldn't create that task because some required details were missing."
        subject_id = None
        if draft.subject_id or draft.subject_label:
            label = draft.subject_label or draft.subject_id or "subject"
            subject_id = self.memory_store.resolve_subject_id(label, draft.subject_id)
        task = TaskSpec.from_draft(draft.model_copy(update={"subject_id": subject_id}))
        self.task_store.add(task)
        known = set(self.stream_registry.known_ids())
        missing = [s.stream_id for s in task.streams if s.stream_id not in known]
        note = f" Still waiting on stream(s): {', '.join(missing)}." if missing else ""
        return f"{reply.reply}{note}"

    def _update_task(self, reply: OrchestratorReply) -> str:
        task = self._resolve_target(reply.target_task_id)
        if task is None:
            return f"{reply.reply} (I couldn't find which task to update.)"
        if reply.task is None:
            return f"{reply.reply} (Tell me which details to change.)"
        update = (
            reply.task
            if isinstance(reply.task, TaskUpdateDraft)
            else TaskUpdateDraft(**reply.task.model_dump(exclude_unset=True))
        )
        if update.subject_id or update.subject_label:
            label = update.subject_label or update.subject_id or "subject"
            update = update.model_copy(
                update={"subject_id": self.memory_store.resolve_subject_id(label, update.subject_id)}
            )
        updated = self.task_store.patch(task.task_id, update)
        if updated is None:
            return f"{reply.reply} (I couldn't update that task.)"
        return reply.reply

    def _change_task_status(self, reply: OrchestratorReply) -> str:
        task = self._resolve_target(reply.target_task_id)
        if task is None:
            return f"{reply.reply} (I couldn't find a task matching '{reply.target_task_id}'.)"
        if reply.intent == "pause_task":
            self.task_store.set_status(task.task_id, "paused")
        elif reply.intent == "resume_task":
            self.task_store.set_status(task.task_id, "active")
        else:
            self.task_store.delete(task.task_id)
        return reply.reply

    def _resolve_target(self, target: str | None) -> TaskSpec | None:
        if target:
            task = self.task_store.find_by_title(target)
            if task is not None:
                return task
        tasks = self.task_store.list()
        if len(tasks) == 1:
            return tasks[0]
        return max(tasks, key=lambda t: t.updated_at, default=None)

    def _list_tasks(self, prefix: str) -> str:
        tasks = self.task_store.list()
        if not tasks:
            return "No tasks yet — tell me what you'd like watched."
        lines = [f"- {t.title} ({t.status}, every {t.interval_seconds}s)" for t in tasks]
        return prefix + "\n" + "\n".join(lines)

    def _list_streams(self, prefix: str) -> str:
        streams = self.stream_registry.list_streams()
        if not streams:
            return "No streams connected yet."
        lines = [f"- {s['stream_id']} ({s['kind']}, {s['source']})" for s in streams]
        return prefix + "\n" + "\n".join(lines)

    def _connect_google(
        self,
        prefix: str,
        google_auth_url: str | None,
        google_configured: bool | None,
    ) -> str:
        if google_configured is False:
            return (
                "Google is not configured yet. Set GOOGLE_OAUTH_CLIENT_SECRETS "
                "in .env to your Google OAuth client JSON path, restart the app, "
                "then ask me to connect Google again."
            )
        if not google_auth_url:
            return (
                f"{prefix}\n\nGoogle connection is available from the Google actions panel."
            )
        return f"{prefix}\n\nOpen this link to connect Google:\n{google_auth_url}"

    # -- mock mode -----------------------------------------------------

    def _mock_reply(self, message: str) -> OrchestratorReply:
        text = message.lower()
        if self._is_google_connect_request(text):
            return OrchestratorReply(
                intent="connect_google",
                reply="I can connect your Google account for Gmail, Calendar, Tasks, and supported Keep actions.",
            )
        if "list tasks" in text or "what tasks" in text or "my tasks" in text:
            return OrchestratorReply(intent="list_tasks", reply="Here are your tasks:")
        if "list streams" in text or "what streams" in text or "connected" in text:
            return OrchestratorReply(intent="list_streams", reply="Here are the connected streams:")
        for keyword, intent in (("pause", "pause_task"), ("stop", "pause_task")):
            if keyword in text:
                target = self._mock_find_target(text)
                if target:
                    return OrchestratorReply(
                        intent=intent, reply=f"Pausing '{target}'.", target_task_id=target
                    )
        if "resume" in text or "unpause" in text:
            target = self._mock_find_target(text)
            if target:
                return OrchestratorReply(
                    intent="resume_task", reply=f"Resuming '{target}'.", target_task_id=target
                )
        if "delete" in text or "remove task" in text:
            target = self._mock_find_target(text)
            if target:
                return OrchestratorReply(
                    intent="delete_task", reply=f"Deleting '{target}'.", target_task_id=target
                )
        if any(k in text for k in ("every", "seconds", "minutes", "interval", "rename", "change", "update")):
            target = self._mock_find_target(text) or self._mock_recent_target()
            if target:
                interval = self._mock_interval_seconds(text)
                update = TaskUpdateDraft(interval_seconds=interval) if interval else TaskUpdateDraft()
                if "rename" in text:
                    update.title = message.split("rename", 1)[-1].strip(" .") or None
                return OrchestratorReply(
                    intent="update_task",
                    reply=f"Updated '{target}'.",
                    task=update,
                    target_task_id=target,
                )
        draft = self._mock_draft(message)
        return OrchestratorReply(
            intent="create_task",
            reply=f"Got it — I'll create the task \"{draft.title}\" and check every "
            f"{draft.interval_seconds}s.",
            task=draft,
        )

    def _is_google_connect_request(self, text: str) -> bool:
        account_terms = ("gmail", "google", "calendar", "tasks", "keep")
        connect_terms = ("connect", "link", "add", "authorize", "authorise", "login", "sign in")
        return any(a in text for a in account_terms) and any(c in text for c in connect_terms)

    def _mock_find_target(self, text: str) -> str | None:
        for task in self.task_store.list():
            if task.title.lower() in text:
                return task.title
        return None

    def _mock_recent_target(self) -> str | None:
        task = max(self.task_store.list(), key=lambda t: t.updated_at, default=None)
        return task.title if task else None

    def _mock_interval_seconds(self, text: str) -> int | None:
        import re

        match = re.search(r"every\s+(\d+)\s*(second|seconds|sec|secs|minute|minutes|min|mins)?", text)
        if not match:
            return None
        value = int(match.group(1))
        unit = match.group(2) or "seconds"
        return value * 60 if unit.startswith(("minute", "min")) else value

    def _mock_draft(self, message: str) -> TaskDraft:
        text = message.lower()
        if any(k in text for k in ("tap", "water", "faucet", "sink")):
            return TaskDraft(
                title="Watch kitchen tap",
                description=message,
                focus="Determine whether the kitchen tap is running unattended, and for how long.",
                interval_seconds=30,
                streams=[
                    StreamRequirement(stream_id="demo-kitchen-cam", kind="image"),
                    StreamRequirement(stream_id="demo-kitchen-mic", kind="audio"),
                ],
            )
        if any(k in text for k in ("fridge", "grocer", "milk", "eggs")):
            return TaskDraft(
                title="Check fridge inventory",
                description=message,
                focus="Check whether kitchen staples are running low.",
                interval_seconds=1800,
                streams=[StreamRequirement(stream_id="demo-kitchen-cam", kind="image")],
            )
        if any(k in text for k in ("pet", "cat", "dog")):
            return TaskDraft(
                title="Watch the pet",
                description=message,
                focus="Check on the pet's activity level and whether it has eaten.",
                interval_seconds=900,
                subject_label="pet",
                streams=[StreamRequirement(stream_id="demo-kitchen-cam", kind="image")],
            )
        known = self.stream_registry.known_ids()
        streams = [
            StreamRequirement(stream_id=known[0], kind=self.stream_registry.kind_of(known[0]))
        ] if known else []
        title = message.strip()[:40] or "New task"
        return TaskDraft(
            title=title,
            description=message,
            focus=message,
            interval_seconds=300,
            streams=streams,
        )
