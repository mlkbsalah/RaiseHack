"""Configuration helpers for the tap agent."""

from __future__ import annotations

import os
from dataclasses import dataclass

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
    sqlite_path: str


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def get_settings() -> Settings:
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
        mock_mode=_truthy(os.environ.get("TAP_AGENT_MOCK")),
        sqlite_path=os.environ.get("TAP_AGENT_DB", "tap_agent.sqlite3"),
    )
