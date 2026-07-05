"""Markdown-backed memory: one file per agent (task) and one per subject.

Matches the architecture directly: each agent has its own memory file it
reads before a run and appends to after; each subject (person or pet) has a
memory file that any agent can add findings to. Plain append-only markdown
is used instead of a database because the only access pattern is "read the
recent tail for context" and "append a new entry" — a database would be an
abstraction with no payoff here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .debug_log import DebugLog

TAIL_CHARS = 4000


def _slugify(label: str) -> str:
    slug = "".join(c.lower() if c.isalnum() else "-" for c in label).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "subject"


class MemoryStore:
    def __init__(self, settings: Settings, debug: DebugLog | None = None) -> None:
        self._agents_dir = settings.data_dir / "memory" / "agents"
        self._subjects_dir = settings.data_dir / "memory" / "subjects"
        self._agents_dir.mkdir(parents=True, exist_ok=True)
        self._subjects_dir.mkdir(parents=True, exist_ok=True)
        self._debug = debug or DebugLog(enabled=False)

    def _agent_path(self, task_id: str) -> Path:
        return self._agents_dir / f"{task_id}.md"

    def _subject_path(self, subject_id: str) -> Path:
        return self._subjects_dir / f"{subject_id}.md"

    def read_agent_memory(self, task_id: str, task_title: str) -> str:
        path = self._agent_path(task_id)
        if not path.exists():
            path.write_text(f"# Agent memory: {task_title}\n\n", encoding="utf-8")
        text = path.read_text(encoding="utf-8")
        return text[-TAIL_CHARS:]

    def append_agent_log(self, task_id: str, task_title: str, entry: str) -> None:
        path = self._agent_path(task_id)
        self.read_agent_memory(task_id, task_title)  # ensure header exists
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"- **{stamp}** — {entry}\n")
        self._debug.emit(
            "memory",
            f"agent memory ← {task_title}",
            f"{path.name}: {entry}",
        )

    def run_count(self, task_id: str) -> int:
        path = self._agent_path(task_id)
        if not path.exists():
            return 0
        return path.read_text(encoding="utf-8").count("\n- **")

    def resolve_subject_id(self, label: str, existing: str | None) -> str:
        return existing or _slugify(label)

    def read_subject_memory(self, subject_id: str) -> str:
        path = self._subject_path(subject_id)
        if not path.exists():
            path.write_text(f"# Subject memory: {subject_id}\n\n## Findings\n\n", encoding="utf-8")
        text = path.read_text(encoding="utf-8")
        return text[-TAIL_CHARS:]

    def append_subject_findings(
        self, subject_id: str, subject_label: str, task_title: str, findings: list[str]
    ) -> None:
        if not findings:
            return
        path = self._subject_path(subject_id)
        if not path.exists():
            path.write_text(
                f"# Subject memory: {subject_label}\n\n## Findings\n\n", encoding="utf-8"
            )
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        with path.open("a", encoding="utf-8") as fh:
            for finding in findings:
                fh.write(f"- **{stamp}** (from *{task_title}*) — {finding}\n")
        self._debug.emit(
            "memory",
            f"subject memory ← {subject_id} ({len(findings)} finding{'s' if len(findings) != 1 else ''})",
            f"{path.name} (from {task_title}):\n" + "\n".join(f"- {f}" for f in findings),
        )

    def list_subjects(self) -> list[str]:
        return sorted(p.stem for p in self._subjects_dir.glob("*.md"))
