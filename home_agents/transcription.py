"""Speech-to-text for the orchestrator's voice mode.

Voice mode is just another front-end to the *same* orchestrator: a spoken clip
is transcribed here into text, then that text is fed through the existing
``Orchestrator.handle_message`` path unchanged. Nothing downstream needs to
know whether the words were typed or spoken.

The provider is pluggable, mirroring the rest of the framework's mock/live
split. ``GradiumTranscriber`` calls Gradium's pre-recorded speech-to-text REST
endpoint (one HTTP POST of the raw audio bytes); ``MockTranscriber`` returns a
canned command so the whole voice flow is demoable offline with no API key,
exactly like ``HOME_AGENTS_MOCK`` mode everywhere else.
"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

import httpx

from .config import Settings


@runtime_checkable
class Transcriber(Protocol):
    provider: str

    def transcribe(self, audio: bytes, mime_type: str) -> str:
        """Return the recognized text for one audio clip."""


class MockTranscriber:
    """Offline stand-in: returns a fixed command so voice mode demos with no key.

    The canned phrase is the tap scenario the mock orchestrator already knows,
    so speaking anything at all drives the full voice -> transcript -> task loop
    without ever touching a network.
    """

    provider = "mock"

    def transcribe(self, audio: bytes, mime_type: str) -> str:
        return "watch the kitchen tap and tell me if it runs unattended"


class GradiumTranscriber:
    """Gradium pre-recorded speech-to-text (a single POST of the raw audio).

    See https://docs.gradium.ai/guides/speech-to-text-rest — the audio bytes go
    in the request body, the format is declared via ``Content-Type``, and the
    key rides in the ``x-api-key`` header. The response is newline-delimited
    JSON; ``_extract_transcript`` stitches the recognized segments back into one
    string.
    """

    provider = "gradium"
    endpoint = "https://api.gradium.ai/api/post/speech/asr"

    def __init__(
        self, api_key: str, language: str | None = None, timeout: float = 30.0
    ) -> None:
        self._api_key = api_key
        self._language = language
        self._timeout = timeout

    def transcribe(self, audio: bytes, mime_type: str) -> str:
        params: dict[str, str] = {}
        if self._language:
            params["json_config"] = json.dumps({"language": self._language})
        response = httpx.post(
            self.endpoint,
            params=params,
            headers={
                "x-api-key": self._api_key,
                "Content-Type": mime_type or "audio/wav",
            },
            content=audio,
            timeout=self._timeout,
        )
        response.raise_for_status()
        return _extract_transcript(response.text)


def _extract_transcript(ndjson: str) -> str:
    """Collapse Gradium's NDJSON stream into one transcript string.

    Each line is a JSON event with a ``type``. Finalized segments arrive as
    ``end_text``; ``text`` events are interim updates for the current segment.
    We prefer the finalized ``end_text`` segments and fall back to the last
    interim ``text`` if no segment was finalized (e.g. a very short clip). This
    is deliberately forgiving: malformed or unknown lines are skipped rather
    than raising, so a single odd frame can't sink the whole turn.
    """
    finals: list[str] = []
    last_interim = ""
    for line in ndjson.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        text = event.get("text") or ""
        if not text:
            continue
        if event.get("type") == "end_text":
            finals.append(text)
        elif event.get("type") == "text":
            last_interim = text
    transcript = " ".join(finals) if finals else last_interim
    return " ".join(transcript.split())


def get_transcriber(settings: Settings) -> Transcriber:
    """Pick the transcriber by whether a Gradium key is configured.

    A present key *is* the signal to use real speech-to-text, so it wins even
    in ``HOME_AGENTS_MOCK`` mode — that lets you test live voice input against
    an otherwise-mock system (real transcript -> mock keyword orchestrator), no
    Crusoe key required. With no key, the offline ``MockTranscriber`` keeps the
    voice button working, exactly like mock mode everywhere else.
    """
    if settings.gradium_api_key:
        return GradiumTranscriber(settings.gradium_api_key, settings.stt_language)
    return MockTranscriber()
