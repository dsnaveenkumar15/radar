"""Dependencies in use -> Radar's verified API changes between that version and the latest. No model involved."""
from radar.agent.types import Dependency
from radar.versions import sort_key


def _key(ecosystem: str, version: str):
    k = sort_key(ecosystem, version)
    return (k is None, k if k is not None else 0)


def untracked(deps: list[Dependency], api_changes: dict) -> list[str]:
    packages = api_changes.get("packages") or {}
    return sorted({f"{d.ecosystem}:{d.name}" for d in deps} - set(packages))


def ground(deps: list[Dependency], api_changes: dict) -> list[dict]:
    packages = api_changes.get("packages") or {}
    out = []
    for d in deps:
        key = f"{d.ecosystem}:{d.name}"
        data = packages.get(key)
        if data is None:
            continue
        versions, latest = data.get("versions") or [], data.get("latest")
        base = {"package": key, "used": d.version, "start": None, "latest": latest, "changes": []}
        if not versions or latest is None or sort_key(d.ecosystem, latest) is None:
            out.append({**base, "status": "no_data", "note": "Radar has no API data for this package yet."})
            continue
        if d.version is None:
            out.append({**base, "status": "unpinned", "note": f"Only a range is given ({d.spec}); pin an exact version to analyse it."})
            continue
        if sort_key(d.ecosystem, d.version) is None:
            out.append({**base, "status": "unpinned", "note": f"Version {d.version} is not comparable; pin a standard version to analyse it."})
            continue
        used = _key(d.ecosystem, d.version)
        if used >= _key(d.ecosystem, latest):
            out.append({**base, "status": "current", "note": "Already on the latest version Radar tracks."})
            continue
        older = [v for v in versions if _key(d.ecosystem, v) <= used]
        start = older[-1] if older else versions[0]
        if not older:
            note = f"Your version is older than Radar's data; changes since {start} are shown."
        elif start != d.version:
            note = f"Radar has no snapshot of {d.version}; the nearest older tracked version, {start}, is used."
        else:
            note = ""
        latest_names = set(data.get("latest_symbols") or [])
        changes, seen = [], set()
        for step in data.get("steps") or []:
            if _key(d.ecosystem, step["from"]) < _key(d.ecosystem, start):
                continue
            for kind, rows in (("breaking", step.get("breaking") or []), ("deprecation", step.get("deprecations") or [])):
                for row in rows:
                    if row.get("change") == "removed" and row["symbol"] in latest_names:
                        continue                         # removed once, present again in the latest release
                    if (row["symbol"], kind) in seen:
                        continue
                    seen.add((row["symbol"], kind))
                    changes.append({"package": key, "symbol": row["symbol"], "kind": kind, "change": row.get("change", ""),
                                    "detail": row.get("detail", ""), "from": step["from"], "to": step["to"]})
        out.append({**base, "start": start, "status": "outdated", "note": note, "changes": changes})
    return out
