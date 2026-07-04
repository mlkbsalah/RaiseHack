"""Task persistence: Tasks.md is the human-readable ledger the orchestrator
writes to; tasks.json next to it is the machine-readable index it is
regenerated from on every change. Parsing hand-edited markdown back into
structured fields is a reliability risk with no upside here, since the
JSON is the source of truth and Tasks.md is purely a rendering of it.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from time import time

from .config import Settings
from .models import TaskSpec


class TaskStore:
    def __init__(self, settings: Settings) -> None:
        self._dir = settings.data_dir / "tasks"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._json_path = self._dir / "tasks.json"
        self._md_path = self._dir / "Tasks.md"
        self._lock = threading.Lock()
        self._tasks: dict[str, TaskSpec] = {}
        self._load()

    def _load(self) -> None:
        if self._json_path.exists():
            raw = json.loads(self._json_path.read_text(encoding="utf-8"))
            self._tasks = {item["task_id"]: TaskSpec.model_validate(item) for item in raw}
        self._render_markdown()

    def _save(self) -> None:
        payload = [task.as_dict() for task in self._tasks.values()]
        self._json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self._render_markdown()

    def _render_markdown(self) -> None:
        lines = ["# Tasks", "", "Generated automatically by the orchestrator. Do not hand-edit.", ""]
        if not self._tasks:
            lines.append("_No tasks yet — describe one in the chat._")
        for task in sorted(self._tasks.values(), key=lambda t: t.created_at):
            lines.append(f"## {task.title} (`{task.task_id}`, {task.status})")
            lines.append(f"- description: {task.description}")
            lines.append(f"- focus: {task.focus}")
            lines.append(f"- interval_seconds: {task.interval_seconds}")
            lines.append(f"- subject_id: {task.subject_id or '—'}")
            stream_list = ", ".join(f"{s.stream_id} ({s.kind})" for s in task.streams) or "—"
            lines.append(f"- streams: {stream_list}")
            lines.append(f"- requires_approval: {task.requires_approval}")
            lines.append("")
        self._md_path.write_text("\n".join(lines), encoding="utf-8")

    def add(self, task: TaskSpec) -> TaskSpec:
        with self._lock:
            self._tasks[task.task_id] = task
            self._save()
        return task

    def get(self, task_id: str) -> TaskSpec | None:
        return self._tasks.get(task_id)

    def find_by_title(self, title_fragment: str) -> TaskSpec | None:
        fragment = title_fragment.strip().lower()
        for task in self._tasks.values():
            if fragment == task.task_id.lower() or fragment in task.title.lower():
                return task
        return None

    def list(self) -> list[TaskSpec]:
        return sorted(self._tasks.values(), key=lambda t: t.created_at)

    def update(self, task: TaskSpec) -> None:
        with self._lock:
            task.updated_at = time()
            self._tasks[task.task_id] = task
            self._save()

    def set_status(self, task_id: str, status: str) -> TaskSpec | None:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task.status = status  # type: ignore[assignment]
        self.update(task)
        return task

    def delete(self, task_id: str) -> bool:
        with self._lock:
            if task_id not in self._tasks:
                return False
            del self._tasks[task_id]
            self._save()
            return True

    def mark_ran(self, task_id: str, when: float) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        task.last_run_at = when
        self.update(task)
