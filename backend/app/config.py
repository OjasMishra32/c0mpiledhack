from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    nvidia_api_key: str | None = None
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    demo_mode: bool = True
    world_mode: str = "simulation"
    camera_index: int = 0
    tick_hz: float = 4.0
    default_timeout_seconds: int = 25
    demo_timeout_seconds: int = 14
    verification_threshold: float = 0.70

    vlm_fast_model: str = "nvidia/nemotron-nano-12b-v2-vl"
    vlm_reason_model: str = "nvidia/cosmos3-nano-reasoner"
    planner_model: str = "nvidia/nemotron-3-super-120b-a12b"
    vlm_fast_hz: float = 1.4
    vlm_enabled: bool = True

    callwright_api_key: str | None = None
    callwright_base_url: str = "https://api.voygr.tech"
    escalation_phone: str | None = None

    port: int = 8000

    model_config = SettingsConfigDict(env_file=".env", env_prefix="")


settings = Settings()


def lan_ip() -> str:
    """Best-effort local network IP. Does not actually send a packet."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()
