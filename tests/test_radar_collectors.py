import httpx
import pytest

from radar import store
from radar.collectors import Context, collect_feed, collect_github_releases, collect_mcp_registry
from radar.config import Source
from radar.errors import SchemaDrift, SourceError

NOW = 1_790_800_000.0
PUBLISHED = "2026-09-30T10:00:00Z"


class FakeGitHub:
    def __init__(self, releases, stars=7):
        self._releases, self._stars = releases, stars

    def releases(self, repo, per_page=30):
        return self._releases

    def repo_meta(self, repo):
        return {"stars": self._stars}


def ctx(conn, github=None, handler=None):
    client = httpx.Client(transport=httpx.MockTransport(handler or (lambda r: httpx.Response(404))))
    return Context(conn=conn, github=github, client=client, now=NOW, stars={})


REL = {"tag": "v0.2.40", "name": "0.2.40", "url": "https://github.com/a/x/releases/tag/v0.2.40",
       "published": PUBLISHED, "body": "Breaking: removed foo()", "prerelease": False}
SRC = Source(id="gh-x", type="github_releases", tier="official", repo="a/x", package="pypi:x", module="x")


def test_github_releases_become_items_with_parsed_version_and_stars(conn):
    c = ctx(conn, FakeGitHub([REL, dict(REL, tag="v0.2.39", url="https://github.com/a/x/releases/tag/v0.2.39",
                                         published="2026-01-01T00:00:00Z")]))
    items = collect_github_releases(c, SRC, since=NOW - 30 * 86400)
    assert len(items) == 1
    item = items[0]
    assert (item["kind"], item["package"], item["version"], item["repo"], item["stars"]) == \
        ("release", "pypi:x", "0.2.40", "a/x", 7)
    assert item["title"] == "x 0.2.40" and item["extra"] == {"tag": "v0.2.40", "prerelease": False}
    assert c.stars == {"a/x": 7}


def test_release_entries_with_no_usable_fields_are_schema_drift(conn):
    c = ctx(conn, FakeGitHub([{"tag": "", "name": "", "url": "", "published": "", "body": "", "prerelease": False}]))
    with pytest.raises(SchemaDrift):
        collect_github_releases(c, SRC, since=0)
    with pytest.raises(SchemaDrift):
        collect_github_releases(ctx(conn, FakeGitHub([])), SRC, since=0)


FEED = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Changelog</title>
<link href="https://example.com/c"/><published>2026-09-30T10:00:00Z</published><summary>s</summary></entry></feed>"""


def test_feed_collector_filters_by_since_and_tags_kind(conn):
    source = Source(id="blog", type="feed", tier="official", url="https://example.com/feed.xml")
    items = collect_feed(ctx(conn, handler=lambda r: httpx.Response(200, content=FEED)), source, since=0)
    assert [(i["kind"], i["title"], i["source"]) for i in items] == [("post", "Changelog", "blog")]
    assert collect_feed(ctx(conn, handler=lambda r: httpx.Response(200, content=FEED)), source, since=NOW) == []


def test_feed_errors_are_source_errors(conn):
    source = Source(id="blog", type="feed", tier="official", url="https://example.com/feed.xml")
    with pytest.raises(SourceError, match="503"):
        collect_feed(ctx(conn, handler=lambda r: httpx.Response(503)), source, since=0)
    with pytest.raises(SchemaDrift):
        collect_feed(ctx(conn, handler=lambda r: httpx.Response(200, content=b"<feed xmlns='http://www.w3.org/2005/Atom'/>")),
                     source, since=0)


def registry_handler(request):
    assert request.url.params["version"] == "latest" and request.url.params["updated_since"] == "2026-09-29T00:00:00Z"
    body = {"servers": [
        {"server": {"name": "io.github.acme/weather", "description": "Weather tools",
                    "repository": {"url": "https://github.com/Acme/Weather", "source": "github"}, "version": "1.0.0"}},
        {"server": {"name": "no-repo", "description": "x"}},
        {"server": {"name": "gitlab", "repository": {"url": "https://gitlab.com/a/b", "source": "gitlab"}}},
    ], "metadata": {}}
    return httpx.Response(200, json=body)


def test_registry_entries_with_a_github_repo_become_server_items(conn):
    source = Source(id="mcp-registry", type="mcp_registry", tier="official")
    items = collect_mcp_registry(ctx(conn, handler=registry_handler), source, since=1790640000.0)
    assert len(items) == 1
    item = items[0]
    assert (item["kind"], item["repo"], item["url"]) == ("server", "acme/weather", "https://github.com/acme/weather")
    assert item["title"] == "New MCP server: io.github.acme/weather" and item["published"] == NOW
    assert item["extra"] == {"version": "1.0.0"}


ARXIV = b"""<feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/2609.00001v1</id><title>Agents
  that plan</title><link rel="alternate" href="http://arxiv.org/abs/2609.00001v1"/>
<published>2026-09-30T08:00:00Z</published><summary>We study planning.</summary></entry></feed>"""


def test_arxiv_collector_queries_the_categories_and_tags_papers(conn):
    seen = {}

    def handler(request):
        seen.update(request.url.params)
        return httpx.Response(200, content=ARXIV)
    source = Source(id="arxiv-ai", type="arxiv", tier="community", options={"categories": ["cs.AI", "cs.MA"]})
    from radar.collectors import collect_arxiv
    items = collect_arxiv(ctx(conn, handler=handler), source, since=0)
    assert seen["search_query"] == "cat:cs.AI OR cat:cs.MA" and seen["sortBy"] == "submittedDate"
    assert [(i["kind"], i["title"], i["tier"]) for i in items] == [("paper", "Agents that plan", "community")]


def test_hn_collector_filters_by_points_and_dedupes_across_queries(conn):
    calls = []

    def handler(request):
        calls.append(request.url.params["query"])
        assert "points>50" in request.url.params["numericFilters"] and "created_at_i>" in request.url.params["numericFilters"]
        return httpx.Response(200, json={"hits": [
            {"objectID": "1", "title": "Show HN: tiny agent", "url": "https://example.com/agent", "points": 120,
             "num_comments": 30, "created_at_i": 1790700000},
            {"objectID": "2", "title": "Ask HN: MCP?", "url": None, "points": 80, "num_comments": 5,
             "created_at_i": 1790700100},
            {"objectID": "3", "title": None, "url": "https://x.test", "points": 90, "created_at_i": 1790700200}]})
    source = Source(id="hn", type="hn", tier="community", options={"queries": ["LLM agent", "MCP"], "min_points": 50})
    from radar.collectors import collect_hn
    items = collect_hn(ctx(conn, handler=handler), source, since=1790000000.0)
    assert calls == ["LLM agent", "MCP"]
    assert sorted(i["url"] for i in items) == ["https://example.com/agent", "https://news.ycombinator.com/item?id=2"]
    assert items[0]["kind"] == "discussion" and items[0]["extra"]["points"] in (120, 80)


def rels(*tags):
    return [{"tag": t, "name": "", "url": f"https://github.com/a/x/releases/tag/{t}", "published": PUBLISHED,
             "body": "", "prerelease": False} for t in tags]


def identities(source, *tags):
    items = collect_github_releases(ctx(None, FakeGitHub(rels(*tags))), source, since=0)
    return {i["extra"]["tag"]: (i["package"], i["version"]) for i in items}


def test_monorepo_sub_package_tags_are_not_attributed_to_the_tracked_package():
    langchain = Source(id="gh-lc", type="github_releases", tier="official", repo="langchain-ai/langchain",
                       package="pypi:langchain", module="langchain")
    got = identities(langchain, "langchain==1.4.3", "v1.4.2", "langchain-openai==1.6.7", "langchain-core==1.6.5", "nightly-2")
    assert got["langchain==1.4.3"] == ("pypi:langchain", "1.4.3") and got["v1.4.2"] == ("pypi:langchain", "1.4.2")
    assert got["langchain-openai==1.6.7"] == (None, None) and got["langchain-core==1.6.5"] == (None, None)
    anthropic = Source(id="gh-an", type="github_releases", tier="official", repo="anthropics/anthropic-sdk-typescript",
                       package="npm:@anthropic-ai/sdk")
    got = identities(anthropic, "v0.131.0", "vertex-sdk-v0.20.2", "bedrock-sdk-v0.34.2")
    assert got == {"v0.131.0": ("npm:@anthropic-ai/sdk", "0.131.0"), "vertex-sdk-v0.20.2": (None, None),
                   "bedrock-sdk-v0.34.2": (None, None)}


def test_tag_prefix_selects_one_package_in_a_multi_language_repo():
    autogen = Source(id="gh-ag", type="github_releases", tier="official", repo="microsoft/autogen",
                     package="pypi:autogen-agentchat", module="autogen_agentchat", options={"tag_prefix": "python-v"})
    got = identities(autogen, "python-v0.5.1", "dotnet-v0.5.1", "v0.5.1")
    assert got == {"python-v0.5.1": ("pypi:autogen-agentchat", "0.5.1"), "dotnet-v0.5.1": (None, None), "v0.5.1": (None, None)}


def test_a_source_without_a_package_keeps_plain_versions():
    ollama = Source(id="gh-ol", type="github_releases", tier="official", repo="ollama/ollama")
    assert identities(ollama, "v0.9.1") == {"v0.9.1": (None, "0.9.1")}


def test_registry_is_read_from_the_watermark_to_the_last_page_and_uses_the_published_date(conn):
    pages = {None: {"servers": [{"server": {"name": "n/a", "repository": {"url": "https://github.com/acme/a", "source": "github"}},
                                 "_meta": {"io.modelcontextprotocol.registry/official": {"publishedAt": "2026-09-30T10:00:00Z"}}}],
                     "metadata": {"nextCursor": "c2"}},
             "c2": {"servers": [{"server": {"name": "n/b", "repository": {"url": "https://github.com/acme/b", "source": "github"}}}],
                    "metadata": {}}}
    seen = []

    def handler(request):
        seen.append((request.url.params.get("cursor"), request.url.params["updated_since"]))
        return httpx.Response(200, json=pages[request.url.params.get("cursor")])
    items = collect_mcp_registry(ctx(conn, handler=handler), Source(id="r", type="mcp_registry", tier="official"), since=1790640000.0)
    assert seen == [(None, "2026-09-29T00:00:00Z"), ("c2", "2026-09-29T00:00:00Z")]
    assert [(i["repo"], i["published"]) for i in items] == [("acme/a", 1790762400.0), ("acme/b", NOW)]
