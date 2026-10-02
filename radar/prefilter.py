"""Pick the clusters worth the night's Claude quota: pinned facts first, then relevant clusters by signal."""
import re

from radar.embed import cosine

DEFAULT_LIMIT = 20
MIN_COMMUNITY_SCORE = 15
ANCHOR_SIMILARITY = 0.55
KEYWORDS = re.compile(
    r"\b(agents?|agentic|mcp|model context protocol|llms?|large language models?|rag|retrieval|embeddings?|"
    r"prompt(?:s|ing)?|inference|fine-?tun\w*|evals?|guardrails?|multi-agent|function calling|tool use|"
    r"context window|reasoning models?)\b", re.I)
ANCHORS = [
    "building reliable LLM agents and multi-agent systems",
    "model context protocol servers and tools",
    "retrieval-augmented generation and vector search",
    "LLM evaluation, guardrails and safety",
    "LLM inference serving and optimisation",
    "agent framework release notes and breaking changes",
]
_KIND_RANK = {"discussion": 0, "post": 1, "paper": 2}


def _mean(vectors: list[list[float]]) -> list[float]:
    return [sum(column) / len(vectors) for column in zip(*vectors)]


def relevance(items: list[dict], registry, vectors: dict | None = None, anchor_vectors: list | None = None) -> float:
    text = " ".join(f"{i['title']} {i['body'][:300]}" for i in items)
    score = 0.5 if KEYWORDS.search(text) else 0.0
    if registry.mentions(text):
        score += 0.5
    found = [vectors[i["id"]] for i in items if vectors and i["id"] in vectors]
    if found and anchor_vectors:
        centre = _mean(found)
        if max(cosine(centre, a) for a in anchor_vectors) >= ANCHOR_SIMILARITY:
            score += 0.5
    return min(score, 1.0)


def is_pinned(entry: dict) -> bool:
    facts = entry["facts"]
    return (bool(facts["api_breaking"])
            or any(a["severity"] in ("high", "critical") for a in facts["advisories"])
            or any(i["kind"] == "release" and i.get("package") and not i["extra"].get("prerelease")
                   for i in entry["items"]))


def _rank(entry: dict):
    return (-entry["signal"]["score"], -entry["relevance"],
            min(_KIND_RANK.get(i["kind"], 3) for i in entry["items"]), int(entry["id"][1:]))


def select_clusters(entries: list[dict], limit: int = DEFAULT_LIMIT) -> list[dict]:
    pinned = sorted((e for e in entries if is_pinned(e)), key=_rank)
    eligible = sorted((e for e in entries if not is_pinned(e) and e["relevance"] > 0
                       and (any(i["tier"] == "official" for i in e["items"])
                            or e["signal"]["score"] >= MIN_COMMUNITY_SCORE)), key=_rank)
    return (pinned + eligible)[:limit]
