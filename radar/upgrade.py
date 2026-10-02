"""The data behind upgrade.json: per tracked package, the versions Radar has API data for, the diffs between them,
advisories with the known versions they affect, and migration snippets from published digests. No model involved."""
import json

from radar.affected import alias_groups, closed, evaluate, fixed_versions, for_package
from radar.versions import sort_key, sorted_stable

MAX_ITEMS = 20
MAX_ADVISORIES = 100
MAX_MIGRATIONS = 10
MAX_DIFFS = 60


def _changes(rows) -> list[dict]:
    return [{"symbol": str(c.get("symbol")), "detail": str(c.get("detail"))} for c in rows[:MAX_ITEMS] if isinstance(c, dict)]


def _migrations(digests: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for digest in digests:
        for cluster in digest.get("clusters") or []:
            if not isinstance(cluster, dict):
                continue
            migration = (cluster.get("synthesis") or {}).get("migration") if isinstance(cluster.get("synthesis"), dict) else None
            facts = cluster.get("facts") if isinstance(cluster.get("facts"), dict) else {}
            if not isinstance(migration, dict):
                continue
            for fact in facts.get("api_breaking") or []:
                if isinstance(fact, dict) and fact.get("package"):
                    out.setdefault(fact["package"], []).append({
                        "from": fact.get("from"), "to": fact.get("to"), "title": cluster.get("title"),
                        "date": digest.get("date"), "cluster": cluster.get("id"), "language": migration.get("language"),
                        "before": migration.get("before"), "after": migration.get("after")})
    return {k: sorted(v, key=lambda m: (m["date"] or "", m["cluster"] or ""), reverse=True)[:MAX_MIGRATIONS] for k, v in out.items()}


def upgrade_facts(conn, now: float, digests: list[dict]) -> dict:
    versions: dict[str, list[str]] = {}
    for r in conn.execute("SELECT ecosystem, package, version FROM radar_api_snapshots"):
        versions.setdefault(f"{r['ecosystem']}:{r['package']}", []).append(r["version"])
    migrations = _migrations(digests)
    packages = {}
    for key, found in sorted(versions.items()):
        ecosystem, _, name = key.partition(":")
        stable = sorted_stable(ecosystem, found)
        diffs = []
        for r in conn.execute("SELECT from_version, to_version, diff FROM radar_api_diffs WHERE ecosystem=? AND package=?",
                              (ecosystem, name)):
            body = json.loads(r["diff"])
            counts = body.get("counts") or {}
            diffs.append({"from": r["from_version"], "to": r["to_version"], "breaking": _changes(body.get("breaking") or []),
                          "deprecations": _changes(body.get("deprecations") or []),
                          "counts": {"breaking": counts.get("breaking", 0), "deprecations": counts.get("deprecations", 0)},
                          "truncated": bool(body.get("truncated")) or len(body.get("breaking") or []) > MAX_ITEMS
                                       or len(body.get("deprecations") or []) > MAX_ITEMS})
        diffs.sort(key=lambda d: (sort_key(ecosystem, d["to"]) is None, sort_key(ecosystem, d["to"]) or 0, d["to"]))
        diffs_omitted = max(0, len(diffs) - MAX_DIFFS)
        diffs = diffs[diffs_omitted:]
        rows = conn.execute("SELECT id, severity, summary, published, affected, aliases FROM radar_advisories WHERE package=? "
                            "ORDER BY published DESC, id", (key,)).fetchall()
        groups = alias_groups(rows)
        advisories = []
        for group in groups[:MAX_ADVISORIES]:
            lead = sorted(group, key=lambda r: (not r["id"].startswith("GHSA-"), -(r["published"] or 0), r["id"]))[0]
            affected = [e for r in group for e in for_package(json.loads(r["affected"]), ecosystem, name)]
            states = {v: evaluate(ecosystem, v, affected) for v in stable}
            advisories.append({"id": lead["id"], "severity": lead["severity"], "summary": lead["summary"],
                               "published": lead["published"],
                               "affected_versions": [v for v in stable if states[v] == "affected"],
                               "undetermined_versions": [v for v in stable if states[v] == "undetermined"],
                               "closed": closed(affected, ecosystem), "fixed_in": fixed_versions(affected, ecosystem)})
        packages[key] = {"ecosystem": ecosystem, "name": name, "versions": stable, "latest_stable": stable[-1] if stable else None,
                         "diffs": diffs, "diffs_omitted": diffs_omitted, "advisories": advisories, "advisories_omitted": max(0, len(groups) - MAX_ADVISORIES),
                         "migrations": migrations.get(key, [])}
    return {"generated_at": now, "packages": packages}
