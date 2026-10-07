# web/radar_dashboard.py
"""Radar home dashboard: plain data for the instrument panel, computed from published files. No model, no network."""
import json
import math
import sqlite3
from datetime import datetime, timezone

GLYPHS = {"breaking": ("▲", "Break"), "notable": ("◆", "Notable")}
MINOR = ("○", "Minor")
LEVEL_RADIUS = {"break": 0.38, "cve": 0.64, "ok": 0.88}     # nearer the centre = more urgent


LIVE_DAYS = 2                      # a digest is written nightly: up to two days old still counts as live


def freshness(date: str, now: float) -> dict:
    """Whether the newest digest is recent enough to call the page live, and how many days old it is."""
    try:
        published = datetime.strptime(str(date), "%Y-%m-%d").replace(tzinfo=timezone.utc).date()
    except ValueError:
        return {"live": False, "days": None}
    days = (datetime.fromtimestamp(now, timezone.utc).date() - published).days
    return {"live": days <= LIVE_DAYS, "days": max(days, 0)}


def ticker(digest: dict, limit: int = 6) -> list[dict]:
    order = {"breaking": 0, "notable": 1}
    clusters = sorted(digest.get("clusters") or [], key=lambda c: order.get(c.get("impact"), 2))   # stable sort
    rows = []
    for c in clusters[:limit]:
        impact = c.get("impact") if c.get("impact") in order else "minor"
        glyph, label = GLYPHS.get(impact, MINOR)
        rows.append({"id": c["id"], "title": c.get("title", ""), "impact": impact, "glyph": glyph, "label": label})
    return rows


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def packages(upgrade: dict) -> list[dict]:
    """One row per tracked package. Malformed entries are skipped or zeroed: the home page must never fail on them."""
    rows = []
    for key, pkg in sorted((upgrade.get("packages") or {}).items()):
        if not isinstance(pkg, dict):
            continue
        latest = str(pkg.get("latest_stable") or "")
        diffs = [d for d in pkg.get("diffs") or [] if isinstance(d, dict)]
        breaking = sum(_int((d.get("counts") or {}).get("breaking")) for d in diffs if isinstance(d.get("counts"), dict))
        # "Not closed" only means Radar cannot prove a fix; count what the latest release itself is listed as affected by.
        on_latest = sum(1 for a in pkg.get("advisories") or []
                        if isinstance(a, dict) and latest and latest in (a.get("affected_versions") or []))
        level = "break" if breaking else ("cve" if on_latest else "ok")
        span = f"{diffs[0].get('from', '')} → {diffs[-1].get('to', '')}".strip() if diffs else ""
        rows.append({"key": key, "latest": latest, "breaking": breaking, "advisories": on_latest, "level": level, "span": span})
    return rows


def sweep(rows: list[dict], size: int = 240) -> list[dict]:
    centre, reach = size / 2, size / 2 - 12
    points = []
    for i, row in enumerate(rows):
        angle = -math.pi / 2 + 2 * math.pi * i / len(rows)
        r = reach * (LEVEL_RADIUS[row["level"]] + 0.05 * (i % 3 - 1))
        points.append({"key": row["key"], "label": row["key"].split(":", 1)[-1], "level": row["level"],
                       "x": round(centre + r * math.cos(angle), 1), "y": round(centre + r * math.sin(angle), 1),
                       "note": f"{row['breaking']} breaking, {row['advisories']} advisories on the latest release"})
    return points


def activity(index: dict, days: int = 14, height: int = 56) -> list[dict]:
    entries = [e for e in (index.get("digests") or []) if isinstance(e, dict)][:days][::-1]    # oldest first
    top = max([int(e.get("clusters") or 0) for e in entries] + [1])
    bars = []
    for i, e in enumerate(entries):
        total, breaking = int(e.get("clusters") or 0), int(e.get("breaking") or 0)
        h_total, h_break = round(height * total / top, 1), round(height * breaking / top, 1)
        bars.append({"date": str(e.get("date", "")), "clusters": total, "breaking": breaking, "x": i * 20,
                     "y_total": round(height - h_total, 1), "h_total": h_total,
                     "y_break": round(height - h_break, 1), "h_break": h_break})
    return bars


def recorded_runs(db_path: str) -> list[dict]:
    """Showcase runs of the upgrade agent; an unreadable store means none, never an error page."""
    from core.db import connect
    from radar.agent import runs
    try:
        conn = connect(db_path)
        try:
            runs.ensure_schema(conn)
            rows = runs.showcases(conn)
        finally:
            conn.close()
    except (sqlite3.Error, OSError):                 # unreadable store or a path that cannot be created
        return []
    out = []
    for r in rows:
        try:
            source = json.loads(r["input"]) or {}
        except (TypeError, ValueError):
            source = {}
        repo = str(source.get("repo_url") or source.get("filename") or r["id"]).replace("https://github.com/", "")
        out.append({"id": r["id"], "repo": repo, "cost": float(r["cost_usd"] or 0)})
    return out
