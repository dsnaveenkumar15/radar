"""Supersession links (spec §6.3): later facts that make an earlier, immutable cluster out of date.
Two sources: an advisory published after the digest that affects a version the cluster covered, and the owner's manual marks."""
import json

from radar import store
from radar.affected import alias_groups, evaluate, for_package


def _lead(group):
    return sorted(group, key=lambda r: (not r["id"].startswith("GHSA-"), -(r["published"] or 0), r["id"]))[0]


def advisory_supersessions(conn, digests: list[dict]) -> list[dict]:
    """At most one banner per cluster and package version: late advisories are grouped by vulnerability (GHSA and PYSEC
    records of one CVE count once) and, when there are several, summarised."""
    found = []
    for digest in digests:
        if not isinstance(digest, dict) or not isinstance(digest.get("clusters"), list):
            continue
        generated = digest.get("generated_at") or 0
        for cluster in digest["clusters"]:
            facts = cluster.get("facts") if isinstance(cluster, dict) else None
            if not isinstance(facts, dict):
                continue
            reported = {a.get("id") for a in facts.get("advisories") or [] if isinstance(a, dict)}
            for entry in facts.get("packages") or []:
                if not isinstance(entry, dict) or not isinstance(entry.get("package"), str) or not isinstance(entry.get("version"), str):
                    continue
                ecosystem, _, name = entry["package"].partition(":")
                rows = [r for r in conn.execute(
                    "SELECT id, severity, published, affected, aliases FROM radar_advisories WHERE package=? AND published > ? "
                    "ORDER BY published, id", (entry["package"], generated))
                    if r["id"] not in reported
                    and evaluate(ecosystem, entry["version"], for_package(json.loads(r["affected"]), ecosystem, name)) == "affected"]
                groups = alias_groups(rows)
                if not groups:
                    continue
                lead = _lead(max(groups, key=lambda g: max(r["published"] for r in g)))
                if len(groups) == 1:
                    text = f"{lead['id']} ({lead['severity']}) was published after this digest and lists {name} {entry['version']} as affected."
                else:
                    text = (f"{len(groups)} advisories were published after this digest and list {name} {entry['version']} as affected, "
                            f"the latest being {lead['id']} ({lead['severity']}). See the upgrade report.")
                found.append({"date": digest.get("date"), "cluster": cluster.get("id"), "kind": "advisory", "ref": lead["id"],
                              "created_at": lead["published"], "url": f"https://osv.dev/vulnerability/{lead['id']}", "text": text})
    return found


def all_supersessions(conn, digests: list[dict]) -> list[dict]:
    rows = [r for r in store.supersessions(conn)] + advisory_supersessions(conn, digests)
    return sorted(rows, key=lambda r: (r["created_at"] or 0, str(r["date"]), str(r["cluster"]), str(r["ref"])))
