"""Thin wrapper around Crusoe's OpenAI-compatible endpoint.

Pattern (client setup, JSON-only structured responses, data-url encoding,
mock-mode fallback) is carried over from ``tap_agent/specialists.py``, the
single hard-coded reference agent this framework generalizes.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .config import Settings, get_settings


T = TypeVar("T", bound=BaseModel)

IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
AUDIO_MIME_TYPES = {"audio/wav", "audio/mpeg", "audio/webm", "audio/ogg"}


def data_url_from_bytes(data: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def data_url_from_path(path: str, allowed: set[str]) -> str:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Input file does not exist: {path}")
    mime_type, _ = mimetypes.guess_type(file_path.name)
    suffix = file_path.suffix.lower()
    if suffix == ".wav":
        mime_type = "audio/wav"
    elif suffix == ".mp3":
        mime_type = "audio/mpeg"
    if mime_type is None:
        mime_type = "application/octet-stream"
    if mime_type not in allowed:
        raise ValueError(f"Unsupported file type {mime_type!r}; expected one of {sorted(allowed)}")
    return data_url_from_bytes(file_path.read_bytes(), mime_type)


def schema_instruction(model_name: str, fields: dict[str, str]) -> str:
    lines = [
        "Return exactly one JSON object.",
        "Do not include markdown, extra keys, or prose outside JSON.",
        f"The JSON object must match this {model_name} schema:",
    ]
    lines.extend(f'- "{key}": {description}' for key, description in fields.items())
    return "\n".join(lines)


def validate_json(raw: str, model: type[T]) -> T:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model returned invalid JSON: {exc.msg}") from exc
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"Model JSON failed validation against {model.__name__}: {exc}") from exc


def _disable_thinking_body(model: str) -> dict[str, Any]:
    lower = model.lower()
    if "nemotron" in lower:
        return {"chat_template_kwargs": {"enable_thinking": False}}
    if "kimi" in lower or "deepseek" in lower:
        return {"chat_template_kwargs": {"thinking": False}}
    return {}


class LLMClient:
    """One client, shared by the orchestrator and every task agent.

    Every scheduled task run and every chat turn is a single stateless API
    call through this client — no SDK-level agent loop, no hidden state
    between calls. State lives in the memory files (see ``memory_store.py``),
    not in this object.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.client: Any | None = None
        if not self.settings.mock_mode:
            try:
                from openai import OpenAI
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "The openai package is required for live Crusoe calls. "
                    "Install home_agents/requirements.txt or set HOME_AGENTS_MOCK=true."
                ) from exc
            api_key = self.settings.crusoe_api_key or os.environ.get("CRUSOE_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "CRUSOE_API_KEY is required unless HOME_AGENTS_MOCK=true is set."
                )
            self.client = OpenAI(base_url=self.settings.crusoe_base_url, api_key=api_key)

    def chat_json(self, *, model: str, messages: list[dict[str, Any]]) -> str:
        if self.client is None:
            raise RuntimeError("chat_json was called while running in mock mode.")
        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
            extra_body=_disable_thinking_body(model),
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("Model returned an empty response.")
        return content
