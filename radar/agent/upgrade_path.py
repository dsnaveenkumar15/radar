"""Which version steps break this code, the safest route, a difficulty rating and open advisories. Numbers come from
data; the model only explains them."""
from radar.versions import sort_key

HOURS_PER_CHANGE, HOURS_PER_FILE = 0.25, 0.5


def _newer(eco, a, b):
    """a > b; False when either version is unparseable (never comparable, never "newest")."""
    ka, kb = sort_key(eco, a), sort_key(eco, b)
    return ka is not None and kb is not None and ka > kb


def plan_upgrade(grounding: dict, verified: list[dict], candidates: list[dict], upgrade_pkg: dict) -> dict:
    eco = grounding["package"].split(":", 1)[0]
    used = grounding.get("used") or grounding.get("start")
    ok_ids = {f["candidate_id"] for f in verified if f.get("verified")}
    breaking = [c for c in candidates if c["id"] in ok_ids and c["change"]["package"] == grounding["package"]
                and c["change"]["kind"] == "breaking"]
    later = [v for v in upgrade_pkg.get("versions") or [] if used and _newer(eco, v, used)]
    steps, safe_now = [], None
    for version in later:
        # a breaking change whose version can't be compared is assumed to apply (conservative)
        hits = [c for c in breaking if sort_key(eco, c["change"]["to"]) is None or not _newer(eco, c["change"]["to"], version)]
        if not hits:
            safe_now = version
            continue
        steps.append({"to": version, "changes": len(hits), "files": len({c["usage"]["path"] for c in hits})})
    files = len({c["usage"]["path"] for c in breaking})
    count = len(breaking)
    difficulty = "Easy" if count <= 2 and files <= 2 else ("Medium" if count <= 8 and files <= 4 else "Hard")
    security, minimum = [], None
    for adv in upgrade_pkg.get("advisories") or []:
        if used in (adv.get("affected_versions") or []) or used in (adv.get("undetermined_versions") or []):
            security.append({k: adv.get(k) for k in ("id", "severity", "summary", "fixed_in")})
            fixed = [v for v in adv.get("fixed_in") or [] if _newer(eco, v, used)]
            if fixed:
                first = fixed[0]
                for v in fixed[1:]:
                    if _newer(eco, first, v):
                        first = v
                if minimum is None or _newer(eco, first, minimum):
                    minimum = first
    return {"package": grounding["package"], "used": used, "latest": grounding.get("latest"), "safe_now": safe_now,
            "steps": steps, "difficulty": difficulty, "effort_hours": round(count * HOURS_PER_CHANGE + files * HOURS_PER_FILE, 2),
            "files": files, "security": security, "minimum_secure": minimum}
