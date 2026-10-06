# radar/agent/context.py (new; keeps web/app.py thin)
"""Builds the per-run Context: input loader, published data, gateway with budgets wired to the run store."""
import json
import os
import time

from core.github import GitHub
from radar.agent import inputs, runs
from radar.agent.code_index import module_roots
from radar.agent.gateway import Budget, BudgetExceeded, Gateway, chat_factory
from radar.agent.graph import Context


def make_context_factory(app, settings):
    def make(conn, run_id, emit):
        cfg = app.state.agent_config
        run = runs.get_run(conn, run_id)
        data = app.state.radar_data

        def load_input():
            src = run["input"]
            if run["mode"] == "repo":
                return inputs.from_repo(GitHub(conn, token=os.environ.get("GITHUB_TOKEN") or None), src["repo_url"])
            return inputs.from_paste(src["filename"], src["text"])

        totals = {"usd": 0.0, "tokens": 0}

        def on_usage(model, provider, usd, tokens):
            totals["usd"] += usd
            totals["tokens"] += tokens
            runs.add_usage(conn, run_id, usd, tokens)
            emit("usage", {"model": model, "provider": provider, "usd": round(usd, 6), "tokens": tokens,
                           "total_usd": round(totals["usd"], 6), "total_tokens": totals["tokens"]})

        def day_guard():
            if runs.day_spend(conn, time.time()) >= cfg.daily_usd:
                raise BudgetExceeded("daily budget reached")

        budget = Budget(cfg.run_usd, cfg.run_tokens, cfg.run_tool_calls, time.monotonic() + cfg.run_seconds)
        gateway = Gateway({"fast": cfg.fast_models, "strong": cfg.strong_models or cfg.fast_models},
                          app.state.agent_factory if hasattr(app.state, "agent_factory") else chat_factory(cfg),
                          cfg.prices, budget, on_usage, day_guard)
        return Context(load_input=load_input, api_changes=data.api_changes(), upgrade=data.upgrade(),
                       roots=module_roots(app.state.radar_sources), gateway=gateway, emit=emit,
                       max_candidates=cfg.max_candidates)
    return make
