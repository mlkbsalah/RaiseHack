"""Persistent session log for the in-app agent console.

The orchestrator, memory store, and task agents emit short, human-readable
events whenever they decide something or write to memory. Events are kept in
an in-memory ring buffer for quick polling and also appended to a JSONL file
per server session so a run can be inspected after the browser closes.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
import json
from pathlib import Path
from time import time
from typing import Any

CAPACITY = 300


class DebugLog:
    def __init__(self, data_dir: Path | None = None, capacity: int = CAPACITY) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._seq = 0
        self._path: Path | None = None
        if data_dir is not None:
            sessions_dir = data_dir / "debug_sessions"
            sessions_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            self._path = sessions_dir / f"{stamp}.jsonl"

    @property
    def enabled(self) -> bool:
        return True

    @property
    def path(self) -> str | None:
        return str(self._path) if self._path else None

    def emit(self, category: str, summary: str, detail: str | None = None) -> None:
        """Record one privacy-safer, human-readable event.

        ``category`` groups events for colour-coding in the UI (e.g.
        ``orchestrator``, ``memory``, ``agent``); ``summary`` is the one-line
        headline; ``detail`` is optional multi-line context.
        """
        with self._lock:
            self._seq += 1
            event = {
                "seq": self._seq,
                "at": time(),
                "category": category,
                "summary": summary,
                "detail": detail,
            }
            self._events.append(event)
            if self._path is not None:
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def recent(self, after: int = 0) -> list[dict[str, Any]]:
        """Events with ``seq`` greater than ``after`` (0 returns everything held)."""
        with self._lock:
            return [event for event in self._events if event["seq"] > after]
