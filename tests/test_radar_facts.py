import json

import pytest

from radar import store
from radar.facts import cluster_facts, package_surface

DIFF = {"breaking": [{"symbol": "pkg.run", "change": "signature_changed", "severity": "breaking",
                      "detail": "new required parameter 'b'"}],
        "deprecations": [{"symbol": "pkg.old", "change": "deprecated", "severity": "notable", "detail": "now deprecated"}],
        "other": [], "added": [], "counts": {}, "truncated": False}


@pytest.fixture
def db(conn):
    store.ensure_schema(conn)
    conn.execute("INSERT INTO radar_api_diffs(ecosystem, package, from_version, to_version, diff, created_at) "
                 "VALUES ('pypi', 'x', '1.0.0', '1.1.0', ?, 1.0)", (json.dumps(DIFF),))
    conn.execute("INSERT INTO radar_api_snapshots(ecosystem, package, version, symbols, created_at) "
                 "VALUES ('pypi', 'x', '1.1.0', ?, 1.0)",
                 (json.dumps({"module": "pkg", "symbols": {"pkg.run": {"kind": "function", "params": []}}}),))
    return conn


def release(version="1.1.0", prerelease=False, package="pypi:x"):
    return {"id": 1, "kind": "release", "package": package, "version": version, "extra": {"prerelease": prerelease}}


def test_release_with_a_diff_yields_breaking_and_deprecation_facts(db):
    facts = cluster_facts(db, [release(version="v1.1.0".lstrip("v"))])
    assert facts["api_breaking"] == [{"package": "pypi:x", "from": "1.0.0", "to": "1.1.0", "symbols": ["pkg.run"],
                                      "details": [{"symbol": "pkg.run", "detail": "new required parameter 'b'"}]}]
    assert facts["api_deprecations"] == [{"package": "pypi:x", "from": "1.0.0", "to": "1.1.0", "symbols": ["pkg.old"]}]
    assert facts["packages"] == [{"package": "pypi:x", "version": "1.1.0"}] and facts["versions"] == ["1.0.0", "1.1.0"]


def test_prereleases_and_other_versions_get_no_api_facts(db):
    assert cluster_facts(db, [release(prerelease=True)])["api_breaking"] == []
    assert cluster_facts(db, [release(version="9.9.9")])["api_breaking"] == []
    assert cluster_facts(db, [release(package="pypi:unknown")])["api_breaking"] == []


def test_advisory_items_become_advisory_facts(db):
    item = {"id": 2, "kind": "advisory", "package": "pypi:x", "version": None,
            "extra": {"id": "GHSA-1", "severity": "high"}}
    assert cluster_facts(db, [item])["advisories"] == [{"id": "GHSA-1", "severity": "high", "package": "pypi:x"}]


def test_package_surface_matches_versions_loosely(db):
    surface = package_surface(db, "pypi:x", "v1.1.0")
    assert surface == {"ecosystem": "pypi", "module": "pkg", "symbols": {"pkg.run"}}
    assert package_surface(db, "pypi:x", "2.0.0") is None and package_surface(db, "npm:nope", "1.0.0") is None
