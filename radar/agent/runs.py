# radar/agent/runs.py
"""Radar's upgrade-agent runs: the shared run store under the 'agent' table prefix (agent_runs, agent_events,
agent_settings, unchanged), kept 30 days, and a runner that executes the upgrade graph."""
from core.agent.runs import LimitError, RunStore  # noqa: F401
from core.agent.runs import Runner as _Runner

KEEP_DAYS = 30
STORE = RunStore("agent", KEEP_DAYS)

ensure_schema = STORE.ensure_schema
create_run = STORE.create_run
create_checked = STORE.create_checked
set_running = STORE.set_running
add_event = STORE.add_event
events = STORE.events
add_usage = STORE.add_usage
day_spend = STORE.day_spend
runs_today = STORE.runs_today
active_for = STORE.active_for
active_count = STORE.active_count
finish = STORE.finish
status_of = STORE.status_of
get_run = STORE.get_run
showcases = STORE.showcases
expire = STORE.expire
interrupt_running = STORE.interrupt_running
set_enabled = STORE.set_enabled
is_enabled = STORE.is_enabled
check_limits = STORE.check_limits


def _error_text(exc: Exception) -> str:
    from radar.agent.inputs import InputError
    return str(exc) if isinstance(exc, (InputError, ValueError)) else "The run failed. Please try again later."


class Runner(_Runner):
    """Runs the upgrade graph; same constructor as before the move."""

    def __init__(self, db_path: str, cfg, make_context):
        super().__init__(db_path, cfg, STORE, self._work, _error_text)
        self.make_context = make_context

    def _work(self, conn, run_id, emit):
        from radar.agent.graph import run_graph
        state = run_graph(self.make_context(conn, run_id, emit), run_id)
        return "done", {"report": state.get("report"), "paths": state.get("paths")}
