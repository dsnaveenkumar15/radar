import json

import pytest

from core import review
from core.db import connect
from core.jobs import enqueue
from core.publish import load_current, publish
from core.worker import run_once
from radar import store
from radar.jobs import ERRATA_JOB, build_job_types
from radar.publishing import DATASET, build_files, publish_digest, publish_errata
from tests.test_radar_site import (EVIL, NOW, REGISTRY, client, cluster, digest, publish_digests,  # noqa: F401
                                   settings)

ERRATUM = {"date": "2026-10-02", "cluster": "c1", "text": "Corrected: this is not breaking.", "created_at": NOW}


@pytest.fixture
def db(conn):
    store.ensure_schema(conn)
    return conn


def test_errata_store_cleans_text_and_orders_oldest_first(db):
    store.add_errata(db, "2026-10-02", "c1", "second​" + "x" * 2000, 2.0)
    store.add_errata(db, "2026-10-01", "c4", "first", 1.0)
    rows = store.errata(db)
    assert [r["cluster"] for r in rows] == ["c4", "c1"] and len(rows[1]["text"]) == 1000 and "​" not in rows[1]["text"]
    assert set(rows[0]) == {"date", "cluster", "text", "created_at"}


def test_publish_errata_adds_errata_json_and_never_touches_digest_bytes(db, tmp_path):
    publish(tmp_path, "radar", DATASET, build_files({}, digest(), [], REGISTRY, NOW), {"date": "2026-10-02"})
    before = load_current(tmp_path, "radar", DATASET)
    store.add_errata(db, "2026-10-02", "c1", ERRATUM["text"], NOW)
    result = publish_errata(db, tmp_path, REGISTRY, NOW)
    after = load_current(tmp_path, "radar", DATASET)
    assert result["published"] is True and after.id != before.id
    assert after.read("digests/2026-10-02.json") == before.read("digests/2026-10-02.json")
    assert after.read("index.json") == before.read("index.json")
    assert json.loads(after.read("errata.json")) == [ERRATUM]
    assert publish_errata(db, tmp_path, REGISTRY, NOW)["published"] is False        # nothing changed


def test_publish_errata_before_any_digest_is_a_no_op(db, tmp_path):
    assert publish_errata(db, tmp_path, REGISTRY, NOW)["published"] is False


def test_the_next_nightly_digest_keeps_the_errata(db, tmp_path):
    publish_digest(db, tmp_path, digest("2026-10-02"), [], REGISTRY, NOW)
    store.add_errata(db, "2026-10-02", "c1", ERRATUM["text"], NOW)
    publish_errata(db, tmp_path, REGISTRY, NOW)
    publish_digest(db, tmp_path, digest("2026-10-03"), [], REGISTRY, NOW + 86400)
    release = load_current(tmp_path, "radar", DATASET)
    assert json.loads(release.read("errata.json")) == [ERRATUM] and "digests/2026-10-03.json" in release.manifest["files"]


def test_errata_job_runs_and_publishes(db, tmp_path):
    publish_digest(db, tmp_path, digest("2026-10-02"), [], REGISTRY, NOW)
    store.add_errata(db, "2026-10-02", "c1", ERRATUM["text"], NOW)
    types = build_job_types(db, github=object(), client=object(), fetcher=object(), embedder=object(), sources=[],
                            registry=REGISTRY, data_root=tmp_path, use_llm=False, clock=lambda: NOW)
    enqueue(db, "radar", ERRATA_JOB, "k", {}, now=NOW)
    assert run_once(db, types, {"radar": 1.0}, "w", clock=lambda: NOW) == "done"
    assert json.loads(load_current(tmp_path, "radar", DATASET).read("errata.json")) == [ERRATUM]


def test_corrections_show_on_the_digest_and_cluster_pages(settings):
    publish_digests(settings.data_root, digest(), errata=[ERRATUM, {**ERRATUM, "cluster": "c9", "text": "other"}])
    c = client(settings)
    cluster_html = c.get("/c/2026-10-02/c1").text
    assert "Correction (2026-10-01)" in cluster_html and "Corrected: this is not breaking." in cluster_html
    assert "other" not in cluster_html
    home = c.get("/").text
    assert "corrected" in home and "1 correction(s)" in home                       # c9 is not in this digest


def post_report(c, **over):
    data = {"date": "2026-10-02", "cluster": "c1", "message": "The version is wrong."}
    data.update(over)
    return c.post("/api/report", data=data, follow_redirects=False)


def test_report_goes_to_the_review_queue_once_and_redirects_back(settings):
    publish_digests(settings.data_root, digest())
    c = client(settings)
    r = post_report(c, message="The version is wrong. " + EVIL)
    assert r.status_code == 303 and r.headers["location"] == "/c/2026-10-02/c1?reported=1"
    post_report(c, message="The version is wrong. " + EVIL)                         # same message: stored once
    conn = connect(settings.db_path)
    rows = review.list_open(conn, "radar")
    assert len(rows) == 1 and rows[0]["kind"] == "radar-error" and rows[0]["ref"].startswith("2026-10-02/c1#")
    payload = json.loads(rows[0]["payload"])
    assert payload["date"] == "2026-10-02" and payload["cluster"] == "c1" and EVIL in payload["message"]
    assert len(payload["reporter"]) == 12
    assert "Thanks" in c.get("/c/2026-10-02/c1?reported=1").text and EVIL not in c.get("/c/2026-10-02/c1?reported=1").text


@pytest.mark.parametrize("over, status", [
    ({"date": "2026-10-09"}, 404), ({"cluster": "c99"}, 404), ({"cluster": "../x"}, 404), ({"date": "nope"}, 404),
    ({"message": "   "}, 400), ({"message": "x" * 1001}, 400)])
def test_bad_reports_are_rejected(settings, over, status):
    publish_digests(settings.data_root, digest())
    assert post_report(client(settings), **over).status_code == status


def test_report_spam_is_rate_limited(settings):
    publish_digests(settings.data_root, digest())
    c = client(settings)
    codes = [post_report(c, message=f"report {i}").status_code for i in range(5)]
    assert codes[:3] == [303, 303, 303] and codes[3] == 429


def test_report_is_radar_only(settings):
    assert post_report(client(settings, "trust.localhost")).status_code == 404


def test_a_cross_origin_report_is_refused_but_a_missing_origin_is_allowed(settings):
    publish_digests(settings.data_root, digest())
    c = client(settings)
    evil = c.post("/api/report", data={"date": "2026-10-02", "cluster": "c1", "message": "x"},
                  headers={"origin": "https://evil.example"}, follow_redirects=False)
    ok = c.post("/api/report", data={"date": "2026-10-02", "cluster": "c1", "message": "x"},
                headers={"origin": "http://radar.localhost"}, follow_redirects=False)
    assert evil.status_code == 403 and ok.status_code == 303
    assert post_report(c, message="no origin header at all").status_code == 303


def test_the_report_queue_is_capped_and_trivial_variants_merge(settings, monkeypatch):
    from web import radar_site
    monkeypatch.setattr(radar_site, "MAX_OPEN_REPORTS", 2)
    publish_digests(settings.data_root, digest())
    c = client(settings)
    assert post_report(c, message="Same thing").status_code == 303
    assert post_report(c, message="same   THING ").status_code == 303                # merged with the first
    assert post_report(c, message="different problem").status_code == 303
    assert post_report(c, message="a third problem").status_code == 429                # two distinct open reports already
    conn = connect(settings.db_path)
    assert len(review.list_open(conn, "radar")) == 2
