"""Keep API snapshots and version-to-version diffs of tracked packages current (no LLM)."""
import json
import logging
from datetime import datetime

from core.db import tx
from core.events import emit
from radar import store
from radar.api_py import snapshot_python
from radar.api_ts import snapshot_typescript
from radar.apidiff import breaking_symbols, diff_snapshots
from radar.archive import ArchiveError
from radar.errors import SourceError
from radar.versions import sort_key, sorted_stable

log = logging.getLogger("radar.apisurface")

DEFAULT_TRACK = 2
MAX_DOWNLOADS = 8


def _load(conn, ecosystem: str, name: str, version: str) -> dict | None:
    row = conn.execute("SELECT symbols FROM radar_api_snapshots WHERE ecosystem=? AND package=? AND version=?",
                       (ecosystem, name, version)).fetchone()
    return json.loads(row["symbols"]) if row else None


def _surface(fetcher, ecosystem: str, name: str, info: dict, module: str) -> dict:
    files = fetcher.files(ecosystem, name, info)
    snap = snapshot_python(files, module or name.replace("-", "_")) if ecosystem == "pypi" else snapshot_typescript(files)
    if not snap["symbols"]:
        raise ValueError(f"no public symbols found in {name} {info['version']} (wrong module?)")
    return snap


def _diff_new_pairs(conn, package: str, ecosystem: str, name: str, tracked: list[str], now: float) -> None:
    for old_version, new_version in zip(tracked, tracked[1:]):
        if conn.execute("SELECT 1 FROM radar_api_diffs WHERE ecosystem=? AND package=? AND from_version=? "
                        "AND to_version=?", (ecosystem, name, old_version, new_version)).fetchone():
            continue
        old, new = _load(conn, ecosystem, name, old_version), _load(conn, ecosystem, name, new_version)
        if old is None or new is None:
            continue
        diff = diff_snapshots(old, new)
        with tx(conn):
            conn.execute("INSERT INTO radar_api_diffs(ecosystem, package, from_version, to_version, diff, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (ecosystem, name, old_version, new_version, json.dumps(diff), now))
            symbols = breaking_symbols(diff)
            if symbols:
                emit(conn, "radar.breaking_change_detected",
                     {"package": package, "from_version": old_version, "to_version": new_version,
                      "symbols": symbols[:50]}, now=now)


def refresh_api_surface(conn, fetcher, sources, now: float, max_downloads: int = MAX_DOWNLOADS,
                        track_last: int = DEFAULT_TRACK) -> dict:
    budget, report = max_downloads, {}
    for source in sources:
        if not (source.api and source.package):
            continue
        ecosystem, _, name = source.package.partition(":")
        try:
            infos = {v["version"]: v for v in fetcher.versions(ecosystem, name)}
            tracked = sorted_stable(ecosystem, list(infos))[-track_last:]
            for version in tracked:
                if _load(conn, ecosystem, name, version) is not None:
                    continue
                if budget <= 0:
                    break
                budget -= 1
                snap = _surface(fetcher, ecosystem, name, infos[version], source.module)
                conn.execute("INSERT OR REPLACE INTO radar_api_snapshots(ecosystem, package, version, symbols, "
                             "created_at) VALUES (?, ?, ?, ?, ?)", (ecosystem, name, version, json.dumps(snap), now))
            stored = [r["version"] for r in conn.execute(
                "SELECT version FROM radar_api_snapshots WHERE ecosystem=? AND package=?", (ecosystem, name))]
            # Releases that landed between runs: also diff the newest stored snapshot older than the window.
            older = [v for v in sorted_stable(ecosystem, stored) if tracked and sort_key(ecosystem, v) < sort_key(ecosystem, tracked[0])]
            _diff_new_pairs(conn, source.package, ecosystem, name, older[-1:] + tracked, now)
        except Exception as exc:
            if not isinstance(exc, (SourceError, ArchiveError, ValueError)):
                log.exception("api surface refresh crashed for %s", source.package)
            store.record_source(conn, f"api:{source.package}", "official", "api", False, str(exc), now)
            report[source.package] = f"error: {exc}"
        else:
            store.record_source(conn, f"api:{source.package}", "official", "api", True, None, now)
            report[source.package] = "ok"
    return report


BACKFILL_DOWNLOADS = 40


def _published(info: dict) -> float | None:
    raw = info.get("published")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def backfill_versions(ecosystem: str, infos: list[dict], now: float, window_days: int = 730,
                      max_versions: int = 15) -> list[str]:
    """Latest stable patch of each minor release published inside the window, oldest first, newest `max_versions`."""
    cutoff = now - window_days * 86400
    recent = [i["version"] for i in infos if (_published(i) or 0) >= cutoff]
    by_minor: dict[tuple, str] = {}
    for version in sorted_stable(ecosystem, recent):
        key = sort_key(ecosystem, version)
        # pypi: packaging Version (has .release); npm: tuple (major, minor, patch, ...)
        release = key.release if hasattr(key, "release") else key[:3]
        by_minor[tuple(release[:2])] = version          # ascending order, so the last patch of a minor wins
    return sorted(by_minor.values(), key=lambda v: sort_key(ecosystem, v))[-max_versions:]


def backfill_api_surface(conn, fetcher, sources, now: float, max_downloads: int = BACKFILL_DOWNLOADS) -> dict:
    """Snapshot older minor releases so the upgrade agent can ground any version from the last ~2 years. Stores
    snapshots only: no diffs and no events (old history is grounding data, not news; api_changes.json diffs it)."""
    budget, report = max_downloads, {}
    for source in sources:
        if not (source.api and source.package):
            continue
        ecosystem, _, name = source.package.partition(":")
        try:
            infos = fetcher.versions(ecosystem, name)
            by_version = {i["version"]: i for i in infos}
            for version in backfill_versions(ecosystem, infos, now):
                if _load(conn, ecosystem, name, version) is not None:
                    continue
                if budget <= 0:
                    break
                budget -= 1
                snap = _surface(fetcher, ecosystem, name, by_version[version], source.module)
                conn.execute("INSERT OR REPLACE INTO radar_api_snapshots(ecosystem, package, version, symbols, "
                             "created_at) VALUES (?, ?, ?, ?, ?)", (ecosystem, name, version, json.dumps(snap), now))
        except Exception as exc:
            if not isinstance(exc, (SourceError, ArchiveError, ValueError)):
                log.exception("api backfill crashed for %s", source.package)
            report[source.package] = f"error: {exc}"
        else:
            report[source.package] = "ok"
    return report
