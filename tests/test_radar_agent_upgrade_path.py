from radar.agent.upgrade_path import plan_upgrade

G = {"package": "pypi:x", "used": "1.0.0", "start": "1.0.0", "latest": "3.0.0", "status": "outdated",
     "changes": [{"package": "pypi:x", "symbol": "x.a", "kind": "breaking", "to": "2.0.0"},
                 {"package": "pypi:x", "symbol": "x.b", "kind": "breaking", "to": "3.0.0"}]}
CANDS = [{"id": "c1", "change": G["changes"][0], "usage": {"path": "a.py", "line": 3}},
         {"id": "c2", "change": G["changes"][1], "usage": {"path": "b.py", "line": 9}}]
UP = {"versions": ["1.0.0", "1.5.0", "2.0.0", "3.0.0"],
      "advisories": [{"id": "GHSA-1", "severity": "high", "summary": "bad", "affected_versions": ["1.0.0"],
                      "undetermined_versions": [], "fixed_in": ["1.5.0"]}]}
BOTH = [{"candidate_id": "c1", "verified": True}, {"candidate_id": "c2", "verified": True}]


def test_safe_route_steps_difficulty_and_security():
    plan = plan_upgrade(G, BOTH, CANDS, UP)
    assert plan["safe_now"] == "1.5.0"
    assert plan["steps"] == [{"to": "2.0.0", "changes": 1, "files": 1}, {"to": "3.0.0", "changes": 2, "files": 2}]
    assert plan["difficulty"] == "Easy" and plan["files"] == 2 and plan["effort_hours"] == 1.5
    assert plan["security"][0]["id"] == "GHSA-1" and plan["minimum_secure"] == "1.5.0"


def test_unverified_findings_do_not_count():
    plan = plan_upgrade(G, [{"candidate_id": "c1", "verified": False}], CANDS, UP)
    assert plan["safe_now"] == "3.0.0" and plan["difficulty"] == "Easy" and plan["files"] == 0


def test_npm_versions():
    g = {**G, "package": "npm:x"}
    cands = [{**c, "change": {**c["change"], "package": "npm:x"}} for c in CANDS]
    plan = plan_upgrade(g, BOTH, cands, UP)
    assert plan["safe_now"] == "1.5.0" and plan["steps"][0]["to"] == "2.0.0" and plan["minimum_secure"] == "1.5.0"


def test_no_advisories():
    plan = plan_upgrade(G, BOTH, CANDS, {"versions": UP["versions"]})
    assert plan["security"] == [] and plan["minimum_secure"] is None


def test_fixed_in_older_than_used_is_ignored():
    up = {"versions": UP["versions"], "advisories": [{**UP["advisories"][0], "fixed_in": ["0.9.0"]}]}
    plan = plan_upgrade(G, BOTH, CANDS, up)
    assert plan["security"][0]["id"] == "GHSA-1" and plan["minimum_secure"] is None


def test_used_unknown_to_versions_list():
    plan = plan_upgrade({**G, "used": "1.2.0"}, BOTH, CANDS, UP)
    assert plan["safe_now"] == "1.5.0" and [s["to"] for s in plan["steps"]] == ["2.0.0", "3.0.0"]


def test_unparseable_versions_are_never_newest():
    plan = plan_upgrade(G, BOTH, CANDS, {"versions": ["1.5.0", "garbage"], "advisories": []})
    assert plan["safe_now"] == "1.5.0" and plan["steps"] == []
    plan = plan_upgrade({**G, "used": "weird"}, BOTH, CANDS, UP)
    assert plan["safe_now"] is None and plan["steps"] == []
