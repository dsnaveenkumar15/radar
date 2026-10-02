import json

import pytest

from core.publish import load_current
from radar import store
from radar.publishing import DATASET, package_facts, publish_digest, publish_errata
from tests.test_radar_site import NOW, REGISTRY, digest

DAY = 86400.0


@pytest.fixture
def db(conn):
    store.ensure_schema(conn)
    return conn


def snapshot(db, ecosystem, package, version):
    db.execute("INSERT INTO radar_api_snapshots(ecosystem, package, version, symbols, created_at) VALUES (?, ?, ?, '{}', 1.0)",
               (ecosystem, package, version))


def advisory(db, vid, package, severity, published):
    db.execute("INSERT INTO radar_advisories(id, ecosystem, package, severity, summary, published, first_seen) "
               "VALUES (?, 'pypi', ?, ?, 's', ?, 1.0)", (vid, package, severity, published))


def diff(db, old, new, breaking, created):
    db.execute("INSERT INTO radar_api_diffs(ecosystem, package, from_version, to_version, diff, created_at) "
               "VALUES ('pypi', 'langgraph', ?, ?, ?, ?)", (old, new, json.dumps({"counts": {"breaking": breaking}}), created))


def test_package_facts_summarise_versions_advisories_and_the_last_break(db):
    for v in ("1.2.11", "1.2.12", "1.3.0rc1"):
        snapshot(db, "pypi", "langgraph", v)
    snapshot(db, "npm", "@anthropic-ai/sdk", "0.131.0")
    advisory(db, "GHSA-new", "pypi:langgraph", "high", NOW - 10 * DAY)
    advisory(db, "GHSA-old", "pypi:langgraph", "low", NOW - 200 * DAY)
    advisory(db, "GHSA-other", "pypi:other", "critical", NOW - DAY)
    diff(db, "1.2.10", "1.2.11", 3, 10.0)
    diff(db, "1.2.11", "1.2.12", 0, 20.0)
    facts = package_facts(db, NOW)
    assert facts["generated_at"] == NOW and list(facts["packages"]) == ["npm:@anthropic-ai/sdk", "pypi:langgraph", "pypi:other"]
    lg = facts["packages"]["pypi:langgraph"]
    assert lg["latest_stable"] == "1.2.12"                                         # the release candidate is ignored
    assert lg["advisories"] == [{"id": "GHSA-new", "severity": "high", "published": NOW - 10 * DAY}]
    assert lg["last_breaking"] == {"from": "1.2.10", "to": "1.2.11"}               # the newest diff that really broke something
    assert facts["packages"]["npm:@anthropic-ai/sdk"] == {"latest_stable": "0.131.0", "advisories": [], "last_breaking": None}
    assert facts["packages"]["pypi:other"]["latest_stable"] is None and facts["packages"]["pypi:other"]["advisories"][0]["id"] == "GHSA-other"


def test_every_release_carries_packages_json_through_errata_republishing(db, tmp_path):
    snapshot(db, "pypi", "langgraph", "1.2.12")
    publish_digest(db, tmp_path, digest("2026-10-02"), [], REGISTRY, NOW)
    release = load_current(tmp_path, "radar", DATASET)
    assert json.loads(release.read("packages.json")) == package_facts(db, NOW)
    store.add_errata(db, "2026-10-02", "c1", "Corrected.", NOW)
    publish_errata(db, tmp_path, REGISTRY, NOW)
    after = load_current(tmp_path, "radar", DATASET)
    assert after.id != release.id and after.read("packages.json") == release.read("packages.json")
