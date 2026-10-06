"""Radar planet pages. Data comes only from the current verified 'radar/digests' release."""
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from core import review
from core.db import connect
from web.published import PublishedData
from radar import store as radar_store
from web.ratelimit import enforce
from web.security import client_ip, same_origin

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
CLUSTER = re.compile(r"c\d{1,12}")
IMPACTS = ("breaking", "notable", "minor")
EMPTY_INDEX = {"digests": []}


class RadarData(PublishedData):
    PLANET, DATASET = "radar", "digests"

    def index(self) -> dict:
        data = self._json("index.json", EMPTY_INDEX)
        return data if isinstance(data, dict) and isinstance(data.get("digests"), list) else EMPTY_INDEX

    def digest(self, date: str) -> dict | None:
        if not DATE.fullmatch(date):
            return None
        data = self._json(f"digests/{date}.json", None)
        return data if isinstance(data, dict) and isinstance(data.get("clusters"), list) else None

    def latest(self) -> dict | None:
        digests = self.index()["digests"]
        first = digests[0] if digests else None
        return self.digest(first["date"]) if isinstance(first, dict) and isinstance(first.get("date"), str) else None

    def api_changes(self) -> dict:
        data = self._json("api_changes.json", {})
        if not isinstance(data, dict) or not isinstance(data.get("packages"), dict):
            return {"generated_at": 0, "packages": {}}
        return data

    def upgrade(self) -> dict:
        data = self._json("upgrade.json", {})
        if not isinstance(data, dict) or not isinstance(data.get("packages"), dict):
            return {"generated_at": 0, "packages": {}}
        return data

    def supersessions(self) -> list[dict]:
        data = self._json("supersessions.json", [])
        return [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []

    def sources(self) -> dict:
        data = self._json("sources.json", {})
        return data if isinstance(data, dict) else {}

    def errata(self) -> list[dict]:
        data = self._json("errata.json", [])
        return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def _synthesis(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    return {"what_changed": "", "affects": [], "impact": "minor", "migration_effort": "", "action": "", "citations": [],
            "quote": None, "versions": [], "entities": [], "migration": None, **value}


def normalise(digest: dict) -> dict:
    """Fill every field the templates read, so a sparse or odd digest degrades to a sparse page, not an error."""
    d = {**digest}
    d["date"] = str(d.get("date") or "")
    d["status"] = d.get("status") or "partial"
    d["synthesis"] = d.get("synthesis") or "unsynthesized"
    d["generated_at"] = d.get("generated_at") or 0
    d["sources"] = {"checked": 0, "total": 0, "failed": [], **(d.get("sources") or {})}
    d["provenance"] = d.get("provenance") or {}
    d["new_servers"] = [s for s in (d.get("new_servers") or []) if isinstance(s, dict)]
    clusters = []
    for c in d.get("clusters") or []:
        if not isinstance(c, dict) or not CLUSTER.fullmatch(str(c.get("id", ""))):
            continue
        facts = {"api_breaking": [], "api_deprecations": [], "advisories": [], "packages": [], "versions": [],
                 **(c.get("facts") or {})}
        clusters.append({**c, "title": c.get("title") or c["id"],
                         "impact": c["impact"] if c.get("impact") in IMPACTS else "minor",
                         "signal": c.get("signal") or {"score": 0}, "facts": facts,
                         "items": [i for i in (c.get("items") or []) if isinstance(i, dict)],
                         "synthesis": _synthesis(c.get("synthesis"))})
    d["clusters"] = clusters
    return d


def radar_only(request: Request) -> None:
    if request.state.planet != "radar":
        raise HTTPException(404)


router = APIRouter(dependencies=[Depends(radar_only)])


def _corrections(request: Request, date: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for e in request.app.state.radar_data.errata():
        if e.get("date") == date:
            out.setdefault(str(e.get("cluster")), []).append(e)
    return out


def derive_tags(cluster: dict, registry) -> list[str]:
    """Stack tags for a cluster, from its own affects when synthesized, else from its packages and titles."""
    if cluster["synthesis"] and cluster["synthesis"]["affects"]:
        return list(cluster["synthesis"]["affects"])
    stack = set(registry.stack_tags())
    found: set[str] = set()
    for i in cluster["items"]:
        name = str(i.get("package") or "").partition(":")[2]
        text = f"{name} {i.get('title') or ''}"
        entity = registry.resolve(name) if name else None
        found |= ({entity} if entity else set()) | set(registry.mentions(text))
    return sorted(found & stack)


def _updates(request: Request, date: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for s in request.app.state.radar_data.supersessions():
        if s.get("date") == date:
            out.setdefault(str(s.get("cluster")), []).append(s)
    return out


def _digest_response(request: Request, digest: dict, latest: bool):
    from web.app import render   # local import: web.app includes this router
    d = normalise(digest)
    ids = {c["id"] for c in d["clusters"]}
    errata = {k: v for k, v in _corrections(request, d["date"]).items() if k in ids}
    registry = request.app.state.radar_registry
    for c in d["clusters"]:
        c["affects"] = derive_tags(c, registry)
    tags = sorted({t for c in d["clusters"] for t in c["affects"]})
    return render(request, "radar_digest.html", d=d, latest=latest, tags=tags, corrected=set(errata), updated=set(_updates(request, d["date"])) & ids,
                  errata_total=sum(len(v) for v in errata.values()))


def home(request: Request):
    from web.app import render
    latest = request.app.state.radar_data.latest()
    if latest is None:
        return render(request, "radar_empty.html")
    return _digest_response(request, latest, True)


@router.get("/digests/{date}", response_class=HTMLResponse)
def digest_page(date: str, request: Request):
    digest = request.app.state.radar_data.digest(date)
    if digest is None:
        raise HTTPException(404)
    return _digest_response(request, digest, False)


@router.get("/archive", response_class=HTMLResponse)
def archive_page(request: Request):
    from web.app import render
    rows = [e for e in request.app.state.radar_data.index()["digests"]
            if isinstance(e, dict) and DATE.fullmatch(str(e.get("date", "")))]
    return render(request, "radar_archive.html", rows=rows)


@router.get("/c/{date}/{cluster_id}", response_class=HTMLResponse)
def cluster_page(date: str, cluster_id: str, request: Request):
    from web.app import render
    digest = request.app.state.radar_data.digest(date)
    if digest is None or not CLUSTER.fullmatch(cluster_id):
        raise HTTPException(404)
    d = normalise(digest)
    found = next((c for c in d["clusters"] if c["id"] == cluster_id), None)
    if found is None:
        raise HTTPException(404)
    cited_ids = set(found["synthesis"]["citations"]) if found["synthesis"] else set()
    return render(request, "radar_cluster.html", d=d, c=found,
                  cited=[i for i in found["items"] if i.get("id") in cited_ids],
                  others=[i for i in found["items"] if i.get("id") not in cited_ids],
                  errata=_corrections(request, date).get(cluster_id, []),
                  updates=_updates(request, date).get(cluster_id, []),
                  reported=request.query_params.get("reported") == "1")


RSS_DIGESTS = 14
RSS_ITEMS = 50
FEED_IMPACTS = ("breaking", "notable")
_XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _x(text) -> str:
    return escape(_XML_ILLEGAL.sub("", str(text)))


def feed_xml(request: Request) -> str:
    settings = request.app.state.settings
    base = f"{settings.public_scheme}://{settings.hosts['radar']}{settings.public_port}"
    data = request.app.state.radar_data
    entries = []
    for row in data.index()["digests"][:RSS_DIGESTS]:
        digest = data.digest(row["date"]) if isinstance(row, dict) and isinstance(row.get("date"), str) else None
        if digest is None:
            continue
        d = normalise(digest)
        entries += [(d, c) for c in d["clusters"] if c["impact"] in FEED_IMPACTS]
    entries = entries[:RSS_ITEMS]
    items = []
    for d, c in entries:
        link = f"{base}/c/{d['date']}/{c['id']}"
        text = c["synthesis"]["what_changed"] if c["synthesis"] else "Unsynthesized: facts and sources are on the page."
        stamp = format_datetime(datetime.fromtimestamp(d["generated_at"], timezone.utc))
        items.append(f"<item><title>{_x('[' + c['impact'] + '] ' + c['title'])}</title><link>{_x(link)}</link>"
                     f"<guid isPermaLink=\"true\">{_x(link)}</guid><pubDate>{stamp}</pubDate>"
                     f"<description>{_x(text)}</description></item>")
    built = format_datetime(datetime.fromtimestamp(entries[0][0]["generated_at"], timezone.utc)) if entries \
        else format_datetime(datetime.now(timezone.utc))
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?><rss version=\"2.0\"><channel>"
            f"<title>Radar</title><link>{_x(base)}/</link><description>What changed in AI engineering</description>"
            f"<lastBuildDate>{built}</lastBuildDate>{''.join(items)}</channel></rss>")


@router.get("/feed.xml")
def feed(request: Request):
    return Response(feed_xml(request), media_type="application/rss+xml")


@router.get("/upgrade.json")
def upgrade_json(request: Request):
    return JSONResponse(request.app.state.radar_data.upgrade(), headers={"Cache-Control": "public, max-age=300"})


@router.get("/upgrade", response_class=HTMLResponse)
def upgrade_page(request: Request):
    from web.app import render
    return render(request, "radar_upgrade.html")


@router.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request):
    from web.app import render
    s = request.app.state.radar_data.sources()
    return render(request, "radar_sources.html", s=s, health=[h for h in s.get("sources", []) if isinstance(h, dict)],
                  m=s.get("methodology") if isinstance(s.get("methodology"), dict) else {})


MAX_REPORT_CHARS = 1000
MAX_OPEN_REPORTS = 200


def radar_db(request: Request):
    conn = connect(request.app.state.settings.db_path)
    try:
        radar_store.ensure_schema(conn)
        yield conn
    finally:
        conn.close()


@router.post("/api/report")
def report(request: Request, date: str = Form(...), cluster: str = Form(...), message: str = Form(...),
           conn=Depends(radar_db)):
    settings = request.app.state.settings
    ip = client_ip(request, settings.trust_proxy)
    enforce(request.app.state.report_limiter, ip)
    if request.headers.get("origin") and not same_origin(request):
        raise HTTPException(403, "cross-origin request refused")
    digest = request.app.state.radar_data.digest(date)
    if digest is None or not CLUSTER.fullmatch(cluster) or cluster not in {c["id"] for c in normalise(digest)["clusters"]}:
        raise HTTPException(404)
    if len(message.strip()) > MAX_REPORT_CHARS:
        raise HTTPException(400, f"message must be at most {MAX_REPORT_CHARS} characters")
    text = radar_store.clean_text(message.strip(), MAX_REPORT_CHARS)
    if not text:
        raise HTTPException(400, "message must not be empty")
    ref = f"{date}/{cluster}#{hashlib.sha1(' '.join(text.lower().split()).encode('utf-8')).hexdigest()[:10]}"
    open_reports = conn.execute("SELECT count(*) FROM review_queue WHERE kind='radar-error' AND state='open'").fetchone()[0]
    if open_reports >= MAX_OPEN_REPORTS and review.entry_for(conn, "radar-error", ref) is None:
        raise HTTPException(429, "the report queue is full; try again later")
    review.add(conn, "radar", "radar-error", ref,
               {"date": date, "cluster": cluster, "message": text,
                "reporter": hashlib.sha256(ip.encode("utf-8")).hexdigest()[:12]}, now=time.time())
    return RedirectResponse(f"/c/{date}/{cluster}?reported=1", status_code=303)


WEEK = re.compile(r"\d{4}-W\d{2}")


def normalise_week(doc) -> dict | None:
    """Fill every field the template reads, so a sparse week file degrades to a sparse page."""
    if not isinstance(doc, dict) or not isinstance(doc.get("stats"), dict):
        return None
    stats = {"entities": [], "clusters": [], "totals": {}, **doc["stats"]}
    stats["entities"] = [e for e in stats["entities"] if isinstance(e, dict)] if isinstance(stats["entities"], list) else []
    stats["clusters"] = [c for c in stats["clusters"] if isinstance(c, dict)] if isinstance(stats["clusters"], list) else []
    stats["totals"] = stats["totals"] if isinstance(stats["totals"], dict) else {}
    commentary = doc.get("commentary") if isinstance(doc.get("commentary"), dict) else None
    if commentary is not None:
        commentary = {"summary": "", "rising": [], "falling": [], "patterns": [], **commentary}
        for key in ("rising", "falling", "patterns"):
            commentary[key] = [m for m in commentary[key] if isinstance(m, dict)] if isinstance(commentary[key], list) else []
    return {**doc, "week": str(doc.get("week")), "stats": stats, "commentary": commentary}


class TrendsData(RadarData):
    PLANET, DATASET = "radar", "trends"

    def index(self) -> list[dict]:
        data = self._json("index.json", {})
        weeks = data.get("weeks") if isinstance(data, dict) else None
        return [w for w in weeks or [] if isinstance(w, dict) and WEEK.fullmatch(str(w.get("week", "")))]

    def week(self, week: str) -> dict | None:
        if not WEEK.fullmatch(week):
            return None
        return normalise_week(self._json(f"weeks/{week}.json", None))


def _trends_page(request: Request, doc: dict | None):
    from web.app import render
    return render(request, "radar_trends.html", w=doc, weeks=request.app.state.trends_data.index())


@router.get("/trends", response_class=HTMLResponse)
def trends_latest(request: Request):
    data = request.app.state.trends_data
    weeks = data.index()
    return _trends_page(request, data.week(weeks[0]["week"]) if weeks else None)


@router.get("/trends/{week}", response_class=HTMLResponse)
def trends_week(week: str, request: Request):
    doc = request.app.state.trends_data.week(week)
    if doc is None:
        raise HTTPException(404)
    return _trends_page(request, doc)
