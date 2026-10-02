"""Security advisories for tracked packages from OSV (which aggregates GitHub advisories). Severity is the source's."""
import json
import logging
from datetime import datetime, timezone

import httpx

from radar import store
from radar.errors import SchemaDrift, SourceError

OSV_URL = "https://api.osv.dev/v1/query"
log = logging.getLogger("radar.advisories")
ECOSYSTEMS = {"pypi": "PyPI", "npm": "npm"}
LOOKBACK_S = 14 * 86400
MAX_PAGES = 5
_SEVERITY = {"low": "low", "moderate": "medium", "medium": "medium", "high": "high", "critical": "critical"}


def _epoch(text) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()


def _severity(vuln: dict) -> str:
    raw = ((vuln.get("database_specific") or {}).get("severity") or "").lower()
    return _SEVERITY.get(raw, "unknown")


def fetch_osv(client: httpx.Client, ecosystem: str, name: str) -> list[dict]:
    vulns, token = [], None
    for _ in range(MAX_PAGES):
        body = {"package": {"name": name, "ecosystem": ECOSYSTEMS[ecosystem]}, **({"page_token": token} if token else {})}
        try:
            response = client.post(OSV_URL, json=body)
        except httpx.HTTPError as exc:
            raise SourceError(f"OSV unreachable: {exc}") from exc
        if response.status_code != 200:
            raise SourceError(f"OSV returned {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise SourceError("OSV did not return JSON") from exc
        vulns += data.get("vulns") or []
        token = data.get("next_page_token")
        if not token:
            break
    return vulns


def _record(conn, source, vulns: list[dict], now: float) -> None:
    valid = 0
    for v in vulns:
        vid, published = v.get("id"), _epoch(v.get("published"))
        if not vid or published is None:
            continue
        valid += 1
        severity, aliases = _severity(v), [a for a in (v.get("aliases") or []) if isinstance(a, str)][:10]
        summary = (v.get("summary") or "").strip() or "Security advisory"
        affected = [{"ranges": a.get("ranges"), "versions": (a.get("versions") or [])[:50]}
                    for a in (v.get("affected") or [])][:10]
        fresh = conn.execute(
            "INSERT OR IGNORE INTO radar_advisories(id, ecosystem, package, severity, summary, aliases, published, "
            "affected, first_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (vid, source.package.partition(":")[0], source.package, severity, summary, json.dumps(aliases), published,
             json.dumps(affected), now)).rowcount == 1
        if fresh and published >= now - LOOKBACK_S:
            store.add_item(conn, {"source": "osv", "tier": "official", "kind": "advisory",
                                  "url": f"https://osv.dev/vulnerability/{vid}", "title": f"{vid}: {summary}",
                                  "body": f"{summary}\n{v.get('details') or ''}", "published": published,
                                  "package": source.package,
                                  "extra": {"severity": severity, "id": vid, "aliases": aliases}}, now)
    if vulns and not valid:
        raise SchemaDrift("schema drift: no OSV entry had an id and a published date")


def refresh_advisories(conn, client: httpx.Client, sources, now: float) -> dict:
    report = {}
    for source in sources:
        if not source.package:
            continue
        ecosystem, _, name = source.package.partition(":")
        health_id = f"osv:{source.package}"
        try:
            _record(conn, source, fetch_osv(client, ecosystem, name), now)
        except Exception as exc:
            if not isinstance(exc, SourceError):
                log.exception("advisory refresh crashed for %s", source.package)
            store.record_source(conn, health_id, "official", "advisory", False, str(exc), now)
            report[source.package] = f"error: {exc}"
        else:
            store.record_source(conn, health_id, "official", "advisory", True, None, now)
            report[source.package] = "ok"
    return report
