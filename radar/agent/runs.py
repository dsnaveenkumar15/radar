# radar/agent/runs.py
"""Runs and their event log in SQLite: the live trace, shareable run pages and replays all read from here."""
import json
import secrets
import threading
import time
from datetime import datetime, timezone

from core.db import connect, tx

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs(id TEXT PRIMARY KEY, created_at REAL NOT NULL, input TEXT NOT NULL, mode TEXT NOT NULL,
  ip_hash TEXT NOT NULL, status TEXT NOT NULL, showcase INTEGER NOT NULL DEFAULT 0, cost_usd REAL NOT NULL DEFAULT 0,
  tokens INTEGER NOT NULL DEFAULT 0, result TEXT, error TEXT, finished_at REAL);
CREATE INDEX IF NOT EXISTS agent_runs_created ON agent_runs(created_at);
CREATE TABLE IF NOT EXISTS agent_events(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, at REAL NOT NULL,
  type TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS agent_events_run ON agent_events(run_id, id);
CREATE TABLE IF NOT EXISTS agent_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
KEEP_DAYS = 30


class LimitError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def _midnight(now: float) -> float:
    d = datetime.fromtimestamp(now, timezone.utc)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()


def create_run(conn, input: dict, mode: str, ip_hash: str, now: float, showcase: bool = False) -> str:
    rid = secrets.token_hex(8)
    conn.execute("INSERT INTO agent_runs(id, created_at, input, mode, ip_hash, status, showcase) VALUES (?,?,?,?,?,?,?)",
                 (rid, now, json.dumps(input), mode, ip_hash, "queued", int(showcase)))
    return rid


def create_checked(conn, cfg, input: dict, mode: str, ip_hash: str, now: float) -> str:
    """Limit check and run creation under one write lock, so simultaneous posts cannot both pass the limits."""
    with tx(conn):
        check_limits(conn, cfg, ip_hash, now)
        return create_run(conn, input, mode, ip_hash, now)


def set_running(conn, run_id: str) -> None:
    conn.execute("UPDATE agent_runs SET status='running' WHERE id=? AND status='queued'", (run_id,))


def add_event(conn, run_id: str, type: str, payload: dict, now: float) -> int:
    return conn.execute("INSERT INTO agent_events(run_id, at, type, payload) VALUES (?,?,?,?)",
                        (run_id, now, type, json.dumps(payload, default=str))).lastrowid


def events(conn, run_id: str, after: int = 0) -> list[dict]:
    return [{"id": r["id"], "type": r["type"], "payload": json.loads(r["payload"]), "at": r["at"]}
            for r in conn.execute("SELECT id, type, payload, at FROM agent_events WHERE run_id=? AND id>? ORDER BY id",
                                  (run_id, after))]


def add_usage(conn, run_id: str, usd: float, tokens: int) -> None:
    conn.execute("UPDATE agent_runs SET cost_usd=cost_usd+?, tokens=tokens+? WHERE id=?", (usd, tokens, run_id))


def day_spend(conn, now: float) -> float:
    return float(conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM agent_runs WHERE created_at>=?", (_midnight(now),)).fetchone()[0])


def runs_today(conn, ip_hash: str, now: float) -> int:
    return conn.execute("SELECT count(*) FROM agent_runs WHERE ip_hash=? AND created_at>=? AND showcase=0",
                        (ip_hash, _midnight(now))).fetchone()[0]


def active_for(conn, ip_hash: str) -> int:
    return conn.execute("SELECT count(*) FROM agent_runs WHERE ip_hash=? AND status IN ('queued','running')", (ip_hash,)).fetchone()[0]


def active_count(conn) -> int:
    return conn.execute("SELECT count(*) FROM agent_runs WHERE status IN ('queued','running')").fetchone()[0]


def finish(conn, run_id: str, status: str, result, error, now: float) -> None:
    conn.execute("UPDATE agent_runs SET status=?, result=?, error=?, finished_at=? WHERE id=?",
                 (status, json.dumps(result) if result is not None else None, error, now, run_id))


def status_of(conn, run_id: str) -> str:
    r = conn.execute("SELECT status FROM agent_runs WHERE id=?", (run_id,)).fetchone()
    return r["status"] if r else "failed"


def get_run(conn, run_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
    if r is None:
        return None
    d = dict(r)
    d["input"] = json.loads(d["input"])
    d["result"] = json.loads(d["result"]) if d["result"] else None
    return d


def showcases(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT id, input, cost_usd, tokens, created_at FROM agent_runs "
                                          "WHERE showcase=1 AND status='done' ORDER BY created_at DESC LIMIT 3")]


def expire(conn, now: float) -> None:
    cutoff = now - KEEP_DAYS * 86400
    conn.execute("DELETE FROM agent_events WHERE run_id IN (SELECT id FROM agent_runs WHERE showcase=0 AND created_at<?)", (cutoff,))
    conn.execute("DELETE FROM agent_runs WHERE showcase=0 AND created_at<?", (cutoff,))


def interrupt_running(conn, now: float) -> None:
    for r in conn.execute("SELECT id FROM agent_runs WHERE status IN ('queued','running')").fetchall():
        add_event(conn, r["id"], "error", {"text": "The server restarted during this run, so it was stopped. Please run it again."}, now)
    conn.execute("UPDATE agent_runs SET status='failed', error='the server restarted during the run', finished_at=? "
                 "WHERE status IN ('queued','running')", (now,))


def set_enabled(conn, enabled: bool) -> None:
    conn.execute("INSERT INTO agent_settings(key, value) VALUES ('enabled', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 ("1" if enabled else "0",))


def is_enabled(conn) -> bool:
    r = conn.execute("SELECT value FROM agent_settings WHERE key='enabled'").fetchone()
    return r is None or r["value"] == "1"


def check_limits(conn, cfg, ip_hash: str, now: float) -> None:
    if not cfg.enabled or not is_enabled(conn):
        raise LimitError(403, "Live runs are switched off right now. Watch a recorded run instead.")
    if day_spend(conn, now) >= cfg.daily_usd:
        raise LimitError(503, "Today's live-run budget is used up. Live runs resume at 00:00 UTC; recorded runs are available.")
    if active_for(conn, ip_hash) > 0:
        raise LimitError(429, "You already have a run in progress: one run at a time.")
    if runs_today(conn, ip_hash, now) >= cfg.per_ip_day:
        raise LimitError(429, f"You have used your {cfg.per_ip_day} live runs for today.")
    if active_count(conn) >= cfg.concurrent + cfg.queue:
        raise LimitError(503, "The agent is busy. Try again in a minute, or watch a recorded run.")


class Runner:
    """Runs agents on background threads, at most `cfg.concurrent` at once; the rest wait (status queued)."""

    def __init__(self, db_path: str, cfg, make_context):
        self.db_path, self.cfg, self.make_context = db_path, cfg, make_context
        self.slots = threading.BoundedSemaphore(cfg.concurrent)

    def start(self, run_id: str) -> None:
        threading.Thread(target=self._run, args=(run_id,), name=f"agent-{run_id}", daemon=True).start()

    def _run(self, run_id: str) -> None:
        from radar.agent.graph import run_graph
        conn = connect(self.db_path)
        try:
            with self.slots:
                set_running(conn, run_id)
                emit = lambda type, payload: add_event(conn, run_id, type, payload, time.time())
                ctx = self.make_context(conn, run_id, emit)
                state = run_graph(ctx, run_id)
                finish(conn, run_id, "done", {"report": state.get("report"), "paths": state.get("paths")}, None, time.time())
        except Exception as exc:                          # visitor-facing message; details stay in the server log
            from radar.agent.inputs import InputError
            text = str(exc) if isinstance(exc, (InputError, ValueError)) else "The run failed. Please try again later."
            add_event(conn, run_id, "error", {"text": text}, time.time())
            finish(conn, run_id, "failed", None, repr(exc)[:2000], time.time())
        finally:
            conn.close()
