"""Deterministic signal score. The formula is public (shown on the sources page); the LLM never sets it."""
import math

SIGNAL_VERSION = "radar.signal@1"
WEIGHTS = {"tier": 40, "corroboration": 20, "velocity": 20, "code": 20}
COMMUNITY_TIER_FACTOR = 0.4
CODE_PARTIAL = 12
VELOCITY_FULL = 1000      # stars gained in 7 days that earn the full velocity weight (log scale)


def score_cluster(items: list[dict], velocity: int, has_api_change: bool) -> dict:
    tier = WEIGHTS["tier"] * (1.0 if any(i["tier"] == "official" for i in items) else COMMUNITY_TIER_FACTOR)
    sources = len({i["source"] for i in items})
    corroboration = WEIGHTS["corroboration"] * min(sources - 1, 3) / 3
    vel = WEIGHTS["velocity"] * min(math.log10(1 + max(velocity, 0)) / math.log10(1 + VELOCITY_FULL), 1.0)
    if has_api_change:
        code = float(WEIGHTS["code"])
    else:
        code = float(CODE_PARTIAL) if any(i["kind"] in ("release", "server") or i.get("repo") for i in items) else 0.0
    parts = {"tier": round(tier, 1), "corroboration": round(corroboration, 1), "velocity": round(vel, 1),
             "code": round(code, 1)}
    return {"score": min(100, round(tier + corroboration + vel + code)), "parts": parts, "version": SIGNAL_VERSION}


def formula() -> dict:
    return {"version": SIGNAL_VERSION, "weights": dict(WEIGHTS),
            "tier": "full weight if any item is from an official source, otherwise 40% of it",
            "corroboration": "distinct sources beyond the first, up to three",
            "velocity": f"log-scaled 7-day GitHub star gain, full weight at {VELOCITY_FULL}",
            "code": f"full weight when an API diff found a break or deprecation, {CODE_PARTIAL} when a release or "
                    "repository stands behind the claim"}
