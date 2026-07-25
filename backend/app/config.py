"""Settings, loaded from .env. Every variable has a working default — none are required.

HIVE runs with no keys, no camera, and no phones (docs/CONTRACTS.md §5.3).
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Absolute, not ".env": the Makefile launches uvicorn from backend/, so a
# relative path here would silently miss the repo-root .env.
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # ── Models: NVIDIA NIM (OpenAI-compatible). One key for everything. ──
    nvidia_api_key: str | None = None
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    planner_model: str = "nvidia/nemotron-3-super-120b-a12b"
    vlm_fast_model: str = "nvidia/nemotron-nano-12b-v2-vl"
    vlm_reason_model: str = "nvidia/cosmos3-nano-reasoner"
    vlm_enabled: bool = True
    vlm_fast_hz: float = 1.4

    # ── Runtime ──
    demo_mode: bool = True
    world_mode: str = "simulation"
    camera_index: int = 0
    tick_hz: float = 4.0
    verification_threshold: float = 0.70
    port: int = 8000

    # ── Planner timeouts. A judge will not wait longer, and neither will you. ──
    planner_timeout_seconds: float = 12.0
    replan_timeout_seconds: float = 8.0
    grounding_timeout_seconds: float = 8.0

    # ── Voygr Callwright voice escalation ──
    callwright_api_key: str | None = None
    callwright_base_url: str = "https://api.voygr.tech"
    escalation_phone: str | None = None


settings = Settings()
