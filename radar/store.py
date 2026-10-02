import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from radar.urls import canonical_url

SCHEMA = Path(__file__).with_name("schema.sql")
TITLE_MAX = 300
BODY_MAX = 20_000
FIRST_LOOKBACK_S = 7 * 86400
OVERLAP_S = 2 * 86400
MAX_BACKFILL_S = 30 * 86400
OK_WITHIN_S = 36 * 3600
# C0/C1 controls (except \t \n), zero-width and bidi-override characters, and Unicode tag characters:
# the usual carriers of hidden instructions in feed text.
_HIDDEN = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏‪-‮⁠-⁤﻿\U000e0000-\U000e007f]")


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    # Items collected before the FTS index existed (or after a restore) are indexed in one pass.
    indexed = conn.execute("SELECT count(*) FROM radar_items_fts_docsize").fetchone()[0]
    if indexed != conn.execute("SELECT count(*) FROM radar_items").fetchone()[0]:
        conn.execute("INSERT INTO radar_items_fts(radar_items_fts) VALUES ('rebuild')")


def clean_text(text: str | None, limit: int) -> str:
    return _HIDDEN.sub("", text or "")[:limit]


def add_item(conn, item: dict, now: float, consumed: str | None = None) -> int | None:
    title, body = clean_text(item["title"], TITLE_MAX), clean_text(item.get("body"), BODY_MAX)
    digest = hashlib.sha256(f"{title}\n{body}".encode("utf-8", "replace")).hexdigest()
    cur = conn.execute(
        "INSERT OR IGNORE INTO radar_items(source, tier, kind, url, canonical_url, title, body, published, "
        "content_hash, package, version, repo, stars, extra, fetched_at, digest_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (item["source"], item["tier"], item["kind"], item["url"], canonical_url(item["url"]), title, body,
         item["published"], digest, item.get("package"), item.get("version"), item.get("repo"),
         int(item.get("stars") or 0), json.dumps(item.get("extra") or {}, sort_keys=True), now, consumed))
    return cur.lastrowid if cur.rowcount == 1 else None


def _item(row) -> dict:
    out = dict(row)
    out["extra"] = json.loads(out["extra"])
    return out


def items_unconsumed(conn, since: float, limit: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM radar_items WHERE digest_date IS NULL AND published >= ? "
                        "ORDER BY published DESC, id DESC LIMIT ?", (since, limit)).fetchall()
    return [_item(r) for r in rows]


def mark_consumed(conn, item_ids: list[int], date: str) -> None:
    conn.executemany("UPDATE radar_items SET digest_date=? WHERE id=? AND digest_date IS NULL",
                     [(date, i) for i in item_ids])


def record_source(conn, source_id: str, tier: str, kind: str, ok: bool, error: str | None, now: float) -> None:
    conn.execute("INSERT OR IGNORE INTO radar_sources(source, tier, kind) VALUES (?, ?, ?)", (source_id, tier, kind))
    if ok:
        conn.execute("UPDATE radar_sources SET watermark=?, last_ok=?, last_attempt=?, last_error=NULL, "
                     "consecutive_failures=0 WHERE source=?", (now, now, now, source_id))
    else:
        conn.execute("UPDATE radar_sources SET last_attempt=?, last_error=?, "
                     "consecutive_failures=consecutive_failures+1 WHERE source=?", (now, (error or "")[:300], source_id))


def since(conn, source_id: str, now: float) -> float:
    row = conn.execute("SELECT watermark FROM radar_sources WHERE source=?", (source_id,)).fetchone()
    if row is None or not row["watermark"]:
        return now - FIRST_LOOKBACK_S
    return max(row["watermark"] - OVERLAP_S, now - MAX_BACKFILL_S)


def source_health(conn, now: float) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM radar_sources ORDER BY source"):
        out.append({"source": r["source"], "tier": r["tier"], "kind": r["kind"], "last_ok": r["last_ok"],
                    "last_error": r["last_error"], "consecutive_failures": r["consecutive_failures"],
                    "ok": r["last_ok"] is not None and now - r["last_ok"] <= OK_WITHIN_S,
                    "drift": bool(r["last_error"] and r["last_error"].startswith("schema drift"))})
    return out


def save_stars(conn, repo: str, day: str, stars: int) -> None:
    conn.execute("INSERT INTO radar_star_samples(repo, day, stars) VALUES (?, ?, ?) "
                 "ON CONFLICT(repo, day) DO UPDATE SET stars=excluded.stars", (repo, day, stars))


def star_velocity(conn, repo: str, now: float) -> int:
    latest = conn.execute("SELECT stars FROM radar_star_samples WHERE repo=? ORDER BY day DESC LIMIT 1",
                          (repo,)).fetchone()
    cutoff = (datetime.fromtimestamp(now, timezone.utc) - timedelta(days=7)).date().isoformat()
    old = conn.execute("SELECT stars FROM radar_star_samples WHERE repo=? AND day <= ? ORDER BY day DESC LIMIT 1",
                       (repo, cutoff)).fetchone()
    return max(0, latest["stars"] - old["stars"]) if latest and old else 0


ERRATUM_MAX = 1000


def add_errata(conn, date: str, cluster_id: str, text: str, now: float) -> int:
    return conn.execute("INSERT INTO radar_errata(digest_date, cluster_id, text, created_at) VALUES (?, ?, ?, ?)",
                        (date, cluster_id, clean_text(text, ERRATUM_MAX), now)).lastrowid


def errata(conn) -> list[dict]:
    return [{"date": r["digest_date"], "cluster": r["cluster_id"], "text": r["text"], "created_at": r["created_at"]}
            for r in conn.execute("SELECT * FROM radar_errata ORDER BY created_at, id")]


def add_alias(conn, name: str, entity_id: str, now: float) -> None:
    from radar.config import alias_key   # local import: keeps store free of config at import time
    conn.execute("INSERT INTO radar_aliases(alias_key, entity_id, created_at) VALUES (?, ?, ?) "
                 "ON CONFLICT(alias_key) DO UPDATE SET entity_id=excluded.entity_id, created_at=excluded.created_at",
                 (alias_key(name), entity_id, now))


def aliases(conn) -> dict[str, str]:
    return {r["alias_key"]: r["entity_id"] for r in conn.execute("SELECT alias_key, entity_id FROM radar_aliases")}


def failing_sources(conn, now: float) -> list[dict]:
    return [h for h in source_health(conn, now) if not h["ok"] or h["consecutive_failures"] or h["drift"]]


SUPERSESSION_MAX = 500


def add_supersession(conn, date: str, cluster_id: str, text: str, url: str, now: float) -> int:
    url = url.strip()
    return conn.execute("INSERT INTO radar_supersessions(digest_date, cluster_id, text, url, created_at) VALUES (?, ?, ?, ?, ?)",
                        (date, cluster_id, clean_text(text, SUPERSESSION_MAX), url if url.startswith("https://") else "", now)).lastrowid


def supersessions(conn) -> list[dict]:
    return [{"date": r["digest_date"], "cluster": r["cluster_id"], "kind": "manual", "ref": f"manual:{r['id']}",
             "text": r["text"], "url": r["url"], "created_at": r["created_at"]}
            for r in conn.execute("SELECT * FROM radar_supersessions ORDER BY created_at, id")]


def delete_supersession(conn, supersession_id: int) -> bool:
    return conn.execute("DELETE FROM radar_supersessions WHERE id=?", (supersession_id,)).rowcount == 1
