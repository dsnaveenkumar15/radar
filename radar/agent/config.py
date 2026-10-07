# radar/agent/config.py
"""Upgrade-agent settings: the shared agent settings under the AGENT_ prefix, plus Radar's model order."""
import os

from core.agent.config import AgentConfig, env_list as _list, env_prices as _prices, load_agent_config  # noqa: F401


def load_config(env=os.environ) -> AgentConfig:
    return load_agent_config("AGENT_", env)


def models_for(cfg: AgentConfig, mode: str) -> dict[str, list[str]]:
    """Model order per role. Models listed as public (free tiers may keep prompts) come first for a public repository
    and are never used for a pasted file, which may be private."""
    fast = (cfg.public_models if mode == "repo" else []) + cfg.fast_models
    return {"fast": fast, "strong": cfg.strong_models or cfg.fast_models}
