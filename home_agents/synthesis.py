"""Text-to-speech for voice mode's reply half (optional, Gradium-powered).

The orchestrator's text reply can be read back aloud. By default the browser's
built-in ``speechSynthesis`` handles that with no key or network; when a Gradium
key *and* a ``voice_id`` are configured, the frontend instead pulls audio from
``POST /api/tts`` so both halves of the conversation share one voice vendor.

This mirrors ``transcription.py`` on the way out: one small pluggable class, one
HTTP call, and a ``None`` when it isn't configured so the caller can fall back.
"""

from __future__ import annotations

import httpx

from .config import Settings


class GradiumSynthesizer:
    """Gradium REST text-to-speech (one POST, raw audio back).

    See https://docs.gradium.ai/guides/text-to-speech-rest — with
    ``only_audio: true`` the response body *is* the audio in ``output_format``,
    so we hand the bytes and their content-type straight to the browser to play.
    """

    endpoint = "https://api.gradium.ai/api/post/speech/tts"

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        output_format: str = "wav",
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._voice_id = voice_id
        self._output_format = output_format
        self._timeout = timeout

    def synthesize(self, text: str) -> tuple[bytes, str]:
        response = httpx.post(
            self.endpoint,
            headers={"x-api-key": self._api_key, "Content-Type": "application/json"},
            json={
                "text": text,
                "voice_id": self._voice_id,
                "output_format": self._output_format,
                "only_audio": True,
            },
            timeout=self._timeout,
        )
        if response.status_code >= 400:
            # Surface Gradium's own error text (bad voice id, auth, quota, …)
            # rather than a generic "HTTP 4xx", so the failure is diagnosable.
            raise RuntimeError(
                f"Gradium TTS returned {response.status_code}: {response.text[:300]}"
            )
        mime_type = response.headers.get("content-type", f"audio/{self._output_format}")
        return response.content, mime_type


def get_synthesizer(settings: Settings) -> GradiumSynthesizer | None:
    """A Gradium synthesizer when a key *and* a voice are set, else ``None``.

    Reply speech is an optional upgrade: with no voice configured the frontend
    keeps using the browser's built-in voice, so returning ``None`` here is the
    normal, fully-working default rather than an error.
    """
    if settings.gradium_api_key and settings.gradium_voice_id:
        return GradiumSynthesizer(settings.gradium_api_key, settings.gradium_voice_id)
    return None
