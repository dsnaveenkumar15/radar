import pytest

from radar import store
from radar.config import Entity, Registry, alias_key
from radar.digest import stage_validate
from tests.test_radar_validate import NOW, REGISTRY, cluster, note


@pytest.fixture
def db(conn):
    store.ensure_schema(conn)
    return conn


def test_with_aliases_resolves_new_names_and_ignores_unknown_entities():
    registry = Registry([Entity("mcp", "MCP", "protocol", (), True)])
    merged = registry.with_aliases({alias_key("Model Ctx Proto"): "mcp", alias_key("Ghost"): "no-such-entity"})
    assert merged.resolve("model-ctx proto") == "mcp" and merged.resolve("ghost") is None
    assert registry.resolve("model ctx proto") is None                               # the original is untouched


def test_alias_store_normalises_and_replaces(db):
    store.add_alias(db, "Totally Unknown", "mcp", 1.0)
    store.add_alias(db, "totally-unknown", "langgraph", 2.0)
    assert store.aliases(db) == {"totallyunknown": "langgraph"}


def test_failing_sources_lists_failures_and_drift_only(db):
    store.record_source(db, "ok-src", "official", "collector", True, None, NOW)
    store.record_source(db, "bad-src", "official", "collector", False, "boom", NOW)
    store.record_source(db, "drift-src", "official", "collector", False, "schema drift: no entries", NOW)
    assert [h["source"] for h in store.failing_sources(db, NOW)] == ["bad-src", "drift-src"]


def test_stage_validate_applies_the_owners_aliases(db):
    store.add_alias(db, "Totally Unknown", "mcp", 1.0)
    out = stage_validate(db, {"notes": [note(entities=["totally-unknown"])]}, {"clusters": [cluster()]}, REGISTRY, NOW)
    assert out["accepted"]["c1"]["entities"] == ["mcp"] and "unknown_entities" not in out["report"]
