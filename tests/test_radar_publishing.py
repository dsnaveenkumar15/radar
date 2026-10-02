import json

import pytest

from core.publish import load_current
from radar import store
from radar.config import Entity, Registry
from radar.publishing import DATASET, build_files, publish_digest

REGISTRY = Registry([Entity("mcp", "MCP", "protocol", (), True)])
NOW = 1_790_800_000.0


def digest(date, impact="minor", synthesis="complete", status="full"):
    return {"schema": 1, "date": date, "generated_at": NOW, "status": status, "synthesis": synthesis,
            "sources": {"checked": 1, "total": 1, "failed": []}, "provenance": {}, "validation": {}, "considered": 1,
            "clusters": [{"id": "c1", "title": "t", "impact": impact, "signal": {"score": 50}, "synthesis": None,
                          "facts": {}, "items": []}], "new_servers": []}


@pytest.fixture
def db(conn):
    store.ensure_schema(conn)
    return conn


def test_first_publish_writes_digest_index_and_sources(db, tmp_path):
    result = publish_digest(db, tmp_path, digest("2026-10-02", "breaking"), [{"source": "gh-x", "ok": True}], REGISTRY, NOW)
    assert result["published"] is True
    release = load_current(tmp_path, "radar", DATASET)
    assert set(release.manifest["files"]) == {"digests/2026-10-02.json", "errata.json", "index.json", "packages.json", "sources.json", "supersessions.json", "surface.json", "upgrade.json"}
    index = json.loads(release.read("index.json"))
    assert index["digests"] == [{"date": "2026-10-02", "status": "full", "synthesis": "complete", "clusters": 1, "breaking": 1}]
    sources = json.loads(release.read("sources.json"))
    assert sources["sources"] == [{"source": "gh-x", "ok": True}] and sources["methodology"]["stack_tags"] == ["mcp"]
    assert db.execute("SELECT date, status, synthesis FROM radar_digests").fetchone()["date"] == "2026-10-02"


def test_a_new_date_carries_old_digests_over_byte_for_byte(db, tmp_path):
    publish_digest(db, tmp_path, digest("2026-10-02"), [], REGISTRY, NOW)
    first = load_current(tmp_path, "radar", DATASET).read("digests/2026-10-02.json")
    publish_digest(db, tmp_path, digest("2026-10-03", status="partial"), [], REGISTRY, NOW + 86400)
    release = load_current(tmp_path, "radar", DATASET)
    assert release.read("digests/2026-10-02.json") == first
    assert [d["date"] for d in json.loads(release.read("index.json"))["digests"]] == ["2026-10-03", "2026-10-02"]


def test_republishing_a_date_is_a_no_op_even_with_different_content(db, tmp_path):
    publish_digest(db, tmp_path, digest("2026-10-02"), [], REGISTRY, NOW)
    before = load_current(tmp_path, "radar", DATASET)
    again = publish_digest(db, tmp_path, digest("2026-10-02", "breaking", synthesis="partial"), [], REGISTRY, NOW + 5)
    after = load_current(tmp_path, "radar", DATASET)
    assert again["published"] is False and after.id == before.id
    assert after.read("digests/2026-10-02.json") == before.read("digests/2026-10-02.json")


def test_build_files_ignores_non_digest_carry_over():
    files = build_files({"digests/2026-10-01.json": b'{"date": "2026-10-01", "status": "full", "synthesis": "complete", "clusters": []}',
                         "index.json": b"stale"}, digest("2026-10-02"), [], REGISTRY, NOW)
    assert set(files) == {"digests/2026-10-01.json", "digests/2026-10-02.json", "errata.json", "index.json", "sources.json"}
    assert json.loads(files["index.json"])["digests"][1]["date"] == "2026-10-01"
