# radar/agent/types.py
"""Plain data passed between the agent's steps (kept JSON-friendly: the graph state stores their dicts)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Dependency:
    ecosystem: str
    name: str
    version: str | None
    spec: str
    source: str
