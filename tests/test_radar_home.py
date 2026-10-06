# tests/test_radar_home.py
import json

from fastapi.testclient import TestClient

from core.publish import publish
from radar.publishing import build_files
from tests.test_radar_site import NOW, REGISTRY, cluster, digest
from tests.webutil import make_settings
from web.app import create_app

UP = {"generated_at": NOW, "packages": {
    "pypi:langchain": {"latest_stable": "1.4.3", "diffs": [{"from": "0.3.30", "to": "1.0.8", "counts": {"breaking": 4}}], "advisories": []},
    "pypi:ollama": {"latest_stable": "0.6.3", "diffs": [], "advisories": []}}}


def site(tmp_path, clusters=None, upgrade=UP):
    settings = make_settings(tmp_path)
    files = build_files({}, digest("2026-10-02", clusters if clusters is not None else [cluster("c1", "breaking"), cluster("c2", "notable")]),
                        [], REGISTRY, NOW)
    if upgrade is not None:
        files["upgrade.json"] = json.dumps(upgrade).encode()
    publish(settings.data_root, "radar", "digests", files, {})
    return TestClient(create_app(settings), base_url="http://radar.localhost")


def test_home_is_the_instrument_panel_followed_by_the_full_digest(tmp_path):
    html = site(tmp_path).get("/").text
    for part in ('class="dash"', 'class="cell cell-digest"', 'class="sweep"', 'class="cell cell-agent"',
                 'class="cell cell-activity"', 'class="cell cell-packages"', 'id="digest"'):
        assert part in html, part
    assert html.count('class="pt pt-') == 2 and "pt-break" in html and "pt-ok" in html
    assert 'href="#c1"' in html and html.index('href="#c1"') < html.index('href="#c2"')
    assert html.index('class="dash"') < html.index('id="digest"') < html.index('id="c1"')
    assert "hero-copy" not in html                      # the generic hero is replaced by the instrument hero


def test_missing_upgrade_data_hides_the_sweep_and_packages_cells(tmp_path):
    html = site(tmp_path, upgrade=None).get("/").text
    assert 'class="sweep"' not in html and "cell-packages" not in html and 'class="cell cell-digest"' in html


def test_an_empty_digest_still_renders_the_dashboard(tmp_path):
    html = site(tmp_path, clusters=[]).get("/").text
    assert "Nothing cleared the bar this night." in html and 'class="dash"' in html


def test_dated_digest_pages_keep_their_own_layout(tmp_path):
    html = site(tmp_path).get("/digests/2026-10-02").text
    assert 'class="dash"' not in html and 'id="c1"' in html and "Digest 2026-10-02" in html


def test_the_hero_never_claims_zero_packages_when_the_data_is_missing(tmp_path):
    html = site(tmp_path, upgrade=None).get("/").text
    assert "packages tracked" not in html and "digest 2026-10-02" in html
