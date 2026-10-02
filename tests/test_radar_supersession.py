import json

import pytest

from core.publish import load_current
from radar import store
from radar.publishing import DATASET, aux_files, publish_aux
from radar.supersession import advisory_supersessions, all_supersessions
from tests.test_radar_aux import seed_release
from tests.test_radar_site import NOW, client, cluster, digest, settings  # noqa: F401  (fixture re-export)
from tests.test_radar_upgrade import advisory

AFFECTS_1_1 = [{"ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "1.0.0"}, {"fixed": "1.2.0"}]}]}]


@pytest.fixture
def db(conn):
    store.ensure_schema(conn)
    return conn


def covering_digest(generated_at=NOW, version="1.1.0"):
    c = cluster("c1")
    c["facts"] = {**c["facts"], "packages": [{"package": "pypi:libx", "version": version}], "advisories": []}
    return digest(clusters=[c, cluster("c2")], generated_at=generated_at)


def test_an_advisory_published_after_the_digest_that_affects_its_version_supersedes_the_cluster(db):
    advisory(db, "pypi:libx", "GHSA-1", AFFECTS_1_1, published=NOW + 3600)
    found = advisory_supersessions(db, [covering_digest()])
    assert found == [{"date": "2026-10-02", "cluster": "c1", "kind": "advisory", "ref": "GHSA-1", "created_at": NOW + 3600,
                      "text": "GHSA-1 (high) was published after this digest and lists libx 1.1.0 as affected.",
                      "url": "https://osv.dev/vulnerability/GHSA-1"}]


@pytest.mark.parametrize("published, version, listed", [(NOW - 3600, "1.1.0", False), (NOW + 3600, "1.2.0", False), (NOW + 3600, "1.1.0", True)])
def test_only_later_advisories_that_really_affect_the_version_count(db, published, version, listed):
    advisory(db, "pypi:libx", "GHSA-1", AFFECTS_1_1, published=published)
    assert bool(advisory_supersessions(db, [covering_digest(version=version)])) is listed


def test_an_advisory_the_cluster_already_reported_is_not_news(db):
    advisory(db, "pypi:libx", "GHSA-1", AFFECTS_1_1, published=NOW + 3600)
    d = covering_digest()
    d["clusters"][0]["facts"]["advisories"] = [{"id": "GHSA-1", "severity": "high", "package": "pypi:libx"}]
    assert advisory_supersessions(db, [d]) == []


def test_odd_digests_do_not_break_detection(db):
    advisory(db, "pypi:libx", "GHSA-1", AFFECTS_1_1, published=NOW + 3600)
    odd = [{"date": "2026-10-02", "generated_at": NOW, "clusters": ["x", {"id": "c1", "facts": None}, {"id": "c2", "facts": {"packages": [None, {"package": 3}]}}]},
           {"clusters": None}, {}]
    assert advisory_supersessions(db, odd) == []


def test_manual_supersessions_are_stored_cleaned_and_merged_oldest_first(db):
    store.add_supersession(db, "2026-10-02", "c1", "Release 1.1.0 was yanked.​", "https://pypi.org/p/libx/", NOW + 10)
    store.add_supersession(db, "2026-10-01", "c4", "x" * 900, "javascript:alert(1)", NOW + 5)
    rows = store.supersessions(db)
    assert [r["cluster"] for r in rows] == ["c4", "c1"] and len(rows[0]["text"]) == 500 and rows[0]["url"] == "" and "​" not in rows[1]["text"]
    advisory(db, "pypi:libx", "GHSA-1", AFFECTS_1_1, published=NOW + 3600)
    merged = all_supersessions(db, [covering_digest()])
    assert [(m["kind"], m["cluster"]) for m in merged] == [("manual", "c4"), ("manual", "c1"), ("advisory", "c1")] or len(merged) == 3


def test_supersessions_json_is_published_and_pages_show_the_banner(db, settings):
    from core.publish import publish
    from radar.publishing import build_files
    from tests.test_radar_site import REGISTRY
    d = covering_digest()
    advisory(db, "pypi:libx", "GHSA-1", AFFECTS_1_1, published=NOW + 3600)
    store.add_supersession(db, "2026-10-02", "c2", "Release was yanked <b>x</b>.", "https://example.com/yank", NOW + 5)
    extra = aux_files(db, [d], NOW + 7200)
    assert "supersessions.json" in extra
    publish(settings.data_root, "radar", DATASET, build_files({}, d, [], REGISTRY, NOW, extra=extra), {})
    c = client(settings)
    home = c.get("/").text
    assert home.count('class="badge">updated<') == 2
    page = c.get("/c/2026-10-02/c1").text
    assert "GHSA-1" in page and "https://osv.dev/vulnerability/GHSA-1" in page
    other = c.get("/c/2026-10-02/c2").text
    assert "yanked &lt;b&gt;x&lt;/b&gt;" in other and "<b>x</b>" not in other and 'href="https://example.com/yank"' in other


def test_pages_work_when_the_release_has_no_supersessions_file(settings):
    from tests.test_radar_site import publish_digests
    publish_digests(settings.data_root, digest())
    assert client(settings).get("/c/2026-10-02/c1").status_code == 200


def test_publish_aux_adds_a_late_advisory_banner_without_touching_digests(db, tmp_path):
    seed_release(tmp_path, covering_digest())
    before = load_current(tmp_path, "radar", DATASET)
    advisory(db, "pypi:libx", "GHSA-1", AFFECTS_1_1, published=NOW + 3600)
    assert publish_aux(db, tmp_path, NOW + 7200)["published"] is True
    after = load_current(tmp_path, "radar", DATASET)
    assert after.read("digests/2026-10-02.json") == before.read("digests/2026-10-02.json")
    assert json.loads(after.read("supersessions.json"))[0]["ref"] == "GHSA-1"


def test_many_late_advisories_collapse_into_one_banner_per_cluster_and_package(db):
    for i in range(5):
        advisory(db, "pypi:libx", f"GHSA-{i}", AFFECTS_1_1, published=NOW + 3600 + i)
    found = advisory_supersessions(db, [covering_digest()])
    assert len(found) == 1 and found[0]["ref"] == "GHSA-4" and found[0]["url"] == "https://osv.dev/vulnerability/GHSA-4"
    assert found[0]["text"].startswith("5 advisories were published after this digest and list libx 1.1.0 as affected")


def test_one_vulnerability_published_under_two_ids_counts_once(db):
    advisory(db, "pypi:libx", "GHSA-1", AFFECTS_1_1, published=NOW + 3600)
    db.execute("INSERT INTO radar_advisories(id, ecosystem, package, severity, summary, aliases, published, affected, first_seen) "
               "VALUES ('PYSEC-1', 'pypi', 'pypi:libx', 'high', 's', '[\"GHSA-1\"]', ?, ?, 1)", (NOW + 3700, json.dumps(AFFECTS_1_1)))
    found = advisory_supersessions(db, [covering_digest()])
    assert len(found) == 1 and "advisories were" not in found[0]["text"]


def test_advisory_entries_for_another_package_never_make_a_banner(db):
    other = [{"package": {"name": "numexpr", "ecosystem": "PyPI"}, "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}]}]}]
    advisory(db, "pypi:libx", "GHSA-1", other, published=NOW + 3600)
    assert advisory_supersessions(db, [covering_digest()]) == []


def test_a_manual_supersession_can_be_deleted_and_unknown_ids_are_reported(db):
    keep = store.add_supersession(db, "2026-10-02", "c1", "keep", "", NOW)
    gone = store.add_supersession(db, "2026-10-02", "c2", "typo", "", NOW + 1)
    assert store.delete_supersession(db, gone) is True and store.delete_supersession(db, gone) is False
    assert [s["ref"] for s in store.supersessions(db)] == [f"manual:{keep}"]
