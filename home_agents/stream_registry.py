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

# Suffixes a live camera+voice stream card posts under, one per modality.
CAMERA_SUFFIX = "-cam"
MIC_SUFFIX = "-mic"


def _base_name(stream_id: str) -> str:
    """Strip a ``-cam``/``-mic`` modality suffix to recover the stream name."""
    for suffix in (CAMERA_SUFFIX, MIC_SUFFIX):
        if stream_id.endswith(suffix):
            return stream_id[: -len(suffix)]
    return stream_id


def _normalize_mime_type(mime_type: str) -> str:
    """Drop browser codec parameters before validating/storing a MIME type."""
    return mime_type.split(";", 1)[0].strip().lower()


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
        mime_type = _normalize_mime_type(mime_type)
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

    def _is_fresh(self, state: StreamState) -> bool:
        """A live stream is 'connected' only while it keeps receiving data.

        Demo/seeded streams are exempt (they are pushed once on purpose); a
        live stream that has gone quiet past the TTL is treated as closed, so
        stopped phones drop out instead of freezing on their last frame.
        """
        if state.source != "live":
            return True
        return (time() - state.updated_at) <= self._settings.stream_ttl_seconds

    def remove(self, stream_id: str) -> bool:
        """Drop a stream immediately (e.g. when a card is stopped in the UI)."""
        return self._streams.pop(stream_id, None) is not None

    def get_payload(self, stream_id: str, kind: StreamKind) -> StreamPayload:
        state = self._streams.get(stream_id)
        if state is None or not self._is_fresh(state):
            return StreamPayload(stream_id=stream_id, kind=kind, connected=False)
        return StreamPayload(
            stream_id=stream_id,
            kind=kind,
            connected=True,
            data_url=data_url_from_bytes(state.data, state.mime_type),
            age_seconds=max(0.0, time() - state.updated_at),
        )

    def get_latest_bytes(self, stream_id: str) -> tuple[bytes, str] | None:
        """Raw latest blob + mime for a stream, for serving into an <img>/<audio>."""
        state = self._streams.get(stream_id)
        if state is None:
            return None
        return state.data, state.mime_type

    def list_streams(self) -> list[dict]:
        return [
            {
                "stream_id": s.stream_id,
                "kind": s.kind,
                "source": s.source,
                "age_seconds": round(time() - s.updated_at, 1),
            }
            for s in sorted(self._streams.values(), key=lambda s: s.stream_id)
            if self._is_fresh(s)
        ]

    def list_pairs(self) -> list[dict]:
        """Group streams into camera+voice pairs by base name.

        A live stream card posts its frames to ``<name>-cam`` and its audio
        clips to ``<name>-mic``; grouping them back together here lets the UI
        and the orchestrator treat one place (kitchen, front door) as a single
        camera+voice stream instead of two unrelated blobs. A stream whose id
        carries neither suffix stands alone as its own single-member pair.
        """
        now = time()
        groups: dict[str, dict] = {}
        for s in sorted(self._streams.values(), key=lambda s: s.stream_id):
            if not self._is_fresh(s):
                continue
            base = _base_name(s.stream_id)
            group = groups.setdefault(
                base, {"name": base, "source": s.source, "camera": None, "mic": None}
            )
            member = {"stream_id": s.stream_id, "age_seconds": round(now - s.updated_at, 1)}
            group["camera" if s.kind == "image" else "mic"] = member
        return [groups[name] for name in sorted(groups)]

    def known_ids(self) -> list[str]:
        return sorted(self._streams.keys())

    def kind_of(self, stream_id: str) -> StreamKind:
        return self._streams[stream_id].kind
