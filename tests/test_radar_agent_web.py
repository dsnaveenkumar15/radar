# tests/test_radar_agent_web.py
import json

import pytest
from fastapi.testclient import TestClient

from radar.agent import runs
from tests.webutil import make_settings
from web.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_ENABLED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("AGENT_FAST_MODELS", "f")
    monkeypatch.setenv("AGENT_STRONG_MODELS", "s")
    app = create_app(make_settings(tmp_path))
    started = []
    app.state.agent_runner.start = lambda rid: started.append(rid)          # no threads in tests
    c = TestClient(app, base_url="http://radar.localhost")
    c.started = started
    return c


def test_page_renders_form_and_run_page_renders_the_map(client):
    from core.db import connect
    r = client.get("/agent")
    assert r.status_code == 200 and 'id="agent-form"' in r.text and "Upgrade agent" in r.text
    conn = connect(client.app.state.settings.db_path)
    runs.ensure_schema(conn)
    rid = runs.create_run(conn, {"repo_url": "https://github.com/o/r"}, "repo", "x", 1.0)
    page = client.get(f"/agent/runs/{rid}")
    assert page.status_code == 200 and 'id="agent-map"' in page.text and "o/r" in page.text
    assert 'name="robots" content="noindex"' in page.text and page.headers.get("x-robots-tag") == "noindex"


def test_start_run_validates_input_and_enforces_one_at_a_time(client):
    h = {"origin": "http://radar.localhost"}
    assert client.post("/api/agent/runs", json={"repo_url": "https://gitlab.com/a/b"}, headers=h).status_code == 400
    r = client.post("/api/agent/runs", json={"repo_url": "https://github.com/o/r"}, headers=h)
    assert r.status_code == 200 and client.started == [r.json()["id"]]
    again = client.post("/api/agent/runs", json={"repo_url": "https://github.com/o/r"}, headers=h)
    assert again.status_code == 429 and "one run at a time" in again.json()["detail"]


def test_cross_site_posts_are_refused(client):
    assert client.post("/api/agent/runs", json={"repo_url": "https://github.com/o/r"},
                       headers={"origin": "https://evil.example"}).status_code == 403


def test_event_stream_resumes_after_an_id_and_ends(client):
    from core.db import connect
    conn = connect(client.app.state.settings.db_path)
    runs.ensure_schema(conn)
    rid = runs.create_run(conn, {}, "repo", "x", 1.0)
    first = runs.add_event(conn, rid, "node", {"name": "fetch_inputs", "status": "start"}, 1.0)
    runs.add_event(conn, rid, "result", {"report": {}}, 1.0)
    runs.finish(conn, rid, "done", {}, None, 2.0)
    body = client.get(f"/api/agent/runs/{rid}/events?after={first}").text
    assert '"type": "result"' in body and '"fetch_inputs"' not in body and "event: end" in body
    assert [e["type"] for e in client.get(f"/api/agent/runs/{rid}/all").json()] == ["node", "result"]


def test_downloads_and_unknown_runs(client):
    from core.db import connect
    conn = connect(client.app.state.settings.db_path)
    runs.ensure_schema(conn)
    rid = runs.create_run(conn, {}, "repo", "x", 1.0)
    runs.finish(conn, rid, "done", {"report": {"patch": "--- a/x\n+++ b/x\n", "issue_markdown": "## Upgrade checklist"}}, None, 2.0)
    assert client.get(f"/agent/runs/{rid}/patch.diff").text.startswith("--- a/x")
    assert "Upgrade checklist" in client.get(f"/agent/runs/{rid}/issue.md").text
    assert client.get("/agent/runs/nope").status_code == 404


def test_admin_toggle_turns_live_runs_off_and_blocks_new_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_ENABLED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("AGENT_FAST_MODELS", "f")
    app = create_app(make_settings(tmp_path, admin_user="owner", admin_password="correct-horse-battery-staple"))
    app.state.agent_runner.start = lambda rid: None
    c = TestClient(app, base_url="http://radar.localhost")
    auth, h = ("owner", "correct-horse-battery-staple"), {"origin": "http://radar.localhost"}
    assert "live runs on" in c.get("/admin", auth=auth).text
    assert c.post("/admin/agent/toggle", auth=auth, headers=h, follow_redirects=False).status_code == 303
    assert "live runs OFF" in c.get("/admin", auth=auth).text
    r = c.post("/api/agent/runs", json={"repo_url": "https://github.com/o/r"}, headers=h)
    assert r.status_code == 403


def _seed_run(client, status="running"):
    from core.db import connect
    conn = connect(client.app.state.settings.db_path)
    runs.ensure_schema(conn)
    rid = runs.create_run(conn, {}, "repo", "x", 1.0)
    ids = [runs.add_event(conn, rid, "info", {"text": f"e{i}"}, 1.0) for i in range(3)]
    if status != "running":
        runs.finish(conn, rid, status, {}, None, 2.0)
    return conn, rid, ids


def test_last_event_id_header_resumes_the_stream(client):
    _, rid, ids = _seed_run(client, "done")
    body = client.get(f"/api/agent/runs/{rid}/events", headers={"last-event-id": str(ids[1])}).text
    assert '"e2"' in body and '"e1"' not in body and '"e0"' not in body and "event: end" in body


def test_a_stream_that_outlives_its_window_closes_without_an_end_event_so_the_browser_reconnects(client, monkeypatch):
    from web import radar_agent
    monkeypatch.setattr(radar_agent, "STREAM_SECONDS", 0.3)
    _, rid, _ = _seed_run(client, "running")
    body = client.get(f"/api/agent/runs/{rid}/events").text
    assert '"e0"' in body and "event: end" not in body


def test_streams_do_not_hold_worker_threads_while_idle(client, monkeypatch):
    import inspect

    from web import radar_agent
    assert inspect.isasyncgenfunction(radar_agent.event_generator)       # sleeping happens on the event loop, not in the pool
