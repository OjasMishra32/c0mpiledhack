"""HIVE configuration. Every setting has a working default; none are required."""

from __future__ import annotations

import socket
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    # ── models: NVIDIA NIM (OpenAI-compatible). One provider for everything. ─
    # Supply EITHER a single key or several comma/newline-separated keys. Multiple keys
    # are pooled: per-key rate windows, sticky affinity for warm prompt caches, and
    # automatic failover with exponential cooldown on 429.
    nvidia_api_key: str | None = None
    nvidia_api_keys: str | None = None
    nim_per_key_rpm: int = 38  # just under a typical free-tier 40 RPM
    nim_key_strategy: str = "sticky_least_loaded"  # or "round_robin"
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"

    # Model routing. Perception is EVENT-TRIGGERED, never a per-frame poll:
    #   OpenCV holds the world state continuously; the reasoner is invoked only when
    #   something meaningful happens (deviation candidate, low tracker confidence,
    #   timeout, failed predicate, operator question).
    vlm_reason_model: str = "nvidia/cosmos3-nano-reasoner"  # primary physical reasoner
    vlm_fallback_model: str = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"  # multimodal fallback
    planner_model: str = "nvidia/nemotron-3-super-120b-a12b"  # fast tool-calling compiler
    planner_fallbacks: str = "z-ai/glm-5.2,qwen/qwen3-next-80b-a3b-instruct"
    # Measured on these endpoints: the template compiler is instant and correct, while the
    # hosted planners take 20s+ on a full prompt. A live compile must feel immediate, so the
    # LLM gets a short window and we fall through to the template library without drama.
    planner_timeout: float = 8.0
    vlm_enabled: bool = True
    vlm_reason_timeout: float = 6.0
    vlm_fallback_timeout: float = 6.0
    vlm_min_interval: float = 2.0  # floor between reasoning bursts; protects the free tier
    vlm_burst_frames: int = 5  # 4–8 frames covering the last ~2s
    vlm_burst_fps: float = 2.0
    frame_buffer_seconds: float = 3.0

    # ── runtime ─────────────────────────────────────────────────────────────
    demo_mode: bool = True
    world_mode: str = "simulation"
    camera_index: int = 0
    tick_hz: float = 4.0
    default_timeout_seconds: int = 25
    demo_timeout_seconds: int = 16
    verification_threshold: float = 0.70
    port: int = 8000
    frontend_port: int = 5173

    # ── Voygr Callwright voice escalation ───────────────────────────────────
    callwright_api_key: str = "pk_live_7f130e9d22a7480b8816ec0033cf4de7"
    callwright_base_url: str = "https://api.voygr.tech"
    escalation_phone: str | None = None

    @property
    def action_timeout(self) -> int:
        return self.demo_timeout_seconds if self.demo_mode else self.default_timeout_seconds

    @property
    def nim_keys(self) -> list[str]:
        """All configured keys, de-duped, order preserved."""
        raw: list[str] = []
        if self.nvidia_api_keys:
            raw += [k for chunk in self.nvidia_api_keys.splitlines() for k in chunk.split(",")]
        if self.nvidia_api_key:
            raw.append(self.nvidia_api_key)
        out: list[str] = []
        for k in raw:
            k = k.strip()
            if k and k not in out:
                out.append(k)
        return out

    @property
    def has_model_access(self) -> bool:
        return bool(self.nim_keys)


settings = Settings()


def lan_ip() -> str:
    """Best-effort local network IP. Does not actually send a packet."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no traffic; just picks the default route
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def join_url() -> str:
    return f"http://{lan_ip()}:{settings.frontend_port}/join"
