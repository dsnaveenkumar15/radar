import json

import pytest

from core.publish import load_current, publish
from radar import store
from radar.config import Source, load_sources
from radar.publishing import DATASET, aux_files, build_files, publish_aux, surface_facts
from tests.test_radar_site import NOW, REGISTRY, digest

SYMBOLS = {"langgraph.graph.StateGraph": {"kind": "class"}, "langgraph.graph.StateGraph.add_node": {"kind": "method"},
           "langgraph.types.interrupt": {"kind": "function"}}


@pytest.fixture
def db(conn):
    store.ensure_schema(conn)
    return conn


def snapshot(db, package, version, symbols=SYMBOLS):
    eco, _, name = package.partition(":")
    db.execute("INSERT INTO radar_api_snapshots(ecosystem, package, version, symbols, created_at) VALUES (?, ?, ?, ?, 1)",
               (eco, name, version, json.dumps({"module": name, "symbols": symbols})))


def test_langgraph_is_marked_as_a_starter_package():
    starters = [s.package for s in load_sources() if s.options.get("starter")]
    assert starters == ["pypi:langgraph"]


def test_surface_lists_the_public_symbols_of_the_latest_stable_snapshot(db):
    snapshot(db, "pypi:langgraph", "1.2.0", {"old.Thing": {}})
    snapshot(db, "pypi:langgraph", "1.3.0")
    snapshot(db, "pypi:langgraph", "1.4.0rc1", {"rc.Thing": {}})
    snapshot(db, "pypi:other", "9.0.0")
    facts = surface_facts(db, NOW, ["pypi:langgraph"])
    assert facts["packages"] == {"pypi:langgraph": {"version": "1.3.0", "symbols": sorted(SYMBOLS)}}


def test_a_starter_package_without_a_snapshot_is_absent_and_junk_symbols_are_ignored(db):
    assert surface_facts(db, NOW, ["pypi:langgraph"])["packages"] == {}
    db.execute("INSERT INTO radar_api_snapshots(ecosystem, package, version, symbols, created_at) VALUES ('pypi', 'langgraph', '1.0.0', 'not json', 1)")
    db.execute("INSERT INTO radar_api_snapshots(ecosystem, package, version, symbols, created_at) VALUES ('pypi', 'langgraph', '1.1.0', '[]', 1)")
    assert surface_facts(db, NOW, ["pypi:langgraph"])["packages"] == {}


def test_symbol_lists_are_capped(db):
    snapshot(db, "pypi:langgraph", "1.0.0", {f"langgraph.s{i}": {} for i in range(25_000)})
    assert len(surface_facts(db, NOW, ["pypi:langgraph"])["packages"]["pypi:langgraph"]["symbols"]) == 20_000


def test_aux_files_include_surface_json_for_the_configured_starters(db):
    snapshot(db, "pypi:langgraph", "1.3.0")
    files = aux_files(db, [], NOW, sources=[Source("gh-langgraph", "github_releases", "official", repo="a/b", package="pypi:langgraph",
                                                  module="langgraph", api=True, options={"starter": True})])
    assert "surface.json" in files and "pypi:langgraph" in json.loads(files["surface.json"])["packages"]
    none = aux_files(db, [], NOW, sources=[])
    assert json.loads(none["surface.json"])["packages"] == {}


def test_publish_aux_ships_surface_json(db, tmp_path):
    publish(tmp_path, "radar", DATASET, build_files({}, digest(), [], REGISTRY, NOW), {})
    snapshot(db, "pypi:langgraph", "1.3.0")
    assert publish_aux(db, tmp_path, NOW)["published"] is True
    surface = json.loads(load_current(tmp_path, "radar", DATASET).read("surface.json"))
    assert surface["packages"]["pypi:langgraph"]["version"] == "1.3.0"
