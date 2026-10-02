import json

import pytest

from core import jobs, review
from core.llm import AuthExpired, FakeBackend, QuotaDeferred
from core.publish import load_current
from core.worker import JobType, run_once
from radar import digest as digest_module
from radar import store, synth
from radar.config import Entity, Registry, Source
from radar.digest import completeness, digest_date, digest_deadline, stages
from radar.publishing import DATASET
from tests.radar_fakes import FakeEmbedder

NOW = 1_790_877_600.0                    # 2026-10-01 23:30 IST, inside the night window
DATE = "2026-10-02"
AFTER_DEADLINE = 1_790_904_600.0         # 2026-10-02 07:00 IST: the night is over
REGISTRY = Registry([Entity("langgraph", "LangGraph", "framework", (), True), Entity("mcp", "MCP", "protocol", (), True)])
SOURCES = [Source(id="gh-x", type="github_releases", tier="official", repo="a/x", package="pypi:x"),
           Source(id="gh-y", type="github_releases", tier="official", repo="a/y", package="pypi:y")]
DIFF = {"breaking": [{"symbol": "pkg.run", "change": "signature_changed", "severity": "breaking",
                      "detail": "new required parameter 'b'"}], "deprecations": [], "other": [], "added": [],
        "counts": {}, "truncated": False}


@pytest.fixture
def db(conn):
    store.ensure_schema(conn)
    return conn


def seed(db, now=NOW):
    add = lambda item: store.add_item(db, item, now)
    release = add({"source": "gh-x", "tier": "official", "kind": "release", "url": "https://github.com/a/x/releases/tag/v1.1.0",
                   "title": "x 1.1.0", "body": "Notes for 1.1.0", "published": now - 3600, "package": "pypi:x",
                   "version": "1.1.0", "repo": "a/x", "stars": 5, "extra": {"tag": "v1.1.0", "prerelease": False}})
    hn = add({"source": "hn", "tier": "community", "kind": "discussion", "url": "https://example.com/agents",
              "title": "Show HN: a tiny LLM agent framework", "body": "", "published": now - 7200, "extra": {"points": 120}})
    paper = add({"source": "arxiv-ai", "tier": "community", "kind": "paper", "url": "https://arxiv.org/abs/2609.1",
                 "title": "Quantum chemistry on lattices", "body": "", "published": now - 7300})
    server = add({"source": "mcp-registry", "tier": "official", "kind": "server", "url": "https://github.com/acme/weather",
                  "title": "New MCP server: io.github.acme/weather", "body": "Weather tools", "published": now - 100,
                  "repo": "acme/weather", "extra": {"version": "1.0.0"}})
    db.execute("INSERT INTO radar_api_diffs(ecosystem, package, from_version, to_version, diff, created_at) "
               "VALUES ('pypi', 'x', '1.0.0', '1.1.0', ?, 1.0)", (json.dumps(DIFF),))
    for source in SOURCES:
        store.record_source(db, source.id, "official", "collector", True, None, now)
    return {"release": release, "hn": hn, "paper": paper, "server": server}


def notes_for(ids, **over):
    return json.dumps({"notes": [
        {"cluster": f"c{ids['release']}", "what_changed": "x 1.1.0 changes run().", "affects": ["mcp", "bogus-tag"],
         "impact": "minor", "migration_effort": "low", "action": "Pass b.", "citations": [f"i{ids['release']}"],
         "versions": ["1.1.0"], "entities": ["LangGraph", "Totally Unknown"]},
        {"cluster": f"c{ids['hn']}", "what_changed": "A small agent framework got attention.", "affects": [],
         "impact": "minor", "migration_effort": "none", "action": "Skim it.", "citations": [f"i{ids['hn']}"],
         "versions": [], "entities": []}], **over})


def run_job(db, backend, now=NOW, embedder=None, tmp=None, key="digest:2026-10-02"):
    types = {"radar.digest": JobType("radar", stages(db, backend=backend, embedder=embedder, registry=REGISTRY,
                                                     sources=SOURCES, data_root=tmp, model="fake", clock=lambda: now))}
    jobs.enqueue(db, "radar", "radar.digest", key, {"date": DATE}, now=now)
    return run_once(db, types, {"radar": 1.0}, "w", clock=lambda: now)


def published(tmp):
    release = load_current(tmp, "radar", DATASET)
    return release, json.loads(release.read(f"digests/{DATE}.json"))


def test_digest_date_and_deadline_follow_the_ist_night():
    assert digest_date(NOW) == DATE and digest_date(NOW + 5 * 3600) == DATE        # 04:30 IST, same morning
    assert digest_date(NOW - 12 * 3600) == "2026-10-01" and digest_deadline(DATE) == 1_790_901_000.0


def test_full_night_publishes_a_complete_validated_digest(db, tmp_path):
    ids = seed(db)
    backend = FakeBackend([notes_for(ids)])
    assert run_job(db, backend, tmp=tmp_path) == "done"
    release, digest = published(tmp_path)
    assert (digest["status"], digest["synthesis"], digest["sources"]) == ("full", "complete", {"checked": 2, "total": 2, "failed": []})
    by_id = {c["id"]: c for c in digest["clusters"]}
    assert set(by_id) == {f"c{ids['release']}", f"c{ids['hn']}"} and digest["considered"] == 3
    top = by_id[f"c{ids['release']}"]
    assert top["impact"] == "breaking" and top["synthesis"]["affects"] == ["mcp"] and top["synthesis"]["entities"] == ["langgraph"]
    assert top["facts"]["api_breaking"][0]["symbols"] == ["pkg.run"] and top["items"][0]["content_hash"]
    assert digest["clusters"][0]["id"] == top["id"]                                  # breaking sorts first
    assert digest["new_servers"] == [{"repo": "acme/weather", "name": "io.github.acme/weather",
                                      "description": "Weather tools", "url": "https://github.com/acme/weather"}]
    assert digest["validation"]["forced_breaking"] == 1 and digest["validation"]["unknown_entities"] == 1
    assert digest["provenance"]["prompt"] == synth.PROMPT_VERSION and digest["provenance"]["embedded"] is False
    assert store.items_unconsumed(db, 0, 100) == []                                  # every item consumed exactly once
    assert [r["ref"] for r in review.list_open(db, "radar")] == ["totallyunknown"]
    assert release.manifest["provenance"]["date"] == DATE


def test_embeddings_are_used_when_available_and_recorded(db, tmp_path):
    ids = seed(db)
    assert run_job(db, FakeBackend([notes_for(ids)]), embedder=FakeEmbedder(), tmp=tmp_path) == "done"
    _, digest = published(tmp_path)
    assert digest["provenance"]["embedded"] is True and f"c{ids['release']}" in {c["id"] for c in digest["clusters"]}
    assert db.execute("SELECT count(*) FROM radar_embeddings").fetchone()[0] == 3        # the three non-server items


def test_an_unreachable_embedder_degrades_to_title_similarity(db, tmp_path):
    from radar.embed import EmbedError

    class Down:
        name = "down"

        def embed(self, texts):
            raise EmbedError("ollama is not running")
    ids = seed(db)
    assert run_job(db, FakeBackend([notes_for(ids)]), embedder=Down(), tmp=tmp_path) == "done"
    assert published(tmp_path)[1]["provenance"]["embedded"] is False


def test_rerunning_a_date_never_rewrites_the_published_digest(db, tmp_path):
    ids = seed(db)
    run_job(db, FakeBackend([notes_for(ids)]), tmp=tmp_path)
    release, first = published(tmp_path)
    before = release.read(f"digests/{DATE}.json")
    store.add_item(db, {"source": "hn", "tier": "community", "kind": "discussion", "url": "https://example.com/late",
                        "title": "Late LLM agent story", "published": NOW - 10}, NOW)
    idle = FakeBackend([])
    assert run_job(db, idle, tmp=tmp_path, key="digest:again") == "done"
    again, _ = published(tmp_path)
    assert again.id == release.id and again.read(f"digests/{DATE}.json") == before
    assert idle.calls == []                                                           # no Claude quota spent on a date already out
    assert len(store.items_unconsumed(db, 0, 100)) == 1                              # the late item waits for tomorrow's digest


def test_quota_deferral_before_the_deadline_publishes_nothing_and_keeps_prepared_work(db, tmp_path):
    seed(db)
    assert run_job(db, FakeBackend([QuotaDeferred("night window")]), tmp=tmp_path) == "deferred"
    assert load_current(tmp_path, "radar", DATASET) is None
    assert {r["stage"] for r in db.execute("SELECT stage FROM stage_artifacts")} == {"prepare"}
    assert len(store.items_unconsumed(db, 0, 100)) == 4


def test_after_the_deadline_the_digest_is_published_unsynthesized(db, tmp_path):
    seed(db, now=AFTER_DEADLINE)
    assert run_job(db, FakeBackend([QuotaDeferred("quota")]), now=AFTER_DEADLINE, tmp=tmp_path) == "done"
    _, digest = published(tmp_path)
    assert digest["synthesis"] == "unsynthesized" and digest["provenance"]["unsynthesized_reason"] == "QuotaDeferred"
    assert all(c["synthesis"] is None for c in digest["clusters"])
    assert digest["clusters"][0]["impact"] == "breaking" and digest["clusters"][0]["facts"]["api_breaking"]


def test_expired_login_degrades_immediately(db, tmp_path):
    seed(db)
    assert run_job(db, FakeBackend([AuthExpired("login")]), tmp=tmp_path) == "done"
    _, digest = published(tmp_path)
    assert digest["synthesis"] == "unsynthesized" and digest["provenance"]["unsynthesized_reason"] == "AuthExpired"


def test_no_llm_backend_publishes_unsynthesized(db, tmp_path):
    seed(db)
    assert run_job(db, None, tmp=tmp_path) == "done"
    assert published(tmp_path)[1]["provenance"]["unsynthesized_reason"] == "no-llm"


def test_one_bad_batch_makes_the_digest_partial(db, tmp_path, monkeypatch):
    ids = seed(db)
    monkeypatch.setattr(synth, "BATCH_SIZE", 1)
    good = json.dumps({"notes": [json.loads(notes_for(ids))["notes"][0]]})
    assert run_job(db, FakeBackend([good, "this is not json"]), tmp=tmp_path) == "done"
    _, digest = published(tmp_path)
    assert digest["synthesis"] == "partial"
    assert sorted(c["synthesis"] is None for c in digest["clusters"]) == [False, True]


def test_missing_official_sources_make_the_digest_partial(db, tmp_path):
    seed(db)
    db.execute("UPDATE radar_sources SET last_ok=?, last_error='boom', consecutive_failures=2 WHERE source='gh-y'", (NOW - 5 * 86400,))
    comp = completeness(db, SOURCES, NOW)
    assert comp == {"status": "partial", "checked": 1, "total": 2, "failed": ["gh-y"]}
    run_job(db, None, tmp=tmp_path)
    assert published(tmp_path)[1]["status"] == "partial"


def test_completeness_counts_never_attempted_sources_as_failed_and_allows_one_miss_in_five(db):
    many = [Source(id=f"s{i}", type="feed", tier="official", url="https://x.test/f") for i in range(5)]
    for source in many[:4]:
        store.record_source(db, source.id, "official", "collector", True, None, NOW)
    comp = completeness(db, many, NOW)                          # s4 was never attempted
    assert comp["status"] == "full" and comp["checked"] == 4 and comp["failed"] == ["s4"]
    assert completeness(db, many[:4] + [Source(id="s9", type="feed", tier="official", url="https://x.test/f"),
                                        Source(id="s10", type="feed", tier="official", url="https://x.test/f")], NOW)["status"] == "partial"
    community = [Source(id="c1", type="hn", tier="community")]
    assert completeness(db, community, NOW)["status"] == "full"                  # only official sources gate


def test_a_source_that_failed_in_the_latest_run_makes_the_digest_partial_despite_an_earlier_success(db):
    seed(db)
    store.record_source(db, "gh-y", "official", "collector", False, "GitHub rate limited", NOW)
    comp = completeness(db, SOURCES, NOW)
    assert comp["status"] == "partial" and comp["checked"] == 1 and comp["failed"] == ["gh-y"]
