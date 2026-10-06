import json

from radar import store
from radar.apidiff import diff_snapshots
from radar.changes import api_changes


def snap(names):
    return {"module": "x", "symbols": {n: {"kind": "function", "params": []} for n in names}, "skipped": []}


def seed(conn, versions):
    store.ensure_schema(conn)
    for v, names in versions:
        conn.execute("INSERT INTO radar_api_snapshots(ecosystem, package, version, symbols, created_at) "
                     "VALUES ('pypi','x',?,?,0)", (v, json.dumps(snap(names))))


def test_steps_follow_version_order_and_carry_the_change_kind(conn):
    seed(conn, [("1.10.0", ["x.a", "x.b"]), ("1.9.0", ["x.a", "x.b", "x.c"]), ("2.0.0", ["x.a", "x.b"]), ("2.1.0rc1", [])])
    data = api_changes(conn, 5.0)["packages"]["pypi:x"]
    assert data["versions"] == ["1.9.0", "1.10.0", "2.0.0"] and data["latest"] == "2.0.0"      # prerelease ignored
    assert [(s["from"], s["to"]) for s in data["steps"]] == [("1.9.0", "1.10.0"), ("1.10.0", "2.0.0")]
    assert data["steps"][0]["breaking"] == [{"symbol": "x.c", "change": "removed", "detail": "removed from the public API"}]
    assert data["latest_symbols"] == ["x.a", "x.b"]


def test_large_steps_are_not_truncated(conn):
    many = [f"x.f{i}" for i in range(150)]
    seed(conn, [("1.0.0", many + ["x.keep"]), ("2.0.0", ["x.keep"])])
    step = api_changes(conn, 5.0)["packages"]["pypi:x"]["steps"][0]
    assert len(step["breaking"]) == 150
    assert len(diff_snapshots(snap(many), snap([]))["breaking"]) == 100                    # stored diffs keep their cap


def test_radar_data_reads_the_published_file(tmp_path):
    from core.publish import publish
    from web.radar_site import RadarData
    publish(tmp_path, "radar", "digests", {"api_changes.json": json.dumps({"generated_at": 1, "packages": {"pypi:x": {}}}).encode()}, {})
    assert RadarData(tmp_path).api_changes()["packages"] == {"pypi:x": {}}
    assert RadarData(tmp_path / "missing").api_changes() == {"generated_at": 0, "packages": {}}


def test_one_corrupt_snapshot_does_not_block_the_other_packages(conn):
    seed(conn, [("1.0.0", ["x.a"]), ("2.0.0", ["x.a"])])
    conn.execute("INSERT INTO radar_api_snapshots(ecosystem, package, version, symbols, created_at) VALUES ('pypi','bad','1.0.0','{not json',0)")
    conn.execute("INSERT INTO radar_api_snapshots(ecosystem, package, version, symbols, created_at) VALUES ('pypi','bad','2.0.0','{}',0)")
    packages = api_changes(conn, 5.0)["packages"]
    assert "pypi:x" in packages and "pypi:bad" not in packages
