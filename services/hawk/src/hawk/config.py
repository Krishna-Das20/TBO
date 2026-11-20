"""Settings from the environment (and a local .env in development)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv() -> None:
    for p in (Path.cwd() / ".env", Path(__file__).resolve().parents[4] / ".env"):
        if p.is_file():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
            return


_load_dotenv()


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


def _limits(raw: str) -> dict[str, tuple[int, int]]:
    """'model=rpm/rpd,model2=rpm/rpd' -> {model: (rpm, rpd)}"""
    out = {}
    for item in raw.split(","):
        if "=" in item and "/" in item:
            m, lim = item.split("=", 1)
            rpm, rpd = lim.split("/", 1)
            out[m.strip()] = (int(rpm), int(rpd))
    return out


@dataclass
class Settings:
    gemini_keys: list[str] = field(default_factory=lambda: _list("GEMINI_API_KEYS") or _list("GEMINI_API_KEY"))
    models: list[str] = field(default_factory=lambda: _list("HAWK_MODELS", "gemini-3.8-flash,gemini-3.5-flash-lite"))
    # Client-side pacing that matches the project's free-tier limits (check AI Studio for yours).
    model_limits: dict[str, tuple[int, int]] = field(default_factory=lambda: _limits(
        os.environ.get("HAWK_MODEL_LIMITS", "gemini-3.8-flash=5/20,gemini-3.5-flash-lite=15/500")))
    vertex_project: str = os.environ.get("HAWK_VERTEX_PROJECT", "")
    vertex_location: str = os.environ.get("HAWK_VERTEX_LOCATION", "us-central1")
    gateway_url: str = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8088")
    gateway_key: str = os.environ.get("GATEWAY_AGENT_KEY", "dev-only-agent-key")
    gateway_admin: str = os.environ.get("GATEWAY_ADMIN_TOKEN", "dev-only-admin")
    database_url: str = os.environ.get("HAWK_DATABASE_URL", "")
    data_dir: Path = Path(os.environ.get("HAWK_DATA_DIR", "data"))
    traffic_source: str = os.environ.get("HAWK_TRAFFIC", "data/traffic/*.jsonl,data/voyage-access.jsonl")
    webhook_secret: str = os.environ.get("HAWK_WEBHOOK_SECRET", "dev-only-webhook")


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
