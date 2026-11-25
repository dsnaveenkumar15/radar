import httpx
import pytest

from core.jobs import enqueue
from core.worker import load_registry, load_schedulers, run_once
from radar import store
from radar.config import Source
from radar.jobs import (EMBED_JOB, ADVISORY_JOB, API_JOB, BACKFILL_JOB, COLLECT_JOB, DIGEST_JOB, ERRATA_JOB, AUX_JOB, TRENDS_JOB, build_job_types, schedule_periodic)

NOW = 1_790_800_000.0
NIGHT = 1_790_877_600.0                  # 2026-10-01 23:30 IST
DAY = NIGHT - 12 * 3600                  # 11:30 IST


class FakeGitHub:
    def releases(self, repo, per_page=30):
        return [{"tag": "v1.0.0", "name": "1.0.0", "url": f"https://github.com/{repo}/releases/tag/v1.0.0",
                 "published": "2026-09-30T10:00:00Z", "body": "notes", "prerelease": False}]

    def repo_meta(self, repo):
        return {"stars": 3}


def count(conn, job_type):
    return conn.execute("SELECT count(*) FROM jobs WHERE job_type=?", (job_type,)).fetchone()[0]


def test_periodic_jobs_are_idempotent_per_window_and_the_digest_is_night_only(conn):
    schedule_periodic(conn, DAY)
    schedule_periodic(conn, DAY + 60)
    assert [count(conn, j) for j in (COLLECT_JOB, API_JOB, ADVISORY_JOB, DIGEST_JOB, ERRATA_JOB)] == [1, 1, 1, 0, 1]
    schedule_periodic(conn, NIGHT)
    schedule_periodic(conn, NIGHT + 600)
    assert count(conn, DIGEST_JOB) == 1
    row = conn.execute("SELECT key, payload FROM jobs WHERE job_type=?", (DIGEST_JOB,)).fetchone()
    assert row["key"] == "digest:2026-10-02" and '"2026-10-02"' in row["payload"]


def test_the_worker_discovers_the_module(conn):
    registry = load_registry(["radar.jobs"], conn)
    assert set(registry) == {EMBED_JOB, COLLECT_JOB, API_JOB, BACKFILL_JOB, ADVISORY_JOB, DIGEST_JOB, ERRATA_JOB, AUX_JOB, TRENDS_JOB}
    assert [s.__module__ for s in load_schedulers(["radar.jobs"])] == ["radar.jobs"]
    assert [name for name, _ in registry[DIGEST_JOB].stages] == ["prepare", "synth", "validate", "publish"]


def test_collect_job_runs_end_to_end_with_fakes(conn):
    sources = [Source(id="gh-x", type="github_releases", tier="official", repo="a/x", package="pypi:x")]
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    types = build_job_types(conn, github=FakeGitHub(), client=client, sources=sources, use_llm=False, clock=lambda: NOW)
    enqueue(conn, "radar", COLLECT_JOB, "k", {}, now=NOW)
    assert run_once(conn, types, {"radar": 1.0}, "w", clock=lambda: NOW) == "done"
    assert [i["title"] for i in store.items_unconsumed(conn, 0, 10)] == ["x 1.0.0"]


def test_trends_job_is_registered_and_enqueued_once_per_week_in_the_night_window(conn):
    from radar.jobs import TRENDS_JOB, build_job_types, schedule_periodic
    from core.scheduler import in_night_window
    night = 1_790_877_600.0 - 3600 * 3        # inside 23:00-06:00 IST; adjust below if the helper disagrees
    while not in_night_window(night):
        night += 1800
    assert TRENDS_JOB in build_job_types(conn, use_llm=False, sources=[], clock=lambda: night)
    schedule_periodic(conn, night)
    schedule_periodic(conn, night + 600)
    rows = conn.execute("SELECT key FROM jobs WHERE job_type=?", (TRENDS_JOB,)).fetchall()
    assert len(rows) == 1 and rows[0]["key"].startswith("trends:")
