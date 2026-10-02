import json

import pytest

from core.publish import publish
from radar.trends import TREND_DATASET, publish_trends
from tests.test_radar_site import client, settings  # noqa: F401  (fixture re-export)
from tests.test_radar_trends import NOW, REG, THIS, LAST, note, stats, validate_trends
from radar.trends import TrendNote

EVIL = "<script>alert(1)</script>"


def week_doc(week="2026-W40", synthesis="complete", **over):
    commentary, report = validate_trends(TrendNote.model_validate(note()), stats())
    doc = {"schema": 1, "week": week, "generated_at": NOW, "synthesis": synthesis, "stats": stats(),
           "commentary": commentary if synthesis == "complete" else None, "validation": report,
           "provenance": {"prompt": "radar.trends@1", "unsynthesized_reason": None}}
    doc.update(over)
    return doc


def test_empty_state(settings):
    r = client(settings).get("/trends")
    assert r.status_code == 200 and "No weekly trends yet" in r.text


def test_latest_week_shows_the_numbers_first_then_labelled_commentary(settings):
    publish_trends(settings.data_root, week_doc(), NOW)
    html = client(settings).get("/trends").text
    assert "2026-W40" in html and "LangGraph" in html and "CrewAI" in html
    assert "Written by a model from the counts above" in html and "More clusters than last week." in html
    assert 'href="/c/2026-09-28/c2"' in html and "Graph runtimes" in html


def test_unsynthesized_weeks_show_only_the_counts(settings):
    publish_trends(settings.data_root, week_doc(synthesis="unsynthesized"), NOW)
    html = client(settings).get("/trends").text
    assert "No commentary this week" in html and "LangGraph" in html and "Written by a model" not in html


def test_older_weeks_are_reachable_and_odd_weeks_are_404(settings):
    publish_trends(settings.data_root, week_doc("2026-W39"), NOW)
    publish_trends(settings.data_root, week_doc("2026-W40"), NOW + 1)
    c = client(settings)
    assert c.get("/trends/2026-W39").status_code == 200 and "2026-W39" in c.get("/trends").text
    for path in ("/trends/2026-W99x", "/trends/nope", "/trends/2025-W01", "/trends/..%2Fx"):
        assert c.get(path).status_code == 404


def test_commentary_and_names_are_escaped(settings):
    doc = week_doc()
    doc["commentary"]["summary"] = EVIL
    doc["commentary"]["rising"][0]["why"] = EVIL
    doc["stats"]["entities"][0]["name"] = EVIL
    publish_trends(settings.data_root, doc, NOW)
    html = client(settings).get("/trends").text
    assert EVIL not in html and "&lt;script&gt;" in html


def test_a_sparse_or_odd_week_file_does_not_500(settings):
    sparse = {"week": "2026-W40", "synthesis": "complete", "stats": {"totals": {"clusters": 0}}}
    index = {"weeks": [{"week": "2026-W40", "synthesis": "complete", "clusters": 0, "digests": 0}]}
    publish(settings.data_root, "radar", "trends", {"index.json": json.dumps(index).encode(),
                                                   "weeks/2026-W40.json": json.dumps(sparse).encode()}, {})
    assert client(settings).get("/trends").status_code in (200, 404)
    assert client(settings, "trust.localhost").get("/trends").status_code == 404
