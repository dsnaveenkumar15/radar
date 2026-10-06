"""Radar's part of the owner admin: errata, entity aliases, and "updated" banners. Plugged in only when Radar is present."""
import time

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse

from core import review
from radar import store as radar_store
from radar.agent import runs as agent_runs
from radar.agent.config import load_config
from radar.config import load_entities
from web.admin import get_db, require_admin
from web.radar_site import CLUSTER, DATE

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


def section(conn, now: float) -> dict:
    """What the dashboard needs for the Radar sections."""
    radar_store.ensure_schema(conn)
    latest = conn.execute("SELECT date, status, synthesis FROM radar_digests ORDER BY date DESC LIMIT 1").fetchone()
    agent_runs.ensure_schema(conn)
    cfg = load_config()
    return {"radar_failing": radar_store.failing_sources(conn, now), "radar_latest": latest,
            "entity_ids": [e.id for e in load_entities().entities], "manual_updates": radar_store.supersessions(conn),
            "agent_spend": agent_runs.day_spend(conn, now), "agent_cap": cfg.daily_usd, "agent_configured": cfg.enabled,
            "agent_switch": agent_runs.is_enabled(conn)}


def _decide(conn, review_id: str, note: str) -> None:
    if review_id.strip().isdigit():
        review.decide(conn, int(review_id), "approved", note)


@router.post("/agent/toggle")
def toggle_agent(conn=Depends(get_db)):
    agent_runs.ensure_schema(conn)
    agent_runs.set_enabled(conn, not agent_runs.is_enabled(conn))
    return RedirectResponse("/admin", status_code=303)


@router.post("/errata")
def add_erratum(date: str = Form(...), cluster: str = Form(...), text: str = Form(...), review_id: str = Form(""),
                conn=Depends(get_db)):
    radar_store.ensure_schema(conn)
    text = text.strip()
    if not DATE.fullmatch(date) or not CLUSTER.fullmatch(cluster) or not 1 <= len(text) <= radar_store.ERRATUM_MAX:
        raise HTTPException(400, "date, cluster and a text of 1 to 1000 characters are required")
    radar_store.add_errata(conn, date, cluster, text, time.time())
    _decide(conn, review_id, "erratum published")
    return RedirectResponse("/admin", status_code=303)


@router.post("/aliases")
def add_alias(name: str = Form(...), entity_id: str = Form(...), review_id: str = Form(""), conn=Depends(get_db)):
    radar_store.ensure_schema(conn)
    if entity_id not in {e.id for e in load_entities().entities} or not name.strip():
        raise HTTPException(400, "a name and a known entity are required")
    radar_store.add_alias(conn, name, entity_id, time.time())
    _decide(conn, review_id, f"alias added: {entity_id}")
    return RedirectResponse("/admin", status_code=303)


@router.post("/supersessions")
def add_supersession(date: str = Form(...), cluster: str = Form(...), text: str = Form(...), url: str = Form(""),
                     conn=Depends(get_db)):
    radar_store.ensure_schema(conn)
    text = text.strip()
    if not DATE.fullmatch(date) or not CLUSTER.fullmatch(cluster) or not 1 <= len(text) <= radar_store.SUPERSESSION_MAX:
        raise HTTPException(400, "date, cluster and a text of 1 to 500 characters are required")
    radar_store.add_supersession(conn, date, cluster, text, url, time.time())
    return RedirectResponse("/admin", status_code=303)


@router.post("/supersessions/{supersession_id}/delete")
def delete_supersession(supersession_id: int, conn=Depends(get_db)):
    radar_store.ensure_schema(conn)
    if not radar_store.delete_supersession(conn, supersession_id):
        raise HTTPException(404)
    return RedirectResponse("/admin", status_code=303)
