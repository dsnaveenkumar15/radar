import pytest

from core.events import consume
from radar import store
from radar.apisurface import refresh_api_surface
from radar.config import Source
from radar.errors import SourceError


class FakeFetcher:
    def __init__(self, versions, files, fail=None):
        self.versions_by_pkg, self.files_by_version, self.fail, self.downloads = versions, files, fail or {}, []

    def versions(self, ecosystem, name):
        if name in self.fail:
            raise self.fail[name]
        return [{"version": v, "published": None, "artifacts": []} for v in self.versions_by_pkg[name]]

    def files(self, ecosystem, name, info):
        self.downloads.append((name, info["version"]))
        return self.files_by_version[(name, info["version"])]


V1 = {"pkg/__init__.py": "def run(a): ...\ndef keep(): ...\n"}
V2 = {"pkg/__init__.py": "def run(a, b): ...\ndef keep(): ...\n"}
V3 = {"pkg/__init__.py": "def run(a, b): ...\ndef keep(): ...\ndef extra(): ...\n"}
SRC = Source(id="gh-x", type="github_releases", tier="official", repo="a/x", package="pypi:x", module="pkg", api=True)


@pytest.fixture
def db(conn):
    store.ensure_schema(conn)
    return conn


def heard(db):
    out = []
    consume(db, "t", lambda e: out.append(e.payload), types=["radar.breaking_change_detected"])
    return out


def test_first_run_snapshots_the_latest_two_stable_versions_and_reports_the_break(db):
    fetcher = FakeFetcher({"x": ["0.9.0", "1.0.0", "1.1.0", "2.0.0rc1"]}, {("x", "1.0.0"): V1, ("x", "1.1.0"): V2})
    assert refresh_api_surface(db, fetcher, [SRC], now=1.0) == {"pypi:x": "ok"}
    assert fetcher.downloads == [("x", "1.0.0"), ("x", "1.1.0")]
    assert heard(db) == [{"package": "pypi:x", "from_version": "1.0.0", "to_version": "1.1.0", "symbols": ["pkg.run"]}]
    assert refresh_api_surface(db, fetcher, [SRC], now=2.0) == {"pypi:x": "ok"}
    assert len(fetcher.downloads) == 2 and heard(db) == []          # nothing re-downloaded, nothing re-announced
    assert store.source_health(db, 2.0)[0]["source"] == "api:pypi:x" and store.source_health(db, 2.0)[0]["ok"]


def test_a_new_release_downloads_one_artifact_and_diffs_against_the_previous(db):
    files = {("x", "1.0.0"): V1, ("x", "1.1.0"): V2, ("x", "1.2.0"): V3}
    fetcher = FakeFetcher({"x": ["1.0.0", "1.1.0"]}, files)
    refresh_api_surface(db, fetcher, [SRC], now=1.0)
    heard(db)
    fetcher.versions_by_pkg["x"].append("1.2.0")
    refresh_api_surface(db, fetcher, [SRC], now=2.0)
    assert fetcher.downloads[2:] == [("x", "1.2.0")] and heard(db) == []     # additive only: no event
    row = db.execute("SELECT diff FROM radar_api_diffs WHERE from_version='1.1.0' AND to_version='1.2.0'").fetchone()
    assert '"pkg.extra"' in row["diff"]


def test_the_download_budget_spreads_work_across_runs(db):
    fetcher = FakeFetcher({"x": ["1.0.0", "1.1.0"]}, {("x", "1.0.0"): V1, ("x", "1.1.0"): V2})
    refresh_api_surface(db, fetcher, [SRC], now=1.0, max_downloads=1)
    assert fetcher.downloads == [("x", "1.0.0")] and heard(db) == []
    refresh_api_surface(db, fetcher, [SRC], now=2.0, max_downloads=1)
    assert fetcher.downloads[1:] == [("x", "1.1.0")] and len(heard(db)) == 1


def test_one_failing_package_does_not_stop_the_next_and_is_recorded(db):
    other = Source(id="gh-y", type="github_releases", tier="official", repo="a/y", package="pypi:y", module="pkg", api=True)
    fetcher = FakeFetcher({"x": [], "y": ["1.0.0"]}, {("y", "1.0.0"): V1}, fail={"x": SourceError("pypi 503")})
    report = refresh_api_surface(db, fetcher, [SRC, other], now=1.0)
    assert report == {"pypi:x": "error: pypi 503", "pypi:y": "ok"}
    assert [h["ok"] for h in store.source_health(db, 1.0)] == [False, True]


def test_empty_surface_is_an_error_not_a_silent_empty_snapshot(db):
    fetcher = FakeFetcher({"x": ["1.0.0"]}, {("x", "1.0.0"): {"other/__init__.py": "def f(): ..."}})
    report = refresh_api_surface(db, fetcher, [SRC], now=1.0)
    assert report["pypi:x"].startswith("error:") and "no public symbols" in report["pypi:x"]
    assert db.execute("SELECT count(*) FROM radar_api_snapshots").fetchone()[0] == 0


def test_sources_without_api_are_ignored(db):
    plain = Source(id="gh-z", type="github_releases", tier="official", repo="a/z", package="pypi:z")
    assert refresh_api_surface(db, FakeFetcher({}, {}), [plain], now=1.0) == {}


def test_an_unexpected_error_in_one_package_does_not_stop_the_others(db):
    other = Source(id="gh-y", type="github_releases", tier="official", repo="a/y", package="pypi:y", module="pkg", api=True)
    fetcher = FakeFetcher({"x": [], "y": ["1.0.0"]}, {("y", "1.0.0"): V1}, fail={"x": AttributeError("odd shape")})
    report = refresh_api_surface(db, fetcher, [SRC, other], now=1.0)
    assert report["pypi:x"].startswith("error:") and report["pypi:y"] == "ok"


def test_diffs_cover_every_version_released_since_the_last_run(db):
    v12 = {"pkg/__init__.py": "def run(a, b, c): ...\ndef keep(): ...\ndef extra(): ...\n"}
    v13 = {"pkg/__init__.py": "def run(a, b, c): ...\ndef keep(): ...\ndef extra(): ...\ndef more(): ...\n"}
    files = {("x", "1.0.0"): V1, ("x", "1.1.0"): V2, ("x", "1.2.0"): v12, ("x", "1.3.0"): v13}
    fetcher = FakeFetcher({"x": ["1.0.0", "1.1.0"]}, files)
    refresh_api_surface(db, fetcher, [SRC], now=1.0)
    heard(db)
    fetcher.versions_by_pkg["x"] += ["1.2.0", "1.3.0"]                 # two releases land between runs
    refresh_api_surface(db, fetcher, [SRC], now=2.0)
    assert heard(db) == [{"package": "pypi:x", "from_version": "1.1.0", "to_version": "1.2.0", "symbols": ["pkg.run"]}]
    assert db.execute("SELECT count(*) FROM radar_api_diffs").fetchone()[0] == 3
