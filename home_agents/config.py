"""Configuration for the home-agents framework."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv() -> bool:
        return False


load_dotenv()


@dataclass(frozen=True)
class Settings:
    crusoe_api_key: str | None
    crusoe_base_url: str
    multimodal_model: str
    reasoning_model: str
    mock_mode: bool
    debug: bool
    data_dir: Path
    tick_seconds: float
    stream_ttl_seconds: float
    host: str
    port: int


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def get_settings() -> Settings:
    data_dir = Path(os.environ.get("HOME_AGENTS_DATA_DIR", "home_agents_data")).resolve()
    return Settings(
        crusoe_api_key=os.environ.get("CRUSOE_API_KEY"),
        crusoe_base_url=os.environ.get(
            "CRUSOE_BASE_URL", "https://api.inference.crusoecloud.com/v1/"
        ),
        multimodal_model=os.environ.get(
            "CRUSOE_MULTIMODAL_MODEL",
            "nvidia/Nemotron-3-Nano-Omni-Reasoning-30B-A3B",
        ),
        reasoning_model=os.environ.get(
            "CRUSOE_REASONING_MODEL", "deepseek-ai/Deepseek-V4-Flash"
        ),
        mock_mode=_truthy(os.environ.get("HOME_AGENTS_MOCK")),
        debug=_truthy(os.environ.get("HOME_AGENTS_DEBUG")),
        data_dir=data_dir,
        tick_seconds=float(os.environ.get("HOME_AGENTS_TICK_SECONDS", "5")),
        stream_ttl_seconds=float(os.environ.get("HOME_AGENTS_STREAM_TTL", "12")),
        host=os.environ.get("HOME_AGENTS_HOST", "127.0.0.1"),
        port=int(os.environ.get("HOME_AGENTS_PORT", "8000")),
    )
