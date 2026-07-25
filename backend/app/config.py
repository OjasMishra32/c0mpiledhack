from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(REPO_ROOT / ".env"), extra="ignore")

    nvidia_api_key: str = ""
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"

    vlm_fast_model: str = "nvidia/nemotron-nano-12b-v2-vl"
    vlm_reason_model: str = "nvidia/cosmos3-nano-reasoner"
    planner_model: str = "nvidia/nemotron-3-super-120b-a12b"
    vlm_enabled: bool = True
    vlm_fast_hz: float = 1.4

    demo_mode: bool = True
    world_mode: str = "simulation"  # live | assisted | simulation
    camera_index: int = 0
    tick_hz: float = 4.0
    verification_threshold: float = 0.70
    port: int = 8000

    callwright_api_key: str = ""
    callwright_base_url: str = "https://api.voygr.tech"
    escalation_phone: str = ""


settings = Settings()
