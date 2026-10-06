# tests/test_radar_agent_runs.py
import pytest

from radar.agent import runs
from radar.agent.config import AgentConfig

NOW = 1_800_000_000.0


def test_events_are_ordered_and_resumable(conn):
    runs.ensure_schema(conn)
    rid = runs.create_run(conn, {"repo_url": "x"}, "repo", "ip1", NOW)
    a = runs.add_event(conn, rid, "node", {"name": "fetch_inputs"}, NOW)
    runs.add_event(conn, rid, "info", {"text": "t"}, NOW)
    assert [e["type"] for e in runs.events(conn, rid)] == ["node", "info"]
    assert [e["type"] for e in runs.events(conn, rid, after=a)] == ["info"]


def test_spend_and_limits(conn):
    runs.ensure_schema(conn)
    cfg = AgentConfig(enabled=True, api_key="k", per_ip_day=3, concurrent=2, queue=3, daily_usd=1.0)
    for _ in range(3):
        rid = runs.create_run(conn, {}, "repo", "ip1", NOW)
        runs.finish(conn, rid, "done", {}, None, NOW)
    with pytest.raises(runs.LimitError) as e:
        runs.check_limits(conn, cfg, "ip1", NOW)
    assert e.value.status == 429
    rid = runs.create_run(conn, {}, "repo", "ip2", NOW)                     # still running
    with pytest.raises(runs.LimitError) as e:
        runs.check_limits(conn, cfg, "ip2", NOW)
    assert "one run at a time" in str(e.value)
    runs.add_usage(conn, rid, 1.2, 1000)
    assert runs.day_spend(conn, NOW) == pytest.approx(1.2)
    with pytest.raises(runs.LimitError) as e:
        runs.check_limits(conn, cfg, "ip3", NOW)
    assert e.value.status == 503


def test_kill_switch_and_disabled_config(conn):
    runs.ensure_schema(conn)
    cfg = AgentConfig(enabled=True, api_key="k")
    runs.set_enabled(conn, False)
    with pytest.raises(runs.LimitError) as e:
        runs.check_limits(conn, cfg, "ip", NOW)
    assert e.value.status == 403
    runs.set_enabled(conn, True)
    runs.check_limits(conn, cfg, "ip", NOW)
    with pytest.raises(runs.LimitError):
        runs.check_limits(conn, AgentConfig(enabled=False), "ip", NOW)


def test_restart_marks_running_runs_failed_and_old_runs_expire(conn):
    runs.ensure_schema(conn)
    live = runs.create_run(conn, {}, "repo", "ip", NOW)
    old = runs.create_run(conn, {}, "repo", "ip", NOW - 31 * 86400)
    keep = runs.create_run(conn, {}, "repo", "ip", NOW - 31 * 86400, showcase=True)
    runs.interrupt_running(conn, NOW)
    assert runs.get_run(conn, live)["status"] == "failed"
    runs.expire(conn, NOW)
    assert runs.get_run(conn, old) is None and runs.get_run(conn, keep) is not None


def test_check_and_create_is_atomic_across_connections(tmp_path):
    import threading

    from core.db import connect
    path = str(tmp_path / "a.db")
    c0 = connect(path)
    runs.ensure_schema(c0)
    cfg = AgentConfig(enabled=True, api_key="k", per_ip_day=3, concurrent=2, queue=3)
    results = []

    def attempt():
        c = connect(path)
        try:
            results.append(runs.create_checked(c, cfg, {"repo_url": "x"}, "repo", "ip1", NOW))
        except runs.LimitError as exc:
            results.append(exc)
        finally:
            c.close()

    threads = [threading.Thread(target=attempt) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(isinstance(r, str) for r in results) == 1          # one run at a time per visitor
    assert sum(isinstance(r, runs.LimitError) for r in results) == 5


def test_runs_start_queued_and_become_running(conn):
    runs.ensure_schema(conn)
    rid = runs.create_run(conn, {}, "repo", "ip", NOW)
    assert runs.get_run(conn, rid)["status"] == "queued" and runs.active_for(conn, "ip") == 1
    runs.set_running(conn, rid)
    assert runs.get_run(conn, rid)["status"] == "running"


def test_interrupted_runs_get_a_visible_message(conn):
    runs.ensure_schema(conn)
    rid = runs.create_run(conn, {}, "repo", "ip", NOW)
    runs.interrupt_running(conn, NOW)
    assert [e["type"] for e in runs.events(conn, rid)] == ["error"] and runs.status_of(conn, rid) == "failed"
