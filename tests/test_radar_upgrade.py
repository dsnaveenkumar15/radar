import json

import pytest

from radar import store
from radar.upgrade import upgrade_facts

NOW = 1_790_877_600.0


@pytest.fixture
def db(conn):
    store.ensure_schema(conn)
    return conn


def snapshot(db, package, version):
    eco, _, name = package.partition(":")
    db.execute("INSERT INTO radar_api_snapshots(ecosystem, package, version, symbols, created_at) VALUES (?, ?, ?, '{}', 1)",
               (eco, name, version))


def diff(db, package, old, new, breaking=(), deprecations=(), counts=None):
    eco, _, name = package.partition(":")
    body = {"breaking": [{"symbol": s, "detail": d, "change": "removed", "severity": "breaking"} for s, d in breaking],
            "deprecations": [{"symbol": s, "detail": "now deprecated"} for s in deprecations], "other": [], "added": [],
            "counts": counts or {"breaking": len(breaking), "deprecations": len(deprecations), "added": 0, "removed": 0},
            "truncated": False}
    db.execute("INSERT INTO radar_api_diffs(ecosystem, package, from_version, to_version, diff, created_at) VALUES (?, ?, ?, ?, ?, 1)",
               (eco, name, old, new, json.dumps(body)))


def advisory(db, package, vid, affected, severity="high", published=NOW - 86400):
    eco = package.partition(":")[0]
    db.execute("INSERT INTO radar_advisories(id, ecosystem, package, severity, summary, aliases, published, affected, first_seen) "
               "VALUES (?, ?, ?, ?, ?, '[]', ?, ?, 1)", (vid, eco, package, severity, f"{vid} summary", published, json.dumps(affected)))


def populated(db):
    for v in ("1.0.0", "1.1.0", "1.2.0"):
        snapshot(db, "pypi:libx", v)
    snapshot(db, "pypi:libx", "1.3.0rc1")
    diff(db, "pypi:libx", "1.0.0", "1.1.0", breaking=[("libx.run", "removed from the public API")], deprecations=["libx.old"])
    diff(db, "pypi:libx", "1.1.0", "1.2.0")
    advisory(db, "pypi:libx", "GHSA-1", [{"ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "1.0.0"}, {"fixed": "1.1.0"}]}]}])


def test_each_tracked_package_lists_its_stable_versions_diffs_and_latest(db):
    populated(db)
    p = upgrade_facts(db, NOW, [])["packages"]["pypi:libx"]
    assert p["versions"] == ["1.0.0", "1.1.0", "1.2.0"] and p["latest_stable"] == "1.2.0"
    assert [(d["from"], d["to"]) for d in p["diffs"]] == [("1.0.0", "1.1.0"), ("1.1.0", "1.2.0")]
    first = p["diffs"][0]
    assert first["breaking"] == [{"symbol": "libx.run", "detail": "removed from the public API"}]
    assert first["deprecations"] == [{"symbol": "libx.old", "detail": "now deprecated"}] and first["counts"]["breaking"] == 1


def test_advisories_carry_the_known_versions_they_affect_and_where_they_are_fixed(db):
    populated(db)
    adv = upgrade_facts(db, NOW, [])["packages"]["pypi:libx"]["advisories"]
    assert adv == [{"id": "GHSA-1", "severity": "high", "summary": "GHSA-1 summary", "published": NOW - 86400,
                    "affected_versions": ["1.0.0"], "undetermined_versions": [], "closed": True, "fixed_in": ["1.1.0"]}]


def test_long_diffs_are_capped_and_flagged(db):
    snapshot(db, "pypi:big", "1.0.0")
    snapshot(db, "pypi:big", "2.0.0")
    diff(db, "pypi:big", "1.0.0", "2.0.0", breaking=[(f"big.f{i}", "removed") for i in range(40)])
    d = upgrade_facts(db, NOW, [])["packages"]["pypi:big"]["diffs"][0]
    assert len(d["breaking"]) == 20 and d["truncated"] is True and d["counts"]["breaking"] == 40


def test_migrations_come_from_published_digests_that_carry_a_breaking_fact(db):
    populated(db)
    migration = {"language": "python", "before": "run(1)", "after": "run(1, 2)"}
    cluster = {"id": "c1", "title": "libx 1.1.0", "synthesis": {"migration": migration},
               "facts": {"api_breaking": [{"package": "pypi:libx", "from": "1.0.0", "to": "1.1.0", "symbols": ["libx.run"], "details": []}]}}
    noise = {"id": "c2", "title": "no fact", "synthesis": {"migration": migration}, "facts": {"api_breaking": []}}
    digests = [{"date": "2026-10-02", "clusters": [cluster, noise, "junk", {"id": "c3", "facts": None, "synthesis": None}]}]
    m = upgrade_facts(db, NOW, digests)["packages"]["pypi:libx"]["migrations"]
    assert m == [{"from": "1.0.0", "to": "1.1.0", "title": "libx 1.1.0", "date": "2026-10-02", "cluster": "c1",
                  "language": "python", "before": "run(1)", "after": "run(1, 2)"}]


def test_a_package_with_no_snapshot_is_absent_and_an_empty_database_gives_no_packages(db):
    advisory(db, "pypi:ghost", "GHSA-9", [])
    assert upgrade_facts(db, NOW, [])["packages"] == {}


def test_output_is_json_serialisable_and_deterministic(db):
    populated(db)
    a, b = upgrade_facts(db, NOW, []), upgrade_facts(db, NOW, [])
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_advisory_entries_for_other_packages_are_ignored(db):
    populated(db)
    other = {"package": {"name": "numexpr", "ecosystem": "PyPI"},
             "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "9.9"}]}]}
    mine = {"package": {"name": "libx", "ecosystem": "PyPI"},
            "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "1.1.0"}, {"fixed": "1.2.0"}]}]}
    advisory(db, "pypi:libx", "GHSA-2", [other, mine])
    adv = {a["id"]: a for a in upgrade_facts(db, NOW, [])["packages"]["pypi:libx"]["advisories"]}["GHSA-2"]
    assert adv["affected_versions"] == ["1.1.0"] and adv["fixed_in"] == ["1.2.0"]


def test_unevaluable_advisories_are_undetermined_not_safe(db):
    populated(db)
    advisory(db, "pypi:libx", "GHSA-G", [{"ranges": [{"type": "GIT", "events": [{"introduced": "0"}]}]}])
    adv = {a["id"]: a for a in upgrade_facts(db, NOW, [])["packages"]["pypi:libx"]["advisories"]}["GHSA-G"]
    assert adv["affected_versions"] == [] and adv["undetermined_versions"] == ["1.0.0", "1.1.0", "1.2.0"] and adv["closed"] is False


def test_the_same_vulnerability_under_two_ids_is_listed_once_and_old_ones_are_counted_not_lost(db, monkeypatch):
    from radar import upgrade
    populated(db)
    db.execute("INSERT INTO radar_advisories(id, ecosystem, package, severity, summary, aliases, published, affected, first_seen) "
               "VALUES ('PYSEC-1', 'pypi', 'pypi:libx', 'high', 'dup', '[\"GHSA-1\"]', ?, ?, 1)",
               (NOW - 100, json.dumps([{"ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "1.0.0"}, {"fixed": "1.1.0"}]}]}])))
    p = upgrade_facts(db, NOW, [])["packages"]["pypi:libx"]
    assert [a["id"] for a in p["advisories"]] == ["GHSA-1"] and p["advisories_omitted"] == 0
    monkeypatch.setattr(upgrade, "MAX_ADVISORIES", 2)
    for i in range(4):
        advisory(db, "pypi:libx", f"GHSA-X{i}", [], published=NOW - 1000 - i)
    p = upgrade_facts(db, NOW, [])["packages"]["pypi:libx"]
    assert len(p["advisories"]) == 2 and p["advisories_omitted"] == 3


def test_only_the_newest_diffs_are_published_and_the_rest_are_counted(db, monkeypatch):
    from radar import upgrade
    snapshot(db, "pypi:libx", "9.0.0")
    for i in range(7):
        diff(db, "pypi:libx", f"1.{i}.0", f"1.{i + 1}.0", breaking=[(f"libx.f{i}", "removed")])
    monkeypatch.setattr(upgrade, "MAX_DIFFS", 5)
    p = upgrade_facts(db, NOW, [])["packages"]["pypi:libx"]
    assert [d["to"] for d in p["diffs"]] == [f"1.{i}.0" for i in range(3, 8)] and p["diffs_omitted"] == 2
