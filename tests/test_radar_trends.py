import json

import pytest

from core.llm import AuthExpired, FakeBackend, QuotaDeferred
from core.publish import load_current, publish
from radar import store
from radar.config import Entity, Registry
from radar.publishing import DATASET, build_files
from radar.trends import (TREND_DATASET, iso_week, previous_week, publish_trends, stages, validate_trends, week_stats)
from tests.test_radar_site import NOW, REGISTRY, cluster, digest

REG = Registry([Entity("mcp", "MCP", "protocol", (), True), Entity("langgraph", "LangGraph", "framework", ("lang graph",), True),
                Entity("crewai", "CrewAI", "framework", (), True)])


def syn(entities, impact="notable"):
    return {"what_changed": "x", "affects": [], "impact": impact, "migration_effort": "low", "action": "a", "citations": ["i1"],
            "quote": None, "versions": [], "entities": entities, "migration": None}


def d(date, *clusters):
    return digest(date, clusters=list(clusters))


def c(cid, entities, impact="notable", score=50):
    return cluster(cid, impact, synthesis=syn(entities, impact), signal={"score": score})


THIS = [d("2026-09-28", c("c1", ["MCP"]), c("c2", ["LangGraph"], "breaking")), d("2026-09-29", c("c1", ["MCP"]), c("c3", ["lang graph"]))]
LAST = [d("2026-09-21", c("c1", ["MCP"]), c("c2", ["CrewAI"]), c("c3", ["CrewAI"]))]


def test_iso_weeks_and_the_previous_week_in_ist():
    assert iso_week("2026-09-28") == "2026-W40" and iso_week("2026-10-04") == "2026-W40" and iso_week("2026-10-05") == "2026-W41"
    assert iso_week("2026-12-31") == "2026-W53" and iso_week("2027-01-01") == "2026-W53"
    assert previous_week(1_790_877_600.0) == "2026-W39"                      # Thu 2026-10-01 -> the week before W40


def test_week_stats_count_clusters_per_resolved_entity_against_last_week():
    s = week_stats(THIS, LAST, REG)
    by = {e["id"]: e for e in s["entities"]}
    assert by["mcp"] == {"id": "mcp", "name": "MCP", "this": 2, "last": 1, "breaking": 0}
    assert by["langgraph"]["this"] == 2 and by["langgraph"]["breaking"] == 1       # alias "lang graph" resolves
    assert by["crewai"]["this"] == 0 and by["crewai"]["last"] == 2
    assert s["totals"] == {"digests": 2, "clusters": 4, "breaking": 1, "last_clusters": 3}
    assert [e["id"] for e in s["entities"]][:2] == ["langgraph", "mcp"] or s["entities"][0]["this"] >= s["entities"][-1]["this"]
    assert {x["id"] for x in s["clusters"]} == {"2026-09-28/c1", "2026-09-28/c2", "2026-09-29/c1", "2026-09-29/c3"}


def test_odd_digests_and_unknown_entities_are_ignored():
    odd = [{"date": "2026-09-28", "clusters": ["x", {"id": "c9", "synthesis": None}, {"id": "c8", "synthesis": {"entities": ["Nobody", 3, None]}}]}, {}]
    s = week_stats(odd, [], REG)
    assert s["entities"] == [] and s["totals"]["clusters"] == 2


def stats():
    return week_stats(THIS, LAST, REG)


def note(**over):
    base = {"summary": "MCP coverage grew.", "rising": [{"entity": "mcp", "why": "More clusters than last week."}],
            "falling": [{"entity": "crewai", "why": "No clusters this week."}],
            "patterns": [{"name": "Graph runtimes", "why": "LangGraph appears in both clusters.", "clusters": ["2026-09-28/c2", "2026-09-29/c3"]}]}
    return {**base, **over}


def test_a_faithful_note_survives_validation():
    from radar.trends import TrendNote
    accepted, report = validate_trends(TrendNote.model_validate(note()), stats())
    assert [r["entity"] for r in accepted["rising"]] == ["mcp"] and [r["entity"] for r in accepted["falling"]] == ["crewai"]
    assert accepted["patterns"][0]["clusters"] == ["2026-09-28/c2", "2026-09-29/c3"] and report["dropped"] == []


def test_lies_are_dropped_with_a_reason():
    from radar.trends import TrendNote
    bad = note(rising=[{"entity": "crewai", "why": "Big week."}, {"entity": "ghost", "why": "?"}, {"entity": "mcp", "why": "Up 300 percent."}],
               falling=[{"entity": "mcp", "why": "Down."}],
               patterns=[{"name": "One cite", "why": "w", "clusters": ["2026-09-28/c2"]}, {"name": "Ghost", "why": "w", "clusters": ["2026-09-28/c2", "2030-01-01/c9"]}],
               summary="Mentions rose 40 percent. Coverage grew.")
    accepted, report = validate_trends(TrendNote.model_validate(bad), stats())
    assert accepted["rising"] == [] and accepted["falling"] == [] and accepted["patterns"] == []
    assert accepted["summary"] == "Coverage grew."
    reasons = " ".join(r["reason"] for r in report["dropped"])
    for word in ("direction", "unknown entity", "number", "two clusters", "unknown cluster"):
        assert word in reasons, word


def test_numbers_the_code_supplied_are_allowed_in_text():
    from radar.trends import TrendNote
    ok = note(summary="MCP had 2 clusters against 1 the week before.")
    accepted, _ = validate_trends(TrendNote.model_validate(ok), stats())
    assert accepted["summary"].startswith("MCP had 2 clusters")


def write_digests(root, digests):
    files = {}
    for dig in digests:
        files = build_files(files, dig, [], REGISTRY, NOW)
    publish(root, "radar", DATASET, files, {})


class Job:
    payload = {"week": "2026-W40"}


def run_stages(db, tmp_path, backend):
    out = {}
    for name, fn in stages(db, backend=backend, registry=REG, data_root=tmp_path, clock=lambda: NOW):
        out[name] = fn(Job, out)
    return out


@pytest.fixture
def db(conn):
    store.ensure_schema(conn)
    return conn


def test_the_job_publishes_numbers_and_validated_commentary(db, tmp_path):
    write_digests(tmp_path, THIS + LAST)
    out = run_stages(db, tmp_path, FakeBackend([json.dumps(note())]))
    assert out["publish"]["published"] is True
    release = load_current(tmp_path, "radar", TREND_DATASET)
    doc = json.loads(release.read("weeks/2026-W40.json"))
    assert doc["synthesis"] == "complete" and doc["stats"]["totals"]["clusters"] == 4 and doc["commentary"]["rising"][0]["entity"] == "mcp"
    assert doc["provenance"]["prompt"] == "radar.trends@1"
    assert json.loads(release.read("index.json"))["weeks"][0]["week"] == "2026-W40"


@pytest.mark.parametrize("failure", [AuthExpired("login"), None])
def test_without_a_model_the_week_is_published_unsynthesized_with_correct_numbers(db, tmp_path, failure):
    write_digests(tmp_path, THIS + LAST)
    backend = FakeBackend([failure]) if failure else None
    out = run_stages(db, tmp_path, backend)
    doc = json.loads(load_current(tmp_path, "radar", TREND_DATASET).read("weeks/2026-W40.json"))
    assert doc["synthesis"] == "unsynthesized" and doc["commentary"] is None and doc["stats"]["totals"]["clusters"] == 4
    assert out["publish"]["published"] is True


def test_quota_deferral_propagates_so_the_worker_retries_later(db, tmp_path):
    write_digests(tmp_path, THIS + LAST)
    with pytest.raises(QuotaDeferred):
        run_stages(db, tmp_path, FakeBackend([QuotaDeferred("night window")]))
    assert load_current(tmp_path, "radar", TREND_DATASET) is None


def test_a_week_is_published_once_and_an_empty_week_not_at_all(db, tmp_path):
    write_digests(tmp_path, THIS + LAST)
    run_stages(db, tmp_path, None)
    again = run_stages(db, tmp_path, FakeBackend([]))
    assert again["publish"] == {"published": False, "reason": "week already published"}
    empty = tmp_path / "other"
    write_digests(empty, LAST)
    assert run_stages(db, empty, None)["publish"] == {"published": False, "reason": "no digests that week"}


def test_no_digest_release_at_all_is_not_an_error(db, tmp_path):
    assert run_stages(db, tmp_path, None)["publish"] == {"published": False, "reason": "no digests that week"}


def test_second_week_keeps_the_first_in_the_index(db, tmp_path):
    write_digests(tmp_path, THIS + LAST)
    run_stages(db, tmp_path, None)
    Job.payload = {"week": "2026-W39"}
    try:
        run_stages(db, tmp_path, None)
    finally:
        Job.payload = {"week": "2026-W40"}
    weeks = [w["week"] for w in json.loads(load_current(tmp_path, "radar", TREND_DATASET).read("index.json"))["weeks"]]
    assert weeks == ["2026-W40", "2026-W39"]


@pytest.mark.parametrize("text", ["MCP had 7 mentions.", "Mentions were up 12 percent.", "Mentions rose -4 overall.", "It hit 1e3.",
                                  "Seen on 28 September.", "Mentions rose to 2\u2070.", "Mentions tripled.", "A dozen new clusters appeared.",
                                  "Fifty percent more coverage.", "A ten-fold jump.", "Mentions doubled.", "About half of them."])
def test_numbers_and_number_words_the_counts_do_not_support_are_dropped(text):
    from radar.trends import TrendNote
    accepted, report = validate_trends(TrendNote.model_validate(note(summary=text + " Coverage was steady.")), stats())
    assert accepted["summary"] == "Coverage was steady." and report["dropped"], text


def test_only_the_counts_are_allowed_numbers_not_every_digit_in_the_data():
    from radar.trends import TrendNote
    accepted, _ = validate_trends(TrendNote.model_validate(note(summary="MCP had 2 clusters. LangGraph had 0 last week.")), stats())
    assert accepted["summary"] == "MCP had 2 clusters. LangGraph had 0 last week."


@pytest.mark.parametrize("text, kept", [("LangGraph mentions surged.", True), ("CrewAI mentions surged.", False), ("MCP fell sharply.", False),
                                        ("CrewAI went quiet.", True), ("LangGraph went quiet.", False), ("Coverage grew.", True),
                                        ("MCP and CrewAI both grew.", False)])
def test_direction_claims_in_free_text_must_match_the_counts(text, kept):
    from radar.trends import TrendNote
    accepted, _ = validate_trends(TrendNote.model_validate(note(summary=text)), stats())
    assert bool(accepted["summary"]) is kept


def test_pattern_names_are_held_to_the_same_rules():
    from radar.trends import TrendNote
    bad = note(patterns=[{"name": "OpenAI up 900%", "why": "w", "clusters": ["2026-09-28/c2", "2026-09-29/c3"]},
                         {"name": "CrewAI surged", "why": "w", "clusters": ["2026-09-28/c2", "2026-09-29/c3"]}])
    accepted, report = validate_trends(TrendNote.model_validate(bad), stats())
    assert accepted["patterns"] == [] and len(report["dropped"]) >= 2
