from datetime import datetime, timezone

from radar.apisurface import backfill_api_surface, backfill_versions
from radar.config import Source
from tests.test_radar_apisurface import FakeFetcher, V1, V2, V3

DAY = 86400.0
NOW = 2_000_000_000.0


def info(v, days_ago):
    return {"version": v, "published": datetime.fromtimestamp(NOW - days_ago * DAY, timezone.utc).isoformat(), "artifacts": []}


def test_latest_patch_of_each_minor_inside_the_window():
    infos = [info("0.1.0", 900), info("0.2.0", 600), info("0.2.5", 590), info("0.3.0", 300), info("0.3.1", 290),
             info("0.3.2rc1", 280), info("1.0.0", 30)]
    assert backfill_versions("pypi", infos, NOW) == ["0.2.5", "0.3.1", "1.0.0"]


def test_backfill_is_capped_to_the_newest_minors():
    infos = [info(f"0.{m}.0", 10 * m) for m in range(30)]
    assert backfill_versions("pypi", infos, NOW, max_versions=15) == [f"0.{m}.0" for m in range(15, 30)]


def test_backfill_stores_snapshots_without_announcing_old_history(conn):
    from core.events import consume
    from radar import store
    store.ensure_schema(conn)
    src = Source(id="gh-x", type="github_releases", tier="official", repo="a/x", package="pypi:x", module="pkg", api=True)
    fetcher = FakeFetcher({"x": ["1.0.0", "1.1.0", "1.2.0"]},
                          {("x", "1.0.0"): V1, ("x", "1.1.0"): V2, ("x", "1.2.0"): V3})
    fetcher.versions = lambda eco, name: [info("1.0.0", 200), info("1.1.0", 100), info("1.2.0", 10)]
    report = backfill_api_surface(conn, fetcher, [src], NOW, max_downloads=12)
    assert report == {"pypi:x": "ok"}
    versions = [r["version"] for r in conn.execute("SELECT version FROM radar_api_snapshots ORDER BY version")]
    assert versions == ["1.0.0", "1.1.0", "1.2.0"]
    heard = []
    consume(conn, "t", lambda e: heard.append(e), types=["radar.breaking_change_detected"])
    assert heard == []          # history is grounding data, not news: api_changes.json diffs it on publish


def test_backfill_respects_the_download_budget(conn):
    from radar import store
    store.ensure_schema(conn)
    src = Source(id="gh-x", type="github_releases", tier="official", repo="a/x", package="pypi:x", module="pkg", api=True)
    fetcher = FakeFetcher({"x": []}, {("x", "1.0.0"): V1, ("x", "1.1.0"): V2, ("x", "1.2.0"): V3})
    fetcher.versions = lambda eco, name: [info("1.0.0", 200), info("1.1.0", 100), info("1.2.0", 10)]
    backfill_api_surface(conn, fetcher, [src], NOW, max_downloads=2)
    assert len(fetcher.downloads) == 2
