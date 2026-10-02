import json

import pytest
from fastapi.testclient import TestClient

from core.publish import publish
from radar.config import Entity, Registry
from radar.publishing import build_files
from tests.webutil import make_settings
from web.app import create_app

EVIL = "<script>alert(1)</script>"
REGISTRY = Registry([Entity("mcp", "MCP", "protocol", (), True)])
NOW = 1_790_877_600.0
FACTS = {"api_breaking": [{"package": "pypi:x", "from": "1.0.0", "to": "1.1.0", "symbols": ["pkg.run"],
                           "details": [{"symbol": "pkg.run", "detail": "new required parameter 'b'"}]}],
         "api_deprecations": [], "advisories": [{"id": "GHSA-1", "severity": "high", "package": "pypi:x"}],
         "packages": [], "versions": ["1.0.0", "1.1.0"]}
SYNTH = {"what_changed": "x 1.1.0 adds a required parameter to run().", "affects": ["mcp"], "impact": "breaking",
         "migration_effort": "low", "action": "Pass b.", "citations": ["i1"],
         "quote": {"item": "i1", "quote": "Breaking: removed foo()."}, "versions": ["1.1.0"], "entities": ["mcp"],
         "migration": {"language": "python", "before": "run(1)", "after": "run(1, 2)"}}


def item(n, **over):
    base = {"id": f"i{n}", "source": "gh-x", "tier": "official", "kind": "release",
            "url": "https://github.com/a/x/releases/tag/v1", "title": f"x release {n}", "published": 1_790_762_400.0,
            "content_hash": "h", "package": "pypi:x", "version": "1.1.0"}
    return {**base, **over}


def cluster(cid="c1", impact="breaking", synthesis=SYNTH, **over):
    base = {"id": cid, "title": f"x 1.1.0 ({cid})", "impact": impact, "signal": {"score": 80}, "synthesis": synthesis,
            "facts": FACTS, "items": [item(1), item(2, title="HN discussion", tier="community", kind="discussion")]}
    return {**base, **over}


def digest(date="2026-10-02", clusters=None, **over):
    base = {"schema": 1, "date": date, "generated_at": NOW, "status": "full", "synthesis": "complete",
            "sources": {"checked": 17, "total": 17, "failed": []},
            "provenance": {"prompt": "radar.synth@1", "model": "sonnet", "signal": "radar.signal@1", "embedded": True,
                           "unsynthesized_reason": None},
            "validation": {}, "considered": 3, "clusters": clusters if clusters is not None else [cluster()],
            "new_servers": [{"repo": "acme/weather", "name": "io.github.acme/weather", "description": "Weather tools",
                             "url": "https://github.com/acme/weather"}]}
    return {**base, **over}


def publish_digests(root, *digests, errata=None, sources_health=()):
    files = {}
    for d in digests:
        files = build_files(files, d, list(sources_health), REGISTRY, NOW)
    if errata is not None:
        files["errata.json"] = json.dumps(errata).encode()
    return publish(root, "radar", "digests", files, {})


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


def client(settings, host="radar.localhost"):
    return TestClient(create_app(settings), base_url=f"http://{host}")


def test_empty_state_before_the_first_digest(settings):
    r = client(settings).get("/")
    assert r.status_code == 200 and "No digest published yet" in r.text


def test_home_shows_the_latest_digest_with_labels_and_cluster_links(settings):
    publish_digests(settings.data_root, digest("2026-10-01", [cluster("c9", "minor")]),
                    digest("2026-10-02", [cluster("c1", "breaking"), cluster("c2", "notable", synthesis=None)]))
    html = client(settings).get("/").text
    assert "2026-10-02" in html and "17/17 sources checked" in html and 'href="/c/2026-10-02/c1"' in html
    assert html.index("c1") < html.index("c2") and "c9" not in html
    assert "x 1.1.0 adds a required parameter" in html and "Unsynthesized" in html      # c2 has no synthesis
    assert "New MCP servers" in html and "acme/weather" in html and 'href="/archive"' in html


def test_partial_and_unsynthesized_digests_are_labeled(settings):
    publish_digests(settings.data_root, digest(status="partial", synthesis="unsynthesized",
                                               sources={"checked": 12, "total": 17, "failed": ["gh-x", "hn"]},
                                               clusters=[cluster(synthesis=None)]))
    html = client(settings).get("/").text
    assert "Partial digest" in html and "gh-x, hn" in html and "12/17 sources checked" in html
    assert "Unsynthesized" in html and "computed from API diffs" in html


def test_hostile_content_is_escaped_and_non_http_urls_are_not_linked(settings):
    evil_synth = {**SYNTH, "what_changed": EVIL, "action": EVIL, "entities": [EVIL],
                  "quote": {"item": "i1", "quote": EVIL}, "migration": {"language": "python", "before": EVIL, "after": EVIL}}
    evil_cluster = cluster(title=EVIL, synthesis=evil_synth,
                           items=[item(1, title=EVIL, url="javascript:alert(1)"), item(2, url="http://ok.example/x")])
    d = digest(clusters=[evil_cluster], new_servers=[{"repo": EVIL, "name": EVIL, "description": EVIL,
                                                      "url": "javascript:alert(2)"}])
    publish_digests(settings.data_root, d)
    for path in ("/", "/digests/2026-10-02", "/c/2026-10-02/c1"):
        html = client(settings).get(path).text
        assert EVIL not in html and "javascript:" not in html, path
    assert "&lt;script&gt;" in client(settings).get("/c/2026-10-02/c1").text
    assert 'href="http://ok.example/x"' in client(settings).get("/c/2026-10-02/c1").text


def test_archive_lists_dates_newest_first_with_counts(settings):
    publish_digests(settings.data_root, digest("2026-10-01", [cluster("c1", "minor")]),
                    digest("2026-10-02", [cluster("c1"), cluster("c2")]))
    html = client(settings).get("/archive").text
    assert html.index("2026-10-02") < html.index("2026-10-01") and 'href="/digests/2026-10-02"' in html
    assert client(settings).get("/digests/2026-10-01").status_code == 200


@pytest.mark.parametrize("path", ["/digests/2026-10-09", "/digests/not-a-date", "/digests/2026-1-2",
                                  "/c/2026-10-02/c99", "/c/2026-10-02/not-a-cluster", "/c/nope/c1"])
def test_unknown_or_malformed_ids_are_404(settings, path):
    publish_digests(settings.data_root, digest())
    assert client(settings).get(path).status_code == 404


def test_cluster_page_shows_synthesis_quote_migration_facts_and_citations(settings):
    publish_digests(settings.data_root, digest())
    html = client(settings).get("/c/2026-10-02/c1").text
    assert "x 1.1.0 adds a required parameter" in html and "Pass b." in html
    assert "Breaking: removed foo()." in html and "run(1, 2)" in html
    assert "pkg.run" in html and "new required parameter &#39;b&#39;" in html and "GHSA-1" in html
    assert "x release 1" in html and "HN discussion" in html and "Cited" in html and "Other items" in html
    assert "report" in html.lower()


def test_pages_survive_a_digest_with_missing_fields(settings):
    sparse = {"date": "2026-10-02", "clusters": [{"id": "c1"}, {"id": "bad id"}, "junk"]}
    publish(settings.data_root, "radar", "digests",
            {"digests/2026-10-02.json": json.dumps(sparse).encode(),
             "index.json": json.dumps({"digests": [{"date": "2026-10-02"}]}).encode()}, {})
    for path in ("/", "/digests/2026-10-02", "/c/2026-10-02/c1", "/archive"):
        assert client(settings).get(path).status_code == 200, path


def test_radar_routes_exist_only_on_the_radar_host(settings):
    publish_digests(settings.data_root, digest())
    for host in ("trust.localhost", "blueprints.localhost"):
        assert client(settings, host).get("/archive").status_code == 404
        assert client(settings, host).get("/c/2026-10-02/c1").status_code == 404
    assert client(settings, "radar.localhost").get("/archive").status_code == 200


def test_unsynthesized_clusters_get_stack_tags_from_their_packages_and_titles(settings):
    lg = cluster("c1", synthesis=None, title="langgraph 1.2.12",
                 items=[item(1, package="pypi:langgraph", title="langgraph 1.2.12")])
    ts = cluster("c2", synthesis=None, title="typescript-sdk 1.31.0",
                 items=[item(2, package="npm:@modelcontextprotocol/sdk", title="typescript-sdk 1.31.0")])
    li = cluster("c3", synthesis=None, title="llama_index v0.14.25",
                 items=[item(3, package="pypi:llama-index-core", title="llama_index v0.14.25")])
    plain = cluster("c4", synthesis=None, title="Show HN: a tiny thing", items=[item(4, package=None, title="Show HN: a tiny thing")])
    publish_digests(settings.data_root, digest(clusters=[lg, ts, li, plain]))
    html = client(settings).get("/").text
    assert 'id="c1" data-cluster="c1" data-generated="1790877600" data-affects="langgraph"' in html
    assert 'data-cluster="c2" data-generated="1790877600" data-affects="mcp"' in html
    assert 'data-cluster="c3" data-generated="1790877600" data-affects="llamaindex"' in html
    assert 'data-cluster="c4" data-generated="1790877600" data-affects=""' in html
    assert 'data-tags="langgraph llamaindex mcp"' in html


def test_a_release_swap_during_a_read_cannot_leave_stale_content_cached(settings, monkeypatch):
    from core.publish import Release
    from web.radar_site import RadarData
    publish_digests(settings.data_root, digest("2026-10-01", [cluster("c1", "minor")]))
    data = RadarData(settings.data_root)
    first = data.release()
    original = Release.read
    switched = []

    def racing_read(self, rel):
        if self.id == first.id and not switched:
            switched.append(1)
            publish_digests(settings.data_root, digest("2026-10-01", [cluster("c1", "minor")]),
                            digest("2026-10-02", [cluster("c1")]))                 # another request's release swap
            data.release()
        return original(self, rel)
    monkeypatch.setattr(Release, "read", racing_read)
    data.index()                                                                    # the in-flight read of the old release
    monkeypatch.setattr(Release, "read", original)
    assert [d["date"] for d in data.index()["digests"]] == ["2026-10-02", "2026-10-01"]
