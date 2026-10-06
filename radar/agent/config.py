# radar/agent/config.py
"""Upgrade-agent settings from the environment. Off unless AGENT_ENABLED=1 and a key is set."""
import os
from dataclasses import dataclass, field


def _list(raw: str) -> list[str]:
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


def _prices(raw: str) -> dict[str, tuple[float, float]]:
    out = {}
    for part in (raw or "").split(";"):
        if "=" in part and "/" in part:
            name, pair = part.split("=", 1)
            a, b = pair.split("/", 1)
            out[name.strip()] = (float(a), float(b))
    return out


@dataclass
class AgentConfig:
    enabled: bool = False
    api_key: str = ""
    base_url: str = "https://openrouter.ai/api/v1"
    fast_models: list[str] = field(default_factory=list)
    strong_models: list[str] = field(default_factory=list)
    prices: dict = field(default_factory=dict)
    daily_usd: float = 1.0
    run_usd: float = 0.05
    run_tokens: int = 60_000
    run_tool_calls: int = 30
    run_seconds: int = 120
    max_candidates: int = 8
    per_ip_day: int = 3
    concurrent: int = 2
    queue: int = 3


def load_config(env=os.environ) -> AgentConfig:
    return AgentConfig(enabled=env.get("AGENT_ENABLED") == "1" and bool(env.get("OPENROUTER_API_KEY")) and bool(_list(env.get("AGENT_FAST_MODELS", ""))),
                       api_key=env.get("OPENROUTER_API_KEY", ""),
                       base_url=env.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                       fast_models=_list(env.get("AGENT_FAST_MODELS", "")),
                       strong_models=_list(env.get("AGENT_STRONG_MODELS", "")),
                       prices=_prices(env.get("AGENT_PRICES", "")),
                       daily_usd=float(env.get("AGENT_DAILY_USD", "1.0")))
