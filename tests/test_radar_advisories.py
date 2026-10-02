import json
from datetime import datetime, timezone

import httpx
import pytest

from radar import store
from radar.advisories import refresh_advisories
from radar.config import Source

NOW = 1_790_800_000.0
DAY = 86400


def vuln(vid, published, severity="HIGH", summary="Bad thing"):
    iso = datetime.fromtimestamp(published, timezone.utc).isoformat()
    return {"id": vid, "summary": summary, "details": "Longer details", "published": iso, "aliases": ["CVE-2026-1"],
            "database_specific": {"severity": severity} if severity else {}, "affected": [{"ranges": [{"type": "ECOSYSTEM"}],
                                                                                      "versions": ["1.0"]}]}


SRC = Source(id="gh-x", type="github_releases", tier="official", repo="a/x", package="pypi:x", module="x")


@pytest.fixture
def db(conn):
    store.ensure_schema(conn)
    return conn


def client(pages):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json=pages[body.get("page_token")])
    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_recent_new_advisories_become_items_and_old_ones_only_rows(db):
    http, calls = client({None: {"vulns": [vuln("GHSA-new", NOW - 2 * DAY, "MODERATE"), vuln("GHSA-old", NOW - 100 * DAY)]}})
    assert refresh_advisories(db, http, [SRC], NOW) == {"pypi:x": "ok"}
    assert calls[0] == {"package": {"name": "x", "ecosystem": "PyPI"}}
    assert db.execute("SELECT count(*) FROM radar_advisories").fetchone()[0] == 2
    (item,) = store.items_unconsumed(db, 0, 10)
    assert (item["kind"], item["source"], item["tier"], item["package"]) == ("advisory", "osv", "official", "pypi:x")
    assert item["extra"] == {"severity": "medium", "id": "GHSA-new", "aliases": ["CVE-2026-1"]}
    assert item["url"] == "https://osv.dev/vulnerability/GHSA-new" and item["title"].startswith("GHSA-new: Bad thing")


def test_reruns_add_nothing_and_unknown_severity_is_not_invented(db):
    http, _ = client({None: {"vulns": [vuln("GHSA-a", NOW - DAY, severity=None)]}})
    refresh_advisories(db, http, [SRC], NOW)
    refresh_advisories(db, http, [SRC], NOW + 60)
    assert db.execute("SELECT severity FROM radar_advisories").fetchone()["severity"] == "unknown"
    assert len(store.items_unconsumed(db, 0, 10)) == 1


def test_pagination_follows_the_token(db):
    http, calls = client({None: {"vulns": [vuln("GHSA-1", NOW - DAY)], "next_page_token": "t2"},
                          "t2": {"vulns": [vuln("GHSA-2", NOW - DAY)]}})
    refresh_advisories(db, http, [SRC], NOW)
    assert [c.get("page_token") for c in calls] == [None, "t2"]
    assert db.execute("SELECT count(*) FROM radar_advisories").fetchone()[0] == 2


def test_failures_are_recorded_and_do_not_stop_other_packages(db):
    other = Source(id="gh-y", type="github_releases", tier="official", repo="a/y", package="npm:y")

    def handler(request):
        if json.loads(request.content)["package"]["name"] == "x":
            return httpx.Response(503)
        return httpx.Response(200, json={"vulns": []})
    report = refresh_advisories(db, httpx.Client(transport=httpx.MockTransport(handler)), [SRC, other], NOW)
    assert report["pypi:x"].startswith("error:") and report["npm:y"] == "ok"
    health = {h["source"]: h for h in store.source_health(db, NOW)}
    assert health["osv:pypi:x"]["ok"] is False and health["osv:npm:y"]["ok"] is True


def test_vulns_without_ids_are_schema_drift(db):
    http, _ = client({None: {"vulns": [{"summary": "no id", "published": "2026-09-30T00:00:00Z"}]}})
    report = refresh_advisories(db, http, [SRC], NOW)
    assert "schema drift" in report["pypi:x"]


def test_a_malformed_advisory_for_one_package_does_not_stop_the_others(db):
    other = Source(id="gh-y", type="github_releases", tier="official", repo="a/y", package="npm:y")

    def handler(request):
        if json.loads(request.content)["package"]["name"] == "x":
            return httpx.Response(200, json={"vulns": [1]})
        return httpx.Response(200, json={"vulns": []})
    report = refresh_advisories(db, httpx.Client(transport=httpx.MockTransport(handler)), [SRC, other], NOW)
    assert report["pypi:x"].startswith("error:") and report["npm:y"] == "ok"
