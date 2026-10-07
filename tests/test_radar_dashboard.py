# tests/test_radar_dashboard.py
import json
import math

from web import radar_dashboard as dash

UP = {"packages": {
    "pypi:b": {"latest_stable": "2.0.0", "diffs": [{"from": "1.0.0", "to": "2.0.0", "counts": {"breaking": 3}}], "advisories": []},
    "pypi:a": {"latest_stable": "1.1.0", "diffs": [{"from": "1.0.0", "to": "1.1.0", "counts": {"breaking": 0}}],
               "advisories": [{"id": "X", "closed": False, "affected_versions": ["1.0.0", "1.1.0"]},
                              {"id": "Y", "closed": False, "affected_versions": ["1.0.0"]},          # fixed in latest
                              {"id": "Z", "closed": True, "affected_versions": []}]},
    "npm:c": {"latest_stable": "4.0.0", "diffs": [], "advisories": []}}}


def test_ticker_puts_breaking_first_then_notable_and_keeps_order_within_each():
    d = {"clusters": [{"id": "c1", "title": "m", "impact": "minor"}, {"id": "c2", "title": "n", "impact": "notable"},
                      {"id": "c3", "title": "b", "impact": "breaking"}, {"id": "c4", "title": "b2", "impact": "breaking"}]}
    rows = dash.ticker(d)
    assert [r["id"] for r in rows] == ["c3", "c4", "c2", "c1"]
    assert rows[0]["glyph"] == "▲" and rows[2]["label"] == "Notable" and rows[3]["impact"] == "minor"
    assert dash.ticker({"clusters": []}) == [] and len(dash.ticker({"clusters": d["clusters"] * 3}, limit=6)) == 6


def test_packages_rank_breaking_over_open_advisories_over_clear():
    rows = {r["key"]: r for r in dash.packages(UP)}
    assert rows["pypi:b"]["level"] == "break" and rows["pypi:b"]["breaking"] == 3 and rows["pypi:b"]["span"] == "1.0.0 → 2.0.0"
    assert rows["pypi:a"]["level"] == "cve" and rows["pypi:a"]["advisories"] == 1
    assert rows["npm:c"]["level"] == "ok" and rows["npm:c"]["span"] == ""
    assert dash.packages({}) == [] and dash.packages({"packages": {}}) == []


def test_sweep_places_urgent_packages_nearer_the_centre_inside_the_dial():
    points = {p["key"]: p for p in dash.sweep(dash.packages(UP), size=240)}
    dist = {k: math.hypot(p["x"] - 120, p["y"] - 120) for k, p in points.items()}
    assert dist["pypi:b"] < dist["pypi:a"] < dist["npm:c"] <= 108
    assert points["pypi:b"]["label"] == "b" and "3 breaking" in points["pypi:b"]["note"]
    assert dash.sweep([]) == []


def test_activity_bars_are_oldest_first_and_scaled_to_the_busiest_day():
    index = {"digests": [{"date": "2026-10-03", "clusters": 4, "breaking": 2}, {"date": "2026-10-02", "clusters": 2, "breaking": 0},
                         {"date": "2026-10-01", "clusters": 0, "breaking": 0}]}
    bars = dash.activity(index, days=14, height=56)
    assert [b["date"] for b in bars] == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert bars[2]["h_total"] == 56 and bars[2]["h_break"] == 28 and bars[2]["y_total"] == 0 and bars[2]["x"] == 40
    assert bars[0]["h_total"] == 0 and dash.activity({}) == []


def test_recorded_runs_are_read_from_the_agent_store_and_never_raise(tmp_path):
    from core.db import connect
    from radar.agent import runs
    db = str(tmp_path / "a.db")
    conn = connect(db)
    runs.ensure_schema(conn)
    rid = runs.create_run(conn, {"repo_url": "https://github.com/o/demo"}, "repo", "ip", 1.0, showcase=True)
    runs.add_usage(conn, rid, 0.0042, 100)
    runs.finish(conn, rid, "done", {}, None, 2.0)
    assert dash.recorded_runs(db) == [{"id": rid, "repo": "o/demo", "cost": 0.0042}]
    (tmp_path / "dir").mkdir()
    assert dash.recorded_runs(str(tmp_path / "dir")) == []          # unopenable database: no runs, no error


def test_only_advisories_that_affect_the_latest_release_count():
    rows = {r["key"]: r for r in dash.packages(UP)}
    assert rows["pypi:a"]["advisories"] == 1           # X affects 1.1.0; Y was fixed before it; Z is closed


def test_malformed_package_entries_are_skipped_not_fatal():
    bad = {"packages": {"pypi:none": None, "pypi:nofrom": {"latest_stable": "1", "diffs": [{"to": "1", "counts": {"breaking": "x"}}],
                                                           "advisories": ["oops"]},
                        "pypi:ok": {"latest_stable": "2", "diffs": [], "advisories": []}}}
    rows = {r["key"]: r for r in dash.packages(bad)}
    assert "pypi:none" not in rows and rows["pypi:nofrom"]["breaking"] == 0 and rows["pypi:nofrom"]["span"] == "\u2192 1"
    assert rows["pypi:ok"]["level"] == "ok"


def test_an_uncreatable_database_path_means_no_runs(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert dash.recorded_runs(str(blocker / "sub" / "a.db")) == []


def test_a_digest_is_live_for_two_days_then_the_page_says_how_old_it_is():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc).timestamp()
    assert dash.freshness("2026-10-07", now) == {"live": True, "days": 0}
    assert dash.freshness("2026-10-05", now) == {"live": True, "days": 2}
    assert dash.freshness("2026-10-04", now) == {"live": False, "days": 3}
    assert dash.freshness("not-a-date", now) == {"live": False, "days": None}
    assert dash.freshness("2026-10-09", now)["live"] is True              # a clock a day behind never makes it look stale
