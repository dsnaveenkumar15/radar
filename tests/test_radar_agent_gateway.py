# tests/test_radar_agent_gateway.py
import time

import pytest

from radar.agent.config import load_config
from radar.agent.gateway import Budget, BudgetExceeded, Gateway
from tests.radar_agent_fakes import ai, scripted_factory


def gw(script, budget=None, day_guard=lambda: None, usage=None):
    usage = usage if usage is not None else []
    return Gateway({"fast": ["m1", "m2"], "strong": ["s1"]}, scripted_factory(script),
                   {"m1": (1.0, 4.0), "m2": (2.0, 8.0), "s1": (3.0, 15.0)},
                   budget or Budget(0.05, 60_000, 30, time.monotonic() + 120),
                   on_usage=lambda *a: usage.append(a), day_guard=day_guard), usage


def test_cost_from_provider_when_given_else_from_prices():
    g, usage = gw({"m1": [ai("a", cost=0.002), ai("b", tokens=(1_000, 1_000))]})
    g.invoke(["hi"]); g.invoke(["hi"])
    assert usage[0][2] == 0.002 and usage[1][2] == pytest.approx(1_000 * 1 / 1e6 + 1_000 * 4 / 1e6)
    assert g.budget.tokens == 2_120


def test_backup_model_when_the_primary_fails():
    g, usage = gw({"m1": [RuntimeError("provider down")], "m2": [ai("ok")]})
    assert g.invoke(["hi"]).content == "ok" and usage[0][0] == "m2"


def test_budget_and_daily_cap_stop_calls_before_they_are_made():
    g, _ = gw({"m1": [ai("a", cost=0.06)]})
    g.invoke(["hi"])
    with pytest.raises(BudgetExceeded):
        g.invoke(["hi"])
    def day():
        raise BudgetExceeded("daily cap reached")
    g2, _ = gw({"m1": [ai("a")]}, day_guard=day)
    with pytest.raises(BudgetExceeded):
        g2.invoke(["hi"])


def test_config_from_env():
    cfg = load_config({"AGENT_ENABLED": "1", "OPENROUTER_API_KEY": "k", "AGENT_FAST_MODELS": "a, b",
                       "AGENT_STRONG_MODELS": "c", "AGENT_PRICES": "a=0.3/1.1;c=0.5/2"})
    assert cfg.enabled and cfg.fast_models == ["a", "b"] and cfg.prices["a"] == (0.3, 1.1) and cfg.daily_usd == 1.0
    assert not load_config({}).enabled


def test_budget_exceeded_from_a_model_propagates_and_failures_do_not_echo_provider_text():
    g, _ = gw({"m1": [BudgetExceeded("daily")]})
    with pytest.raises(BudgetExceeded):
        g.invoke(["hi"])
    g2, _ = gw({"m1": [RuntimeError("sk-or-secret in url")], "m2": [RuntimeError("boom")]})
    with pytest.raises(RuntimeError) as e:
        g2.invoke(["hi"])
    assert "sk-or-secret" not in str(e.value) and "RuntimeError" in str(e.value)


def test_a_reply_without_tool_calls_is_a_normal_result():
    g, usage = gw({"m1": [ai("just text")]})
    assert g.invoke(["hi"], tools=[dict]).tool_calls == [] and len(usage) == 1


def test_unknown_cost_is_charged_a_conservative_default_so_the_daily_cap_cannot_fail_open():
    g, usage = gw({"m1": [ai("a", tokens=(1_000_000, 1_000_000))]})
    g.prices = {}                                              # model missing from the price table, provider sent no cost
    g.invoke(["hi"])
    assert usage[0][2] > 0


def test_enabled_requires_at_least_one_model():
    assert not load_config({"AGENT_ENABLED": "1", "OPENROUTER_API_KEY": "k"}).enabled
    assert load_config({"AGENT_ENABLED": "1", "OPENROUTER_API_KEY": "k", "AGENT_FAST_MODELS": "a"}).enabled


def test_total_failures_are_counted_so_callers_can_say_the_model_service_is_down():
    g, _ = gw({"m1": [RuntimeError("401")], "m2": [RuntimeError("401")]})
    with pytest.raises(RuntimeError):
        g.invoke(["hi"])
    assert g.failures == 1


def test_public_models_are_used_first_only_for_public_repo_runs():
    from radar.agent.config import models_for
    cfg = load_config({"AGENT_ENABLED": "1", "OPENROUTER_API_KEY": "k", "AGENT_FAST_MODELS": "paid1,paid2",
                       "AGENT_STRONG_MODELS": "strong1", "AGENT_PUBLIC_MODELS": "free1"})
    assert models_for(cfg, "repo") == {"fast": ["free1", "paid1", "paid2"], "strong": ["strong1"]}
    assert models_for(cfg, "paste") == {"fast": ["paid1", "paid2"], "strong": ["strong1"]}      # pasted files may be private
    assert models_for(load_config({"AGENT_ENABLED": "1", "OPENROUTER_API_KEY": "k", "AGENT_FAST_MODELS": "a"}), "repo") == {"fast": ["a"], "strong": ["a"]}


def test_only_public_models_may_use_providers_that_keep_data():
    from radar.agent.gateway import chat_factory
    cfg = load_config({"AGENT_ENABLED": "1", "OPENROUTER_API_KEY": "k", "AGENT_FAST_MODELS": "paid1", "AGENT_PUBLIC_MODELS": "free1"})
    make = chat_factory(cfg)
    assert make("free1").extra_body["provider"]["data_collection"] == "allow"
    assert make("paid1").extra_body["provider"]["data_collection"] == "deny"
