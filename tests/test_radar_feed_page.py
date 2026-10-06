from tests.test_radar_site import EVIL, client, cluster, digest, publish_digests, settings  # noqa: F401


def test_feed_page_explains_the_feed_and_lists_its_items(settings):
    publish_digests(settings.data_root,
                    digest("2026-10-01", [cluster("c5", "notable")]),
                    digest("2026-10-02", [cluster("c1", "breaking"), cluster("c2", "minor"), cluster("c3", "notable", synthesis=None)]))
    r = client(settings).get("/feed")
    html = r.text
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert 'id="feed-url"' in html and "radar.localhost/feed.xml" in html and 'data-copy="feed-url"' in html
    assert 'href="/c/2026-10-02/c1"' in html and 'href="/c/2026-10-02/c3"' in html and 'href="/c/2026-10-01/c5"' in html
    assert 'href="/c/2026-10-02/c2"' in html and "status-quiet" in html          # minor updates are in the feed too
    assert html.index("/c/2026-10-02/c1") < html.index("/c/2026-10-01/c5")  # newest first, as in the XML
    assert '<script src="/static/radar-feed.js' in html


def test_feed_page_without_items_says_so(settings):
    publish_digests(settings.data_root, digest("2026-10-02", clusters=[]))
    html = client(settings).get("/feed").text
    assert "No updates in the last" in html and 'id="feed-url"' in html


def test_feed_page_escapes_titles(settings):
    publish_digests(settings.data_root, digest("2026-10-02", [cluster("c1", "breaking", title=EVIL)]))
    html = client(settings).get("/feed").text
    assert EVIL not in html and "&lt;script&gt;" in html


def test_nav_sends_people_to_the_feed_page_and_the_xml_stays_for_readers(settings):
    publish_digests(settings.data_root, digest("2026-10-02", [cluster("c1", "breaking")]))
    c = client(settings)
    html = c.get("/archive").text
    assert 'href="/feed">RSS</a>' in html and 'href="/feed.xml">RSS' not in html
    assert 'rel="alternate" type="application/rss+xml"' in html and 'href="/feed.xml"' in html
    xml = c.get("/feed.xml")
    assert xml.headers["content-type"].startswith("application/rss+xml") and "<rss version=\"2.0\">" in xml.text
