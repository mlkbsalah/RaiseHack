"""Optional Telegram bridge for the orchestrator and the approval queue.

Fully inert unless ``TELEGRAM_BOT_TOKEN`` is set: no thread starts, no
network call is ever made, so the rest of the app behaves identically with
or without Telegram configured. When enabled, it does three things:

1. Mirrors the orchestrator chat — any authorized chat can type the same
   free-text task descriptions the web UI's chat panel accepts.
2. Pushes every new ``ApprovalRequest`` as a message with inline
   Approve/Deny buttons, the moment an agent files one — this is the
   primary feature: permission requests reach you wherever you are, not
   just when the web tab happens to be open.
3. Exposes ``/tasks`` with inline Run now/Pause-Resume/Delete buttons per
   task, so the task list can be operated entirely from the chat.

Cross-notification is origin-aware in one direction only: a decision made
*in Telegram* never re-notifies Telegram (the button press already gives
that chat its own feedback by editing the message in place), but a decision
made *in the web UI* does get pushed to Telegram, per the requirement that
the two interfaces stay in sync. Task creation always notifies Telegram
regardless of which interface it came from, since duplicate confirmation of
your own action in the same chat is harmless and simpler than threading an
origin flag through the orchestrator.

Long polling (``getUpdates``) is used instead of a webhook: this runs
locally or behind a Cloudflare tunnel whose URL can change between runs, and
long polling needs no public endpoint at all, at the cost of one background
thread — the same tradeoff already made for the task ``Scheduler``.
"""

from __future__ import annotations

import threading
from typing import Any

import httpx

from .approvals import ApprovalStore
from .config import Settings
from .memory_store import MemoryStore
from .models import ApprovalRequest, SafetyAlert, TaskSpec
from .scheduler import Scheduler
from .task_store import TaskStore

HELP_TEXT = (
    "Home Agents bot.\n\n"
    "Send a plain-language message describing what you want watched, "
    "same as the web chat, e.g. \"watch the kitchen tap\".\n\n"
    "/tasks — list tasks with Run now / Pause / Delete buttons\n\n"
    "Agents will message you here automatically when they want to take "
    "an action, with Approve / Deny buttons."
)


def _esc(value: Any) -> str:
    """Escape text for Telegram's HTML parse mode."""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class TelegramBot:
    def __init__(
        self,
        settings: Settings,
        orchestrator: Any,
        task_store: TaskStore,
        approval_store: ApprovalStore,
        scheduler: Scheduler,
        memory_store: MemoryStore,
    ) -> None:
        self.enabled = bool(settings.telegram_bot_token)
        self.allowed_chat_ids = settings.telegram_allowed_chat_ids
        self.orchestrator = orchestrator
        self.task_store = task_store
        self.approval_store = approval_store
        self.scheduler = scheduler
        self.memory_store = memory_store

        self._base_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
        self._client = httpx.Client(timeout=httpx.Timeout(35.0, connect=10.0)) if self.enabled else None
        self._offset = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # approval_id -> [(chat_id, message_id), ...] so a resolution can edit
        # every copy of the request that was broadcast, not just the one acted on.
        self._approval_messages: dict[str, list[tuple[int, int]]] = {}

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------ outbound

    def _call(self, method: str, **params: Any) -> Any:
        if not self.enabled:
            return None
        try:
            response = self._client.post(f"{self._base_url}/{method}", json=params)
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                print(f"[telegram] {method} rejected: {payload}")
                return None
            return payload.get("result")
        except httpx.HTTPError as exc:
            print(f"[telegram] {method} failed: {exc}")
            return None

    def _broadcast(self, text: str, reply_markup: dict | None = None) -> list[tuple[int, int]]:
        sent = []
        for chat_id in self.allowed_chat_ids:
            result = self._call(
                "sendMessage", chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=reply_markup
            )
            if result:
                sent.append((chat_id, result["message_id"]))
        return sent

    def notify_task_created(self, task: TaskSpec) -> None:
        if not self.enabled:
            return
        streams = ", ".join(s.stream_id for s in task.streams) or "none"
        text = (
            f"\U0001f195 <b>New task:</b> {_esc(task.title)}\n"
            f"{_esc(task.focus)}\n"
            f"Every {task.interval_seconds}s · streams: {_esc(streams)}"
        )
        self._broadcast(text)

    def notify_approval_created(self, approval: ApprovalRequest) -> None:
        if not self.enabled:
            return
        text = (
            f"\U0001f514 <b>{_esc(approval.task_title)}</b> wants to act:\n"
            f"{_esc(approval.action)}\n"
            f"Reason: {_esc(approval.reason)} ({_esc(approval.risk)} risk)"
        )
        markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve", "callback_data": f"approve:{approval.approval_id}"},
                    {"text": "❌ Deny", "callback_data": f"deny:{approval.approval_id}"},
                ]
            ]
        }
        sent = self._broadcast(text, reply_markup=markup)
        if sent:
            self._approval_messages[approval.approval_id] = sent

    def notify_approval_resolved(self, approval: ApprovalRequest, origin: str) -> None:
        if not self.enabled or origin == "telegram":
            return  # that chat already saw its own button press resolve the message
        verb = "Approved ✅" if approval.status == "approved" else "Denied ❌"
        text = f"{verb} from the web interface: {_esc(approval.action)} ({_esc(approval.task_title)})"
        self._broadcast(text)

    def notify_safety_alert(self, alert: SafetyAlert) -> None:
        """Push from the hidden background safety monitor, not from any task.

        Deliberately louder than a normal approval ping and with no
        Approve/Deny buttons — this is a notification only, there is nothing
        to act on here, so it bypasses ApprovalStore entirely.
        """
        if not self.enabled:
            return
        siren = {"high": "🚨🚨🚨", "medium": "🚨⚠️", "low": "🚨"}.get(alert.urgency, "🚨")
        text = (
            f"{siren} <b>SAFETY ALERT</b> {siren}\n"
            f"<b>{_esc(alert.stream_name)}</b>: {_esc(alert.danger_type)}\n"
            f"{_esc(alert.description)}\n"
            f"Confidence: {alert.confidence:.0%} · urgency: {_esc(alert.urgency)}"
        )
        self._broadcast(text)

    # ------------------------------------------------------------- inbound

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            updates = self._call("getUpdates", offset=self._offset, timeout=25)
            if updates is None:
                self._stop.wait(5)
                continue
            for update in updates:
                self._offset = update["update_id"] + 1
                try:
                    self._handle_update(update)
                except Exception as exc:  # noqa: BLE001 - one bad update must not kill the loop
                    print(f"[telegram] update handling failed: {exc}")

    def _handle_update(self, update: dict) -> None:
        if "message" in update:
            self._handle_message(update["message"])
        elif "callback_query" in update:
            self._handle_callback(update["callback_query"])

    def _authorized(self, chat_id: int) -> bool:
        return chat_id in self.allowed_chat_ids

    def _deny_unauthorized(self, chat_id: int) -> None:
        self._call(
            "sendMessage",
            chat_id=chat_id,
            text=(
                f"Not authorized. Ask the admin to add {chat_id} to "
                "TELEGRAM_ALLOWED_CHAT_IDS in home_agents/.env and restart the app."
            ),
        )

    def _handle_message(self, message: dict) -> None:
        chat_id = message["chat"]["id"]
        text = (message.get("text") or "").strip()
        if not self._authorized(chat_id):
            self._deny_unauthorized(chat_id)
            return
        if text in ("/start", "/help"):
            self._call("sendMessage", chat_id=chat_id, text=HELP_TEXT)
            return
        if text == "/tasks":
            self._send_task_list(chat_id)
            return
        if not text:
            return
        try:
            reply = self.orchestrator.handle_message(text)
        except Exception as exc:  # noqa: BLE001 - report the failure instead of dropping it
            reply = f"Error: {exc}"
        self._call("sendMessage", chat_id=chat_id, text=reply)

    def _task_line(self, task: TaskSpec) -> str:
        return f"<b>{_esc(task.title)}</b> ({_esc(task.status)}, every {task.interval_seconds}s)"

    def _task_buttons(self, task: TaskSpec) -> dict:
        toggle = (
            {"text": "⏸ Pause", "callback_data": f"pause:{task.task_id}"}
            if task.status == "active"
            else {"text": "▶️ Resume", "callback_data": f"resume:{task.task_id}"}
        )
        return {
            "inline_keyboard": [
                [
                    {"text": "▶ Run now", "callback_data": f"run:{task.task_id}"},
                    toggle,
                    {"text": "\U0001f5d1 Delete", "callback_data": f"delete:{task.task_id}"},
                ]
            ]
        }

    def _send_task_list(self, chat_id: int) -> None:
        tasks = self.task_store.list()
        if not tasks:
            self._call("sendMessage", chat_id=chat_id, text="No tasks yet.")
            return
        for task in tasks:
            self._call(
                "sendMessage",
                chat_id=chat_id,
                text=self._task_line(task),
                parse_mode="HTML",
                reply_markup=self._task_buttons(task),
            )

    def _handle_callback(self, callback: dict) -> None:
        callback_id = callback["id"]
        chat_id = callback["message"]["chat"]["id"]
        message_id = callback["message"]["message_id"]
        if not self._authorized(chat_id):
            self._call("answerCallbackQuery", callback_query_id=callback_id, text="Not authorized")
            return
        action, _, target = callback.get("data", "").partition(":")
        if action in ("approve", "deny"):
            self._handle_decision(callback_id, target, action == "approve")
        elif action == "run":
            self._handle_run(callback_id, chat_id, message_id, target)
        elif action in ("pause", "resume"):
            self._handle_status_change(callback_id, chat_id, message_id, target, action)
        elif action == "delete":
            self._handle_delete(callback_id, chat_id, message_id, target)

    def _handle_decision(self, callback_id: str, approval_id: str, approve: bool) -> None:
        approval = self.approval_store.decide(approval_id, approve)
        if approval is None:
            self._call("answerCallbackQuery", callback_query_id=callback_id, text="Unknown approval")
            return
        self.memory_store.append_agent_log(
            approval.task_id,
            approval.task_title,
            f"User {'approved' if approve else 'denied'} proposed action via Telegram: {approval.action}",
        )
        verb = "Approved ✅" if approve else "Denied ❌"
        self._call("answerCallbackQuery", callback_query_id=callback_id, text=verb)
        text = f"{verb}: {_esc(approval.action)} ({_esc(approval.task_title)})"
        for msg_chat_id, msg_id in self._approval_messages.pop(approval.approval_id, []):
            self._call("editMessageText", chat_id=msg_chat_id, message_id=msg_id, text=text, parse_mode="HTML")

    def _handle_run(self, callback_id: str, chat_id: int, message_id: int, task_id: str) -> None:
        result = self.scheduler.run_task_now(task_id)
        task = self.task_store.get(task_id)
        if result is None or task is None:
            self._call("answerCallbackQuery", callback_query_id=callback_id, text="Task not found")
            return
        self._call("answerCallbackQuery", callback_query_id=callback_id, text="Ran now")
        flag = " ⚠️ anomaly" if result.observation.anomaly_detected else ""
        text = f"{self._task_line(task)}\n{_esc(result.observation.summary)}{flag}"
        self._call(
            "editMessageText",
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=self._task_buttons(task),
        )

    def _handle_status_change(
        self, callback_id: str, chat_id: int, message_id: int, task_id: str, action: str
    ) -> None:
        status = "active" if action == "resume" else "paused"
        task = self.task_store.set_status(task_id, status)
        if task is None:
            self._call("answerCallbackQuery", callback_query_id=callback_id, text="Task not found")
            return
        self._call("answerCallbackQuery", callback_query_id=callback_id, text=status.capitalize())
        self._call(
            "editMessageText",
            chat_id=chat_id,
            message_id=message_id,
            text=self._task_line(task),
            parse_mode="HTML",
            reply_markup=self._task_buttons(task),
        )

    def _handle_delete(self, callback_id: str, chat_id: int, message_id: int, task_id: str) -> None:
        task = self.task_store.get(task_id)
        title = task.title if task else task_id
        deleted = self.task_store.delete(task_id)
        self._call(
            "answerCallbackQuery",
            callback_query_id=callback_id,
            text="Deleted" if deleted else "Already gone",
        )
        self._call(
            "editMessageText",
            chat_id=chat_id,
            message_id=message_id,
            text=f"\U0001f5d1 Deleted: {_esc(title)}",
            reply_markup={"inline_keyboard": []},
        )
