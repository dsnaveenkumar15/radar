"""api_changes.json: every consecutive pair of stored API snapshots, diffed without caps, for the upgrade agent's
grounding. Recomputed on each publish so backfilled versions slot in between existing ones."""
import json

import logging

from radar.apidiff import diff_snapshots
from radar.versions import sorted_stable

KEEP = ("symbol", "change", "detail")
log = logging.getLogger(__name__)


def _rows(rows) -> list[dict]:
    return [{k: str(c.get(k, "")) for k in KEEP} for c in rows]


def _package(conn, ecosystem: str, name: str, versions: list[str]) -> dict:
    stable = sorted_stable(ecosystem, versions)
    snaps = {r["version"]: {"symbols": {}, **json.loads(r["symbols"])} for r in conn.execute(
        "SELECT version, symbols FROM radar_api_snapshots WHERE ecosystem=? AND package=?", (ecosystem, name))}
    steps = []
    for old, new in zip(stable, stable[1:]):
        diff = diff_snapshots(snaps[old], snaps[new], limit=None, added_limit=0)
        steps.append({"from": old, "to": new, "breaking": _rows(diff["breaking"]), "deprecations": _rows(diff["deprecations"])})
    latest = stable[-1] if stable else None
    return {"ecosystem": ecosystem, "name": name, "versions": stable, "latest": latest,
            "latest_symbols": sorted((snaps[latest].get("symbols") or {}).keys()) if latest else [], "steps": steps}


def api_changes(conn, now: float) -> dict:
    found: dict[tuple, list[str]] = {}
    for r in conn.execute("SELECT ecosystem, package, version FROM radar_api_snapshots"):
        found.setdefault((r["ecosystem"], r["package"]), []).append(r["version"])
    packages = {}
    for (ecosystem, name), versions in sorted(found.items()):
        try:
            packages[f"{ecosystem}:{name}"] = _package(conn, ecosystem, name, versions)
        except Exception:                      # one corrupt snapshot must not block the other packages or the aux publish
            log.exception("api_changes skipped %s:%s", ecosystem, name)
    return {"generated_at": now, "packages": packages}
