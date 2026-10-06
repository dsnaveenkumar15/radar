"""Publish Radar digests as hash-manifested releases. A published digest is immutable (spec §4.6, §6.3)."""
import json
import re

from core.publish import load_current, prune, publish
from radar.config import load_sources
from radar import changes, signal, store, supersession, upgrade
from radar.versions import sorted_stable
from radar.synth import PROMPT_VERSION

DATASET = "digests"
COMPLETE_RATIO = 0.8
KEEP_RELEASES = 30
_DIGEST_PATH = re.compile(r"^digests/\d{4}-\d{2}-\d{2}\.json$")


def methodology(registry) -> dict:
    return {"signal": signal.formula(), "prompt_version": PROMPT_VERSION, "stack_tags": registry.stack_tags(),
            "validators": ["every claim cites item ids that exist in its cluster",
                           "versions must appear in the parsed release and API-diff data",
                           "'breaking' needs an API-diff break or a verified quote from release notes",
                           "migration snippets must parse and use symbols present in the new API",
                           "unknown entities go to owner review"],
            "completeness": f"published as full when at least {int(COMPLETE_RATIO * 100)}% of official sources were checked"}


RECENT_ADVISORY_S = 90 * 86400


def package_facts(conn, now: float) -> dict:
    """What other planets may know about the packages Radar tracks, published as packages.json."""
    versions: dict[str, list[str]] = {}
    for r in conn.execute("SELECT ecosystem, package, version FROM radar_api_snapshots"):
        versions.setdefault(f"{r['ecosystem']}:{r['package']}", []).append(r["version"])
    out = {}
    for key, found in versions.items():
        stable = sorted_stable(key.partition(":")[0], found)
        out[key] = {"latest_stable": stable[-1] if stable else None, "advisories": [], "last_breaking": None}
    for r in conn.execute("SELECT id, package, severity, published FROM radar_advisories WHERE published >= ? "
                          "ORDER BY published DESC, id", (now - RECENT_ADVISORY_S,)):
        entry = out.setdefault(r["package"], {"latest_stable": None, "advisories": [], "last_breaking": None})
        entry["advisories"].append({"id": r["id"], "severity": r["severity"], "published": r["published"]})
    for r in conn.execute("SELECT ecosystem, package, from_version, to_version, diff FROM radar_api_diffs "
                          "ORDER BY created_at DESC, to_version DESC"):
        entry = out.get(f"{r['ecosystem']}:{r['package']}")
        if entry is not None and entry["last_breaking"] is None and json.loads(r["diff"]).get("counts", {}).get("breaking"):
            entry["last_breaking"] = {"from": r["from_version"], "to": r["to_version"]}
    return {"generated_at": now, "packages": dict(sorted(out.items()))}


def _dump(obj) -> bytes:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")


def build_files(existing: dict[str, bytes], digest: dict, health: list[dict], registry, now: float,
                errata=(), packages=None, extra=None) -> dict[str, bytes]:
    files = {path: data for path, data in existing.items() if _DIGEST_PATH.match(path)}
    files.setdefault(f"digests/{digest['date']}.json", _dump(digest))
    entries = []
    for path, data in files.items():
        d = json.loads(data)
        entries.append({"date": d["date"], "status": d["status"], "synthesis": d["synthesis"],
                        "clusters": len(d["clusters"]), "breaking": sum(1 for c in d["clusters"] if c.get("impact") == "breaking")})
    files["index.json"] = _dump({"digests": sorted(entries, key=lambda e: e["date"], reverse=True)})
    files["sources.json"] = _dump({"generated_at": now, "sources": health, "methodology": methodology(registry)})
    files["errata.json"] = _dump(list(errata))
    if packages is not None:
        files["packages.json"] = _dump(packages)
    files.update(extra or {})
    return files


def publish_digest(conn, data_root, digest: dict, health: list[dict], registry, now: float) -> dict:
    release = load_current(data_root, "radar", DATASET)
    existing = {rel: release.read(rel) for rel in release.manifest["files"] if _DIGEST_PATH.match(rel)} if release else {}
    if f"digests/{digest['date']}.json" in existing:
        return {"published": False, "release": release.id, "reason": "digest already published"}
    extra = aux_files(conn, [json.loads(data) for data in existing.values()] + [digest], now)
    files = build_files(existing, digest, health, registry, now, store.errata(conn), extra=extra)
    release_id = publish(data_root, "radar", DATASET, files,
                         {"date": digest["date"], "prompt": PROMPT_VERSION, "signal": signal.SIGNAL_VERSION,
                          "model": digest["provenance"].get("model"), "generated_at": now}, conn=conn, now=now)
    conn.execute("INSERT OR IGNORE INTO radar_digests(date, status, synthesis, release_id, created_at) VALUES (?, ?, ?, ?, ?)",
                 (digest["date"], digest["status"], digest["synthesis"], release_id, now))
    prune(data_root, "radar", DATASET, keep=KEEP_RELEASES)
    return {"published": True, "release": release_id}


def publish_errata(conn, data_root, registry, now: float) -> dict:
    """Republish the current release with a new errata.json. Every other file, digests included, is byte-identical."""
    release = load_current(data_root, "radar", DATASET)
    if release is None:
        return {"published": False, "reason": "nothing published yet"}
    rows = store.errata(conn)
    files = {rel: release.read(rel) for rel in release.manifest["files"]}
    new = _dump(rows)
    if files.get("errata.json") == new:
        return {"published": False, "release": release.id}
    files["errata.json"] = new
    release_id = publish(data_root, "radar", DATASET, files,
                         {**release.manifest.get("provenance", {}), "errata": len(rows), "generated_at": now},
                         conn=conn, now=now)
    prune(data_root, "radar", DATASET, keep=KEEP_RELEASES)
    return {"published": True, "release": release_id}


def digests_in(release) -> list[dict]:
    return [json.loads(release.read(rel)) for rel in sorted(release.manifest["files"]) if _DIGEST_PATH.match(rel)]


def aux_files(conn, digests: list[dict], now: float, sources=None) -> dict[str, bytes]:
    """Files derived from the database rather than from one digest: refreshed on a schedule, not only nightly."""
    starters = [s.package for s in (load_sources() if sources is None else sources) if s.options.get("starter")]
    return {"surface.json": _dump(surface_facts(conn, now, starters)), "packages.json": _dump(package_facts(conn, now)), "upgrade.json": _dump(upgrade.upgrade_facts(conn, now, digests)),
            "supersessions.json": _dump(supersession.all_supersessions(conn, digests)),
            "api_changes.json": _dump(changes.api_changes(conn, now))}


def _same_content(old: bytes | None, new: bytes) -> bool:
    if old is None:
        return False
    try:
        a, b = json.loads(old), json.loads(new)
    except ValueError:
        return False
    if isinstance(a, dict) and isinstance(b, dict):
        a.pop("generated_at", None), b.pop("generated_at", None)
    return a == b


def publish_aux(conn, data_root, now: float) -> dict:
    """Republish the current release with fresh aux files. Digests and errata stay byte-identical, and nothing is
    written when only the generated_at stamp would change."""
    release = load_current(data_root, "radar", DATASET)
    if release is None:
        return {"published": False, "reason": "nothing published yet"}
    files = {rel: release.read(rel) for rel in release.manifest["files"]}
    fresh = aux_files(conn, digests_in(release), now)
    if all(_same_content(files.get(name), data) for name, data in fresh.items()):
        return {"published": False, "release": release.id}
    files.update(fresh)
    release_id = publish(data_root, "radar", DATASET, files, {**release.manifest.get("provenance", {}), "generated_at": now},
                         conn=conn, now=now)
    prune(data_root, "radar", DATASET, keep=KEEP_RELEASES)
    return {"published": True, "release": release_id}


MAX_SURFACE_SYMBOLS = 20_000


def surface_facts(conn, now: float, packages: list[str]) -> dict:
    """The public API names of the newest stable snapshot of each starter package, so another planet can check that code
    it generates only uses symbols that exist at the version it pins."""
    out = {}
    for package in packages:
        ecosystem, _, name = package.partition(":")
        rows = {r["version"]: r["symbols"] for r in conn.execute(
            "SELECT version, symbols FROM radar_api_snapshots WHERE ecosystem=? AND package=?", (ecosystem, name))}
        for version in reversed(sorted_stable(ecosystem, list(rows))):
            try:
                symbols = json.loads(rows[version]).get("symbols")
            except (ValueError, AttributeError):
                continue
            if isinstance(symbols, dict) and symbols:
                out[package] = {"version": version, "symbols": sorted(symbols)[:MAX_SURFACE_SYMBOLS]}
                break
    return {"generated_at": now, "packages": out}
