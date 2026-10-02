"""Deterministic facts about a cluster: what the API diff and the advisory feed say, independent of the LLM."""
import json

from radar.versions import same

MAX_SYMBOLS = 20


def _split(package: str) -> tuple[str, str]:
    ecosystem, _, name = package.partition(":")
    return ecosystem, name


def cluster_facts(conn, items: list[dict]) -> dict:
    api_breaking, api_deprecations, advisories, packages = [], [], [], []
    versions: set[str] = set()
    for item in items:
        if item.get("version"):
            versions.add(item["version"])
        if item["kind"] == "advisory":
            advisories.append({"id": item["extra"].get("id"), "severity": item["extra"].get("severity", "unknown"),
                               "package": item.get("package")})
        if item["kind"] != "release" or item["extra"].get("prerelease") or not (item.get("package") and item.get("version")):
            continue
        ecosystem, name = _split(item["package"])
        packages.append({"package": item["package"], "version": item["version"]})
        for row in conn.execute("SELECT from_version, to_version, diff FROM radar_api_diffs WHERE ecosystem=? AND package=?",
                                (ecosystem, name)):
            if not same(row["to_version"], item["version"]):
                continue
            diff = json.loads(row["diff"])
            versions |= {row["from_version"], row["to_version"]}
            if diff["breaking"]:
                api_breaking.append({
                    "package": item["package"], "from": row["from_version"], "to": row["to_version"],
                    "symbols": list(dict.fromkeys(c["symbol"] for c in diff["breaking"]))[:MAX_SYMBOLS],
                    "details": [{"symbol": c["symbol"], "detail": c["detail"]} for c in diff["breaking"][:MAX_SYMBOLS]]})
            if diff["deprecations"]:
                api_deprecations.append({"package": item["package"], "from": row["from_version"], "to": row["to_version"],
                                         "symbols": [c["symbol"] for c in diff["deprecations"][:MAX_SYMBOLS]]})
    return {"api_breaking": api_breaking, "api_deprecations": api_deprecations, "advisories": advisories,
            "packages": packages, "versions": sorted(versions)}


def package_surface(conn, package: str, version: str) -> dict | None:
    ecosystem, name = _split(package)
    for row in conn.execute("SELECT version, symbols FROM radar_api_snapshots WHERE ecosystem=? AND package=?",
                            (ecosystem, name)):
        if same(row["version"], version):
            snap = json.loads(row["symbols"])
            return {"ecosystem": ecosystem, "module": snap.get("module") or name, "symbols": set(snap["symbols"])}
    return None
