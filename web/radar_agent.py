# web/radar_agent.py
"""Upgrade agent pages and API. Runs execute on background threads (radar.agent.runs.Runner); the browser follows a
run over a same-origin server-sent-events stream that reads the event log, so a reload resumes where it left off."""
import json
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from core.agent.stream import follow, response as stream_response
from core.db import connect
from radar.agent import inputs, runs
from web.radar_site import radar_only
from web.security import client_ip, same_origin

router = APIRouter(dependencies=[Depends(radar_only)])
RUN_ID = re.compile(r"[0-9a-f]{16}")
STREAM_SECONDS = 180


class StartRun(BaseModel):
    repo_url: str | None = Field(default=None, max_length=300)
    filename: str | None = Field(default=None, max_length=120)
    text: str | None = Field(default=None, max_length=inputs.MAX_PASTE)


def _db(request: Request):
    conn = connect(request.app.state.settings.db_path)
    try:
        runs.ensure_schema(conn)
        yield conn
    finally:
        conn.close()


def _ip_hash(request: Request, conn) -> str:
    return runs.STORE.visitor_hash(conn, client_ip(request, request.app.state.settings.trust_proxy))


@router.get("/agent", response_class=HTMLResponse)
def agent_page(request: Request, conn=Depends(_db)):
    from web.app import render
    cfg = request.app.state.agent_config
    available = cfg.enabled and runs.is_enabled(conn) and runs.day_spend(conn, time.time()) < cfg.daily_usd
    showcases = [{**s, "input": json.loads(s["input"])} for s in runs.showcases(conn)]
    return render(request, "radar_agent.html", showcases=showcases, available=available)


@router.post("/api/agent/runs")
def start_run(request: Request, body: StartRun, conn=Depends(_db)):
    if not same_origin(request):
        raise HTTPException(403, "cross-site request refused")
    now = time.time()
    try:
        if body.repo_url:
            url = body.repo_url.strip()
            inputs.parse_repo_url(url)
            payload, mode = {"repo_url": url}, "repo"
        elif body.filename and body.text:
            inputs.from_paste(body.filename, body.text)
            payload, mode = {"filename": body.filename, "text": body.text}, "paste"
        else:
            raise inputs.InputError("Enter a GitHub repository URL or paste a dependency file.")
        runs.expire(conn, now)                     # the 30-day retention holds without waiting for a restart
        rid = runs.create_checked(conn, request.app.state.agent_config, payload, mode, _ip_hash(request, conn), now)
    except inputs.InputError as exc:
        raise HTTPException(400, str(exc))
    except runs.LimitError as exc:
        raise HTTPException(exc.status, str(exc))
    request.app.state.agent_runner.start(rid)
    return {"id": rid}


def _run_or_404(conn, run_id: str) -> dict:
    run = runs.get_run(conn, run_id) if RUN_ID.fullmatch(run_id) else None
    if run is None:
        raise HTTPException(404)
    return run


async def event_generator(db_path: str, run_id: str, last: int):
    """Follow a run (shared implementation in core.agent.stream)."""
    async for chunk in follow(db_path, runs.STORE, run_id, last, STREAM_SECONDS):
        yield chunk


@router.get("/api/agent/runs/{run_id}/events")
async def stream(run_id: str, request: Request, after: int = 0):
    db_path = request.app.state.settings.db_path
    conn = connect(db_path)
    try:
        runs.ensure_schema(conn)
        _run_or_404(conn, run_id)
    finally:
        conn.close()
    header = request.headers.get("last-event-id", "")
    last = max(after, int(header)) if header.isdigit() else after
    return stream_response(event_generator(db_path, run_id, last))


@router.get("/api/agent/runs/{run_id}/all")
def all_events(run_id: str, conn=Depends(_db)):
    _run_or_404(conn, run_id)
    return runs.events(conn, run_id)


@router.get("/agent/runs/{run_id}", response_class=HTMLResponse)
def run_page(run_id: str, request: Request, conn=Depends(_db)):
    from web.app import render
    run = _run_or_404(conn, run_id)
    response = render(request, "radar_agent_run.html", run=run)
    response.headers["X-Robots-Tag"] = "noindex"
    return response


@router.get("/agent/runs/{run_id}/patch.diff", response_class=PlainTextResponse)
def patch(run_id: str, conn=Depends(_db)):
    run = _run_or_404(conn, run_id)
    return PlainTextResponse(((run.get("result") or {}).get("report") or {}).get("patch") or "",
                             headers={"Content-Disposition": f'attachment; filename="upgrade-{run_id}.diff"'})


@router.get("/agent/runs/{run_id}/issue.md", response_class=PlainTextResponse)
def issue(run_id: str, conn=Depends(_db)):
    run = _run_or_404(conn, run_id)
    return PlainTextResponse(((run.get("result") or {}).get("report") or {}).get("issue_markdown") or "",
                             headers={"Content-Disposition": f'attachment; filename="upgrade-{run_id}.md"'})
