"""In-memory ring buffer of debug events for the debug panel.

Only active when the app runs in debug mode (``HOME_AGENTS_DEBUG=true``).
The orchestrator, the memory store, and each task agent ``emit`` a short,
human-readable event whenever they decide something or write to memory; the
UI polls ``recent`` and streams the tail into a collapsible panel so you can
watch memories being populated and the orchestrator reasoning in real time.

A single shared instance is injected into every component that produces
events, mirroring how the rest of the framework is wired in ``app.py``. When
debug mode is off ``emit`` is a cheap no-op, so instrumentation can stay in
the hot paths without a runtime cost.
"""

from __future__ import annotations

import threading
from collections import deque
from time import time
from typing import Any

CAPACITY = 300


class DebugLog:
    def __init__(self, enabled: bool, capacity: int = CAPACITY) -> None:
        self._enabled = enabled
        self._events: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._seq = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    def emit(self, category: str, summary: str, detail: str | None = None) -> None:
        """Record one event. Cheap no-op unless debug mode is on.

        ``category`` groups events for colour-coding in the UI (e.g.
        ``orchestrator``, ``memory``, ``agent``); ``summary`` is the one-line
        headline; ``detail`` is optional multi-line context shown on expand.
        """
        if not self._enabled:
            return
        with self._lock:
            self._seq += 1
            self._events.append(
                {
                    "seq": self._seq,
                    "at": time(),
                    "category": category,
                    "summary": summary,
                    "detail": detail,
                }
            )

    def recent(self, after: int = 0) -> list[dict[str, Any]]:
        """Events with ``seq`` greater than ``after`` (0 returns everything held)."""
        with self._lock:
            return [event for event in self._events if event["seq"] > after]
