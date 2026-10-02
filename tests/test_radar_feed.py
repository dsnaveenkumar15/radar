import xml.etree.ElementTree as ET

import pytest

from tests.test_radar_site import EVIL, client, cluster, digest, publish_digests, settings  # noqa: F401

HEALTH = [{"source": "gh-x", "tier": "official", "kind": "collector", "last_ok": 1_790_800_000.0, "last_error": None,
           "consecutive_failures": 0, "ok": True, "drift": False},
          {"source": "hn", "tier": "community", "kind": "collector", "last_ok": None,
           "last_error": "HN returned 503 " + EVIL, "consecutive_failures": 3, "ok": False, "drift": False}]


def test_sources_page_shows_health_the_formula_and_the_validators(settings):
    publish_digests(settings.data_root, digest(), sources_health=HEALTH)
    html = client(settings).get("/sources").text
    assert "gh-x" in html and "hn" in html and "HN returned 503" in html and EVIL not in html
    assert "tier" in html and "corroboration" in html and "versions must appear" in html
    assert "radar.synth@1" in html and "mcp" in html


def test_sources_page_without_data_still_renders(settings):
    r = client(settings).get("/sources")
    assert r.status_code == 200 and "No source data yet" in r.text


def test_feed_has_one_item_per_breaking_or_notable_cluster_newest_first(settings):
    publish_digests(settings.data_root,
                    digest("2026-10-01", [cluster("c5", "notable")]),
                    digest("2026-10-02", [cluster("c1", "breaking"), cluster("c2", "minor"), cluster("c3", "notable", synthesis=None)]))
    r = client(settings).get("/feed.xml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/rss+xml")
    items = ET.fromstring(r.text).findall("./channel/item")
    assert [i.find("link").text for i in items] == [
        "http://radar.localhost/c/2026-10-02/c1", "http://radar.localhost/c/2026-10-02/c3",
        "http://radar.localhost/c/2026-10-01/c5"]
    assert items[0].find("title").text.startswith("[breaking]") and "adds a required parameter" in items[0].find("description").text
    assert "Unsynthesized" in items[1].find("description").text


def test_feed_escapes_hostile_text_and_strips_control_characters(settings):
    bad = cluster("c1", title=EVIL + "\x01 end", synthesis={**cluster()["synthesis"], "what_changed": EVIL})
    publish_digests(settings.data_root, digest(clusters=[bad]))
    r = client(settings).get("/feed.xml")
    root = ET.fromstring(r.text)                         # still well-formed XML
    assert EVIL in root.find("./channel/item/title").text and "\x01" not in r.text
    assert EVIL not in r.text.replace("&lt;script&gt;", "")


def test_empty_feed_is_valid(settings):
    r = client(settings).get("/feed.xml")
    assert r.status_code == 200 and ET.fromstring(r.text).findall("./channel/item") == []


def test_feed_is_radar_only(settings):
    assert client(settings, "trust.localhost").get("/feed.xml").status_code == 404
