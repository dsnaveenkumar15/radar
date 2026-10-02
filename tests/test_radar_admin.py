import pytest
from fastapi.testclient import TestClient

from core import review
from core.db import connect
from radar import store
from radar.config import load_entities
from tests.test_radar_site import EVIL
from tests.webutil import make_settings
from web.app import create_app

AUTH = ("owner", "correct-horse-battery-staple")
ORIGIN = {"origin": "http://radar.localhost"}
NOW = 1_790_877_600.0


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path, admin_user="owner", admin_password="correct-horse-battery-staple")


@pytest.fixture
def c(settings):
    return TestClient(create_app(settings), base_url="http://radar.localhost")


@pytest.fixture
def db(settings):
    conn = connect(settings.db_path)
    store.ensure_schema(conn)
    yield conn
    conn.close()


def test_dashboard_works_before_any_radar_table_exists(c):
    assert c.get("/admin", auth=AUTH).status_code == 200


def test_dashboard_shows_failing_sources_and_the_latest_digest(c, db):
    store.record_source(db, "gh-x", "official", "collector", False, "boom " + EVIL, NOW)
    store.record_source(db, "hn", "community", "collector", True, None, NOW)
    db.execute("INSERT INTO radar_digests(date, status, synthesis, release_id, created_at) VALUES "
               "('2026-10-02', 'partial', 'unsynthesized', 'r1', 1.0)")
    html = c.get("/admin", auth=AUTH).text
    assert "gh-x" in html and "boom" in html and EVIL not in html and "&lt;script&gt;" in html
    assert "2026-10-02" in html and "unsynthesized" in html and "hn</td>" not in html


def test_a_report_row_has_an_erratum_form_and_publishing_it_approves_the_review(c, db):
    rid = review.add(db, "radar", "radar-error", "2026-10-02/c1#abc",
                     {"date": "2026-10-02", "cluster": "c1", "message": "wrong " + EVIL, "reporter": "r"})
    html = c.get("/admin", auth=AUTH).text
    assert 'action="/admin/errata"' in html and EVIL not in html
    r = c.post("/admin/errata", data={"date": "2026-10-02", "cluster": "c1", "text": "Fixed: version.",
                                      "review_id": str(rid)}, auth=AUTH, headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303
    assert [(e["date"], e["cluster"], e["text"]) for e in store.errata(db)] == [("2026-10-02", "c1", "Fixed: version.")]
    assert review.decision_for(db, "radar-error", "2026-10-02/c1#abc") == "approved"


@pytest.mark.parametrize("data", [
    {"date": "nope", "cluster": "c1", "text": "x"}, {"date": "2026-10-02", "cluster": "../c1", "text": "x"},
    {"date": "2026-10-02", "cluster": "c1", "text": "  "}, {"date": "2026-10-02", "cluster": "c1", "text": "x" * 1001}])
def test_erratum_input_is_validated(c, db, data):
    assert c.post("/admin/errata", data=data, auth=AUTH, headers=ORIGIN).status_code == 400
    assert store.errata(db) == []


def test_admin_posts_still_need_credentials_and_same_origin(c):
    data = {"date": "2026-10-02", "cluster": "c1", "text": "x"}
    assert c.post("/admin/errata", data=data, headers=ORIGIN).status_code == 401
    assert c.post("/admin/errata", data=data, auth=AUTH).status_code == 403


def test_unknown_entity_can_be_aliased_from_the_review_queue(c, db):
    rid = review.add(db, "radar", "unknown-entity", "totallyunknown", {"name": "Totally Unknown", "cluster": "c1"})
    html = c.get("/admin", auth=AUTH).text
    assert 'action="/admin/aliases"' in html and '<option value="mcp">mcp</option>' in html
    r = c.post("/admin/aliases", data={"name": "Totally Unknown", "entity_id": "mcp", "review_id": str(rid)},
               auth=AUTH, headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303 and store.aliases(db) == {"totallyunknown": "mcp"}
    assert review.decision_for(db, "unknown-entity", "totallyunknown") == "approved"
    assert load_entities().with_aliases(store.aliases(db)).resolve("totally  unknown") == "mcp"


def test_alias_to_a_missing_entity_is_rejected(c, db):
    r = c.post("/admin/aliases", data={"name": "X Thing", "entity_id": "nope"}, auth=AUTH, headers=ORIGIN)
    assert r.status_code == 400 and store.aliases(db) == {}


def test_owner_can_mark_a_cluster_as_updated(c, db):
    r = c.post("/admin/supersessions", auth=AUTH, headers=ORIGIN, follow_redirects=False,
               data={"date": "2026-10-02", "cluster": "c1", "text": "Release 1.1.0 was yanked.", "url": "https://example.com/y"})
    assert r.status_code == 303 and store.supersessions(db)[0]["text"] == "Release 1.1.0 was yanked."


def test_supersession_form_validates_and_needs_credentials(c, db):
    base = {"date": "2026-10-02", "cluster": "c1", "text": "ok", "url": ""}
    assert c.post("/admin/supersessions", auth=AUTH, headers=ORIGIN, data={**base, "date": "yesterday"}).status_code == 400
    assert c.post("/admin/supersessions", auth=AUTH, headers=ORIGIN, data={**base, "cluster": "../x"}).status_code == 400
    assert c.post("/admin/supersessions", auth=AUTH, headers=ORIGIN, data={**base, "text": " "}).status_code == 400
    assert c.post("/admin/supersessions", headers=ORIGIN, data=base).status_code == 401
    assert c.post("/admin/supersessions", auth=AUTH, data=base).status_code == 403


def test_the_owner_can_delete_a_manual_supersession(c, db):
    first = store.add_supersession(db, "2026-10-02", "c1", "a typo", "", NOW)
    html = c.get("/admin", auth=AUTH).text
    assert "a typo" in html and f'action="/admin/supersessions/{first}/delete"' in html
    r = c.post(f"/admin/supersessions/{first}/delete", auth=AUTH, headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303 and store.supersessions(db) == []
    assert c.post(f"/admin/supersessions/{first}/delete", auth=AUTH, headers=ORIGIN).status_code == 404
    assert c.post("/admin/supersessions/1/delete", headers=ORIGIN).status_code == 401
    assert c.post("/admin/supersessions/1/delete", auth=AUTH).status_code == 403
