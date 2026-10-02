import json

import pytest

from core.publish import load_current, publish
from radar import store
from radar.jobs import AUX_JOB, build_job_types, schedule_periodic
from radar.publishing import DATASET, aux_files, build_files, digests_in, publish_aux, publish_digest
from tests.test_radar_site import NOW, REGISTRY, cluster, digest
from tests.test_radar_upgrade import advisory, diff, populated, snapshot


@pytest.fixture
def db(conn):
    store.ensure_schema(conn)
    return conn


def seed_release(tmp_path, *digests):
    files = {}
    for d in digests:
        files = build_files(files, d, [], REGISTRY, NOW)
    return publish(tmp_path, "radar", DATASET, files, {})


def test_aux_files_are_packages_and_upgrade_json(db):
    populated(db)
    files = aux_files(db, [], NOW)
    assert set(files) == {"packages.json", "upgrade.json", "supersessions.json", "surface.json"}
    assert "pypi:libx" in json.loads(files["upgrade.json"])["packages"]


def test_publish_digest_ships_the_aux_files_in_the_same_release(db, tmp_path):
    populated(db)
    publish_digest(db, tmp_path, digest(), [], REGISTRY, NOW)
    release = load_current(tmp_path, "radar", DATASET)
    assert {"upgrade.json", "packages.json", "index.json"} <= set(release.manifest["files"])


def test_publish_aux_adds_new_data_without_touching_digest_bytes(db, tmp_path):
    seed_release(tmp_path, digest())
    before = load_current(tmp_path, "radar", DATASET)
    populated(db)
    result = publish_aux(db, tmp_path, NOW + 60)
    after = load_current(tmp_path, "radar", DATASET)
    assert result["published"] is True and after.id != before.id
    assert after.read("digests/2026-10-02.json") == before.read("digests/2026-10-02.json")
    assert "pypi:libx" in json.loads(after.read("upgrade.json"))["packages"]


def test_publish_aux_twice_with_nothing_new_publishes_once(db, tmp_path):
    seed_release(tmp_path, digest())
    populated(db)
    first = publish_aux(db, tmp_path, NOW)
    again = publish_aux(db, tmp_path, NOW + 3600)                  # only generated_at differs
    assert first["published"] is True and again == {"published": False, "release": first["release"]}


def test_publish_aux_picks_up_a_new_diff_and_keeps_errata(db, tmp_path):
    seed_release(tmp_path, digest())
    store.add_errata(db, "2026-10-02", "c1", "fixed", NOW)
    populated(db)
    publish_aux(db, tmp_path, NOW)
    snapshot(db, "pypi:libx", "1.4.0")
    diff(db, "pypi:libx", "1.2.0", "1.4.0", breaking=[("libx.y", "removed")])
    assert publish_aux(db, tmp_path, NOW + 60)["published"] is True
    release = load_current(tmp_path, "radar", DATASET)
    assert json.loads(release.read("upgrade.json"))["packages"]["pypi:libx"]["latest_stable"] == "1.4.0"


def test_publish_aux_with_no_release_says_so(db, tmp_path):
    assert publish_aux(db, tmp_path, NOW) == {"published": False, "reason": "nothing published yet"}


def test_the_aux_job_is_registered_scheduled_and_runs(db, tmp_path):
    seed_release(tmp_path, digest())
    populated(db)
    types = build_job_types(db, use_llm=False, data_root=tmp_path, sources=[], registry=REGISTRY, clock=lambda: NOW)
    assert AUX_JOB in types
    schedule_periodic(db, NOW)
    assert db.execute("SELECT count(*) FROM jobs WHERE job_type=?", (AUX_JOB,)).fetchone()[0] == 1
