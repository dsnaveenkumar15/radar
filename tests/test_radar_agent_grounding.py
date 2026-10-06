from radar.agent.grounding import ground, untracked
from radar.agent.types import Dependency

CHANGES = {"packages": {"pypi:x": {
    "versions": ["1.0.0", "1.1.0", "2.0.0"], "latest": "2.0.0", "latest_symbols": ["x.a", "x.back"],
    "steps": [
        {"from": "1.0.0", "to": "1.1.0", "breaking": [{"symbol": "x.gone", "change": "removed", "detail": "removed"},
                                                      {"symbol": "x.back", "change": "removed", "detail": "removed"}],
         "deprecations": [{"symbol": "x.old", "change": "deprecated", "detail": "now deprecated"}]},
        {"from": "1.1.0", "to": "2.0.0", "breaking": [{"symbol": "x.a", "change": "params", "detail": "b is now required"},
                                                      {"symbol": "x.gone", "change": "removed", "detail": "removed"}],
         "deprecations": []}]}}}


def dep(version):
    return Dependency("pypi", "x", version, f"x=={version}", "requirements.txt")


def test_changes_are_chained_from_the_version_in_use_and_readded_symbols_dropped():
    [g] = ground([dep("1.0.0")], CHANGES)
    assert g["status"] == "outdated" and g["latest"] == "2.0.0"
    symbols = {(c["symbol"], c["kind"]) for c in g["changes"]}
    assert symbols == {("x.gone", "breaking"), ("x.old", "deprecation"), ("x.a", "breaking")}
    assert next(c for c in g["changes"] if c["symbol"] == "x.gone")["to"] == "1.1.0"      # first step that breaks it


def test_only_later_steps_apply_and_untracked_versions_use_the_nearest_older_one():
    [g] = ground([dep("1.1.5")], CHANGES)
    assert g["start"] == "1.1.0" and "nearest" in g["note"] and {c["symbol"] for c in g["changes"]} == {"x.a", "x.gone"}


def test_current_unpinned_and_older_than_data():
    assert ground([dep("2.0.0")], CHANGES)[0]["status"] == "current"
    assert ground([Dependency("pypi", "x", None, "x>=1", "r.txt")], CHANGES)[0]["status"] == "unpinned"
    [old] = ground([dep("0.5.0")], CHANGES)
    assert old["start"] == "1.0.0" and "older than" in old["note"]


def test_untracked_packages_are_listed_not_guessed():
    deps = [dep("1.0.0"), Dependency("pypi", "requests", "2.0.0", "requests==2.0.0", "r.txt")]
    assert untracked(deps, CHANGES) == ["pypi:requests"] and len(ground(deps, CHANGES)) == 1


def test_unparseable_versions_are_never_reported_current():
    npm = {"packages": {"npm:y": {"versions": ["1.0.0", "2.0.0"], "latest": "2.0.0", "steps": []}}}
    assert ground([Dependency("npm", "y", "garbage", "y@garbage", "p.json")], npm)[0]["status"] == "unpinned"
    assert ground([dep("not a version")], CHANGES)[0]["status"] == "unpinned"
    bad = {"packages": {"pypi:x": {"versions": ["1.0.0"], "latest": "zzz", "steps": []}}}
    assert ground([dep("1.0.0")], bad)[0]["status"] == "no_data"
