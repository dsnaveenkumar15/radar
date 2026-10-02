import httpx
import pytest

from core.events import consume
from core.github import GitHubError, GitHubRateLimited
from radar import store
from radar.collect import run_collect
from radar.collectors import Context
from radar.config import Source
from radar.errors import SourceError

NOW = 1_790_800_000.0
PUBLISHED = "2026-09-30T10:00:00Z"
REL = {"tag": "v1.0.0", "name": "1.0.0", "url": "https://github.com/a/x/releases/tag/v1.0.0",
       "published": PUBLISHED, "body": "notes", "prerelease": False}


class FakeGitHub:
    def __init__(self, fail=None):
        self.fail, self.calls = fail or {}, []

    def releases(self, repo, per_page=30):
        self.calls.append(repo)
        if repo in self.fail:
            raise self.fail[repo]
        return [dict(REL, url=f"https://github.com/{repo}/releases/tag/v1.0.0")]

    def repo_meta(self, repo):
        return {"stars": 11}


def src(i, type_="github_releases"):
    return Source(id=f"gh-{i}", type=type_, tier="official", repo=f"a/r{i}", package=f"pypi:r{i}")


@pytest.fixture
def db(conn):
    store.ensure_schema(conn)
    return conn


def make_ctx(db, github, handler=None, now=NOW):
    client = httpx.Client(transport=httpx.MockTransport(handler or (lambda r: httpx.Response(404))))
    return Context(conn=db, github=github, client=client, now=now, stars={})


def test_items_are_stored_health_recorded_and_stars_sampled(db):
    report = run_collect(make_ctx(db, FakeGitHub()), [src(1), src(2)])
    assert report["new"] == 2 and report["ok"] == ["gh-1", "gh-2"] and report["failed"] == {}
    assert [h["ok"] for h in store.source_health(db, NOW)] == [True, True]
    assert db.execute("SELECT stars FROM radar_star_samples WHERE repo='a/r1'").fetchone()["stars"] == 11
    assert run_collect(make_ctx(db, FakeGitHub()), [src(1), src(2)])["new"] == 0       # reruns add nothing


def test_one_failing_source_does_not_stop_the_others(db):
    github = FakeGitHub(fail={"a/r1": GitHubError("GitHub 500")})
    report = run_collect(make_ctx(db, github), [src(1), src(2)])
    assert report["ok"] == ["gh-2"] and "GitHub 500" in report["failed"]["gh-1"]
    assert store.source_health(db, NOW)[0]["consecutive_failures"] == 1


def test_rate_limit_skips_remaining_github_sources_but_not_other_types(db):
    github = FakeGitHub(fail={"a/r1": GitHubRateLimited("limit", NOW + 3600)})
    feed = Source(id="blog", type="feed", tier="official", url="https://example.com/feed.xml")
    body = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>T</title><link href="https://example.com/t"/>'
            b"<published>2026-09-30T10:00:00Z</published></entry></feed>")
    report = run_collect(make_ctx(db, github, lambda r: httpx.Response(200, content=body)), [src(1), src(2), feed])
    assert github.calls == ["a/r1"]                       # r2 was not even attempted
    assert report["ok"] == ["blog"] and set(report["failed"]) == {"gh-1", "gh-2"}
    assert "rate limit" in report["failed"]["gh-2"]


def test_unexpected_collector_bugs_are_contained(db, monkeypatch):
    import radar.collect as module
    monkeypatch.setitem(module.COLLECTORS, "github_releases", lambda c, s, since: 1 / 0)
    report = run_collect(make_ctx(db, FakeGitHub()), [src(1)])
    assert "ZeroDivisionError" in report["failed"]["gh-1"]


def registry_body(*repos):
    return {"servers": [{"server": {"name": f"n/{r}", "repository": {"url": f"https://github.com/acme/{r}",
                                                                     "source": "github"}}} for r in repos],
            "metadata": {}}


def test_registry_first_run_is_a_silent_baseline_then_new_servers_emit_events_once(db):
    source = Source(id="mcp-registry", type="mcp_registry", tier="official")
    state = {"repos": ["one", "two"]}
    handler = lambda r: httpx.Response(200, json=registry_body(*state["repos"]))
    run_collect(make_ctx(db, None, handler), [source])
    assert db.execute("SELECT count(*) FROM radar_items WHERE digest_date='baseline'").fetchone()[0] == 2
    assert store.items_unconsumed(db, 0, 10) == []
    state["repos"] = ["one", "two", "three"]
    run_collect(make_ctx(db, None, handler, now=NOW + 3600), [source])
    run_collect(make_ctx(db, None, handler, now=NOW + 7200), [source])
    heard = []
    consume(db, "test", lambda e: heard.append(e.payload), types=["radar.mcp_server_discovered"])
    assert heard == [{"repo": "acme/three", "signal": 0, "source": "registry"}]
    assert [i["repo"] for i in store.items_unconsumed(db, 0, 10)] == ["acme/three"]
