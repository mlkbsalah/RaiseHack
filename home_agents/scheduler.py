"""Deterministic scheduling loop.

This is ordinary Python, not an autonomous agent: it just checks, every
``tick_seconds``, which active tasks are due (now - last_run_at >=
interval_seconds) and calls the agent for each one in turn. The intelligence
lives entirely inside ``TaskAgent.run`` for that one call; the scheduler
itself never reasons about anything.
"""

from __future__ import annotations

import threading
from time import time

from .agent_runner import TaskAgent
from .models import AgentRunResult
from .task_store import TaskStore


class LatestResults:
    """Most recent run result per task, kept in memory for the UI to poll."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._results: dict[str, AgentRunResult] = {}

    def set(self, result: AgentRunResult) -> None:
        with self._lock:
            self._results[result.task_id] = result

    def get(self, task_id: str) -> AgentRunResult | None:
        return self._results.get(task_id)


class Scheduler:
    def __init__(
        self,
        task_store: TaskStore,
        agent: TaskAgent,
        results: LatestResults,
        tick_seconds: float,
    ) -> None:
        self.task_store = task_store
        self.agent = agent
        self.results = results
        self.tick_seconds = tick_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def run_task_now(self, task_id: str) -> AgentRunResult | None:
        task = self.task_store.get(task_id)
        if task is None:
            return None
        result = self.agent.run(task)
        self.task_store.mark_ran(task.task_id, result.ran_at)
        self.results.set(result)
        return result

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._tick()
            self._stop.wait(self.tick_seconds)

    def _tick(self) -> None:
        now = time()
        for task in self.task_store.list():
            if task.status != "active":
                continue
            due = task.last_run_at is None or (now - task.last_run_at) >= task.interval_seconds
            if not due:
                continue
            try:
                result = self.agent.run(task)
            except Exception as exc:  # noqa: BLE001 - a single bad run must not kill the loop
                self.task_store.mark_ran(task.task_id, now)
                print(f"[scheduler] task {task.task_id} ({task.title}) failed: {exc}")
                continue
            self.task_store.mark_ran(task.task_id, result.ran_at)
            self.results.set(result)
