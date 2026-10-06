import time

from core.db import connect
from radar.agent import runs
from radar.agent.config import AgentConfig
from radar.agent.inputs import InputError
from tests.radar_agent_fakes import ai
from tests.test_radar_agent_graph import API, FILES, UP, call, ctx


def _wired(context, emit):
    context.emit = emit
    return context


def _wait(conn, rid, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        status = runs.get_run(conn, rid)["status"]
        if status not in ("queued", "running"):
            return status
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_runner_runs_the_graph_stores_events_and_finishes(tmp_path):
    db = str(tmp_path / "r.db")
    conn = connect(db)
    runs.ensure_schema(conn)
    rid = runs.create_run(conn, {"repo_url": "https://github.com/o/r"}, "repo", "ip", time.time())
    script = {"s": [ai(tool_calls=[call("SubmitPlan", {"order": [{"id": "c1", "reason": "r"}]})]),
                    ai(tool_calls=[call("ProposeChanges", {"summary": "done", "changes": []})])],
              "f": [ai(tool_calls=[call("SubmitVerdict", {"verdict": "confirmed", "explanation": "removed",
                                                           "evidence": [{"path": "app/main.py", "line": 2, "quote": "t = x.old()"}]})])]}
    runner = runs.Runner(db, AgentConfig(enabled=True, api_key="k", concurrent=1), lambda c, run_id, emit: _wired(ctx(script, []), emit))
    runner.start(rid)
    assert _wait(conn, rid) == "done"
    run = runs.get_run(conn, rid)
    types = [e["type"] for e in runs.events(conn, rid)]
    assert "result" in types and run["result"]["report"]["summary"] and run["finished_at"]


def test_runner_failure_is_recorded_with_a_friendly_message_and_no_internals(tmp_path):
    db = str(tmp_path / "r.db")
    conn = connect(db)
    runs.ensure_schema(conn)
    rid = runs.create_run(conn, {}, "repo", "ip", time.time())
    rid2 = runs.create_run(conn, {}, "repo", "ip2", time.time())

    def boom(c, run_id, emit):
        raise RuntimeError("secret internal path /opt/x and key sk-or-123") if run_id == rid else InputError("Repository not found or private.")

    runner = runs.Runner(db, AgentConfig(enabled=True, api_key="k", concurrent=2), boom)
    runner.start(rid)
    runner.start(rid2)
    assert _wait(conn, rid) == "failed" and _wait(conn, rid2) == "failed"
    shown = [e["payload"]["text"] for r in (rid, rid2) for e in runs.events(conn, r) if e["type"] == "error"]
    assert shown == ["The run failed. Please try again later.", "Repository not found or private."] or \
        sorted(shown) == sorted(["The run failed. Please try again later.", "Repository not found or private."])
    assert all("sk-or" not in t and "/opt" not in t for t in shown)


def test_production_context_wires_models_by_run_mode_and_records_usage(tmp_path, monkeypatch):
    from fastapi import FastAPI

    from radar.agent.context import make_context_factory
    monkeypatch.setenv("AGENT_FAST_MODELS", "paid")
    monkeypatch.setenv("AGENT_PUBLIC_MODELS", "free")
    from radar.agent.config import load_config
    from web.radar_site import RadarData
    app = FastAPI()
    app.state.agent_config = load_config({"AGENT_ENABLED": "1", "OPENROUTER_API_KEY": "k", "AGENT_FAST_MODELS": "paid", "AGENT_PUBLIC_MODELS": "free"})
    app.state.radar_data = RadarData(tmp_path)
    app.state.radar_sources = []
    conn = connect(str(tmp_path / "c.db"))
    runs.ensure_schema(conn)
    repo_run = runs.create_run(conn, {"repo_url": "https://github.com/o/r"}, "repo", "ip", time.time())
    paste_run = runs.create_run(conn, {"filename": "requirements.txt", "text": "x==1"}, "paste", "ip2", time.time())
    make = make_context_factory(app, None)
    events = []
    c1 = make(conn, repo_run, lambda t, p: events.append((t, p)))
    c2 = make(conn, paste_run, lambda t, p: None)
    assert c1.gateway.models_by_role["fast"] == ["free", "paid"] and c2.gateway.models_by_role["fast"] == ["paid"]
    c1.gateway.on_usage("paid", "p", 0.002, 100)                       # usage reaches the run row and the event log
    assert runs.get_run(conn, repo_run)["cost_usd"] == 0.002 and events[-1][0] == "usage"
