"""Registry of live data streams (phone cameras, mics, demo files).

A "stream" is just a named source of the latest image or audio blob.
Browser clients push frames/clips over HTTP as they capture them; demo
streams are seeded once at startup from static files under ``data/`` so the
tap-water scenario works with zero hardware connected.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import time

from .config import Settings
from .llm_client import AUDIO_MIME_TYPES, IMAGE_MIME_TYPES, data_url_from_bytes
from .models import StreamKind


@dataclass
class StreamState:
    stream_id: str
    kind: StreamKind
    mime_type: str
    data: bytes
    updated_at: float
    source: str  # "live" or "demo"


@dataclass
class StreamPayload:
    stream_id: str
    kind: StreamKind
    connected: bool
    data_url: str | None = None
    age_seconds: float | None = None


class StreamRegistry:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._streams: dict[str, StreamState] = {}
        self._dir = settings.data_dir / "streams"
        self._dir.mkdir(parents=True, exist_ok=True)

    def seed_demo_streams(self, repo_data_dir: Path) -> None:
        """Register the tap-water demo media as default streams."""
        seeds = [
            ("demo-kitchen-cam", "image", repo_data_dir / "running-tap-person.jpeg"),
            ("demo-kitchen-mic", "audio", repo_data_dir / "running-tap.wav"),
        ]
        for stream_id, kind, path in seeds:
            if not path.exists() or stream_id in self._streams:
                continue
            mime = "image/jpeg" if kind == "image" else "audio/wav"
            self._streams[stream_id] = StreamState(
                stream_id=stream_id,
                kind=kind,
                mime_type=mime,
                data=path.read_bytes(),
                updated_at=time(),
                source="demo",
            )

    def put(self, stream_id: str, kind: StreamKind, mime_type: str, data: bytes) -> None:
        allowed = IMAGE_MIME_TYPES if kind == "image" else AUDIO_MIME_TYPES
        if mime_type not in allowed:
            raise ValueError(f"Unsupported {kind} mime type {mime_type!r}")
        self._streams[stream_id] = StreamState(
            stream_id=stream_id,
            kind=kind,
            mime_type=mime_type,
            data=data,
            updated_at=time(),
            source="live",
        )
        suffix = mime_type.split("/")[-1].split(";")[0]
        out_dir = self._dir / stream_id
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"latest.{suffix}").write_bytes(data)

    def get_payload(self, stream_id: str, kind: StreamKind) -> StreamPayload:
        state = self._streams.get(stream_id)
        if state is None:
            return StreamPayload(stream_id=stream_id, kind=kind, connected=False)
        return StreamPayload(
            stream_id=stream_id,
            kind=kind,
            connected=True,
            data_url=data_url_from_bytes(state.data, state.mime_type),
            age_seconds=max(0.0, time() - state.updated_at),
        )

    def list_streams(self) -> list[dict]:
        return [
            {
                "stream_id": s.stream_id,
                "kind": s.kind,
                "source": s.source,
                "age_seconds": round(time() - s.updated_at, 1),
            }
            for s in sorted(self._streams.values(), key=lambda s: s.stream_id)
        ]

    def known_ids(self) -> list[str]:
        return sorted(self._streams.keys())

    def kind_of(self, stream_id: str) -> StreamKind:
        return self._streams[stream_id].kind
