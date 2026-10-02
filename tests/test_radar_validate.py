import json

import pytest

from core import review
from radar import store
from radar.config import Entity, Registry
from radar.validate import validate_notes

NOW = 1_790_800_000.0
REGISTRY = Registry([Entity("langgraph", "LangGraph", "framework", (), True), Entity("mcp", "MCP", "protocol", (), True)])
BODY = "Breaking: removed foo(). Use bar() instead."


def cluster(cid="c1", facts=None, kind="release", body=BODY):
    return {"id": cid,
            "facts": {"api_breaking": [], "api_deprecations": [], "advisories": [], "packages": [], "versions": ["1.1.0"],
                      **(facts or {})},
            "items": [{"id": 1, "kind": kind, "body": body, "title": "x 1.1.0", "tier": "official", "source": "gh",
                       "package": "pypi:x", "version": "1.1.0", "extra": {}},
                      {"id": 2, "kind": "discussion", "body": "Someone said: it is all fine.", "title": "hn", "tier": "community",
                       "source": "hn", "package": None, "version": None, "extra": {}}]}


def note(**over):
    base = {"cluster": "c1", "what_changed": "x 1.1.0 changed.", "affects": ["mcp"], "impact": "notable",
            "migration_effort": "low", "action": "Update.", "citations": ["i1"], "versions": ["1.1.0"], "entities": []}
    return {**base, **over}


@pytest.fixture
def db(conn):
    store.ensure_schema(conn)
    return conn


def run(db, notes, clusters=None):
    return validate_notes(db, notes, clusters or [cluster()], REGISTRY, NOW)


def test_clean_note_is_accepted_with_deterministic_fields_filtered(db):
    accepted, report = run(db, [note(affects=["mcp", "bogus"], entities=["LangGraph", "langgraph"])])
    assert accepted["c1"]["affects"] == ["mcp"] and accepted["c1"]["entities"] == ["langgraph"]
    assert report["unknown_stack_tags"] == 1 and accepted["c1"]["migration"] is None and accepted["c1"]["quote"] is None


def test_unknown_duplicate_and_invalid_notes_are_dropped(db):
    accepted, report = run(db, [note(cluster="c9"), note(), note(what_changed="second"), {"cluster": "c1"}])
    assert list(accepted) == ["c1"] and accepted["c1"]["what_changed"] == "x 1.1.0 changed."
    assert (report["unknown_cluster"], report["duplicate"], report["invalid_note"]) == (1, 1, 1)


def test_citations_must_exist_in_the_cluster(db):
    accepted, report = run(db, [note(citations=["i1", "i99"])])
    assert accepted["c1"]["citations"] == ["i1"] and report["bad_citations"] == 1
    accepted, report = run(db, [note(citations=["i99"])])
    assert accepted == {} and report["no_valid_citation"] == 1


def test_versions_must_come_from_the_parsed_data(db):
    assert run(db, [note(versions=["v1.1.0"])])[0] != {}                  # a leading v is the same version
    accepted, report = run(db, [note(versions=["1.1.0", "7.7.7"])])
    assert accepted == {} and report["unknown_version"] == 1


def test_breaking_needs_an_api_break_or_a_verified_release_quote(db):
    accepted, report = run(db, [note(impact="breaking")])
    assert accepted["c1"]["impact"] == "notable" and report["downgraded_breaking"] == 1
    good = {"item": "i1", "quote": "removed foo(). Use bar() instead"}
    assert run(db, [note(impact="breaking", quote=good)])[0]["c1"]["impact"] == "breaking"
    assert run(db, [note(impact="breaking", quote=good)])[0]["c1"]["quote"] == good
    invented = {"item": "i1", "quote": "this was never written"}
    assert run(db, [note(impact="breaking", quote=invented)])[0]["c1"]["impact"] == "notable"
    from_forum = {"item": "i2", "quote": "it is all fine"}
    assert run(db, [note(impact="breaking", quote=from_forum, citations=["i1", "i2"])])[0]["c1"]["impact"] == "notable"


def test_an_api_break_forces_breaking_even_if_the_model_says_minor(db):
    facts = {"api_breaking": [{"package": "pypi:x", "from": "1.0.0", "to": "1.1.0", "symbols": ["pkg.run"], "details": []}]}
    accepted, report = run(db, [note(impact="minor")], [cluster(facts=facts)])
    assert accepted["c1"]["impact"] == "breaking" and report["forced_breaking"] == 1


def test_unknown_entities_go_to_the_review_queue_once(db):
    accepted, report = run(db, [note(entities=["LangGraph", "Totally Unknown Tool"])])
    assert accepted["c1"]["entities"] == ["langgraph"] and report["unknown_entities"] == 1
    run(db, [note(entities=["totally-unknown tool"])])
    rows = review.list_open(db, "radar")
    assert [(r["kind"], r["ref"]) for r in rows] == [("unknown-entity", "totallyunknowntool")]
    assert json.loads(rows[0]["payload"]) == {"cluster": "c1", "name": "Totally Unknown Tool"}


def test_migration_is_kept_only_when_it_parses_and_uses_real_symbols(db):
    db.execute("INSERT INTO radar_api_snapshots(ecosystem, package, version, symbols, created_at) VALUES ('pypi', 'x', '1.1.0', ?, 1.0)",
               (json.dumps({"module": "pkg", "symbols": {"pkg.bar": {"kind": "function", "params": []}}}),))
    facts = {"packages": [{"package": "pypi:x", "version": "1.1.0"}]}
    good = {"language": "python", "before": "from pkg import foo\nfoo()", "after": "from pkg import bar\nbar()"}
    accepted, report = run(db, [note(migration=good)], [cluster(facts=facts)])
    assert accepted["c1"]["migration"] == good and "migration_dropped" not in report
    bad = {"language": "python", "before": "x = 1", "after": "from pkg import nothing\nnothing()"}
    accepted, report = run(db, [note(migration=bad)], [cluster(facts=facts)])
    assert accepted["c1"]["migration"] is None and report["migration_dropped"] == 1 and "nothing" in report["reasons"][0]
    accepted, report = run(db, [note(migration=good)], [cluster()])         # no snapshot known for this cluster
    assert accepted["c1"]["migration"] is None and "no API snapshot" in report["reasons"][0]


def test_free_text_may_only_mention_versions_that_exist_in_the_data(db):
    assert run(db, [note(what_changed="x 1.1.0 changes run().", action="Upgrade soon.")])[0] != {}
    accepted, report = run(db, [note(what_changed="x 9.9.9 is out and changes run().")])
    assert accepted == {} and report["unknown_version_in_text"] == 1
    accepted, report = run(db, [note(action="Upgrade from 1.0 first.")])
    assert accepted == {} and report["unknown_version_in_text"] == 1
    grounded = cluster(body=BODY + " Needs Python 3.12.")                       # 3.12 appears in the release notes
    assert run(db, [note(action="Requires Python 3.12.")], [grounded])[0] != {}


def test_urls_in_free_text_are_stripped(db):
    accepted, report = run(db, [note(what_changed="See https://evil.test/x?a=1 for details.", action="Read http://evil.test now")])
    assert "evil.test" not in accepted["c1"]["what_changed"] + accepted["c1"]["action"]
    assert accepted["c1"]["what_changed"] == "See for details." and report["urls_removed"] == 2


def test_a_breaking_quote_must_be_substantial_and_describe_a_break(db):
    body = "Bug fixes and many improvements to the docs. Breaking: removed foo() now."
    c = cluster(body=body)
    short = {"item": "i1", "quote": "Bug fixes"}
    bland = {"item": "i1", "quote": "Bug fixes and many improvements to the docs"}
    real = {"item": "i1", "quote": "Breaking: removed foo() now."}
    assert run(db, [note(impact="breaking", quote=short)], [c])[0]["c1"]["impact"] == "notable"
    assert run(db, [note(impact="breaking", quote=bland)], [c])[0]["c1"]["impact"] == "notable"
    assert run(db, [note(impact="breaking", quote=real)], [c])[0]["c1"]["impact"] == "breaking"


def test_a_snippet_that_breaks_the_checker_drops_only_the_snippet(db, monkeypatch):
    import radar.validate as module

    def explode(migration, surfaces):
        raise RecursionError("boom")
    monkeypatch.setattr(module, "check_migration", explode)
    db.execute("INSERT INTO radar_api_snapshots(ecosystem, package, version, symbols, created_at) VALUES ('pypi', 'x', '1.1.0', ?, 1.0)",
               (json.dumps({"module": "pkg", "symbols": {"pkg.bar": {"kind": "function", "params": []}}}),))
    facts = {"packages": [{"package": "pypi:x", "version": "1.1.0"}]}
    snippet = {"language": "python", "before": "x = 1", "after": "from pkg import bar"}
    accepted, report = run(db, [note(migration=snippet)], [cluster(facts=facts)])
    assert accepted["c1"]["migration"] is None and report["migration_dropped"] == 1
