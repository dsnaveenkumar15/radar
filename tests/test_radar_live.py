import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_radar_site import SYNTH, client, cluster, digest, publish_digests, settings  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "web" / "static" / "radar-live.js"
NODE = shutil.which("node")


def test_script_uses_dom_apis_and_survives_blocked_storage():
    js = JS.read_text(encoding="utf-8")
    assert "innerHTML" not in js and "eval(" not in js and "document.write" not in js
    assert "localStorage" in js and "sessionStorage" in js and "catch" in js
    assert "dataset.affects" in js and "dataset.generated" in js and "textContent" in js


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_stack_matching_and_new_detection_logic():
    script = ("const m = require('./web/static/radar-live.js');"
              "console.log(JSON.stringify([m.matchesStack([], ['a']), m.matchesStack(['a','b'], []),"
              " m.matchesStack(['a'], ['b']), m.matchesStack(['a','b'], ['b']),"
              " m.isNew(10, 5), m.isNew(5, 10), m.isNew(10, 0), m.isNew(10, 10)]))")
    out = subprocess.run([NODE, "-e", script], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == [True, True, False, True, True, False, False, False]


def test_digest_page_carries_the_markup_the_script_needs(settings):
    publish_digests(settings.data_root, digest(clusters=[cluster("c1"), cluster("c2", synthesis=None)]))
    c = client(settings)
    html = c.get("/").text
    assert '<script src="/static/radar-live.js"></script>' in html
    assert 'id="stack"' in html and 'data-tags="mcp"' in html and "hidden" in html
    assert 'data-affects="mcp"' in html and 'data-affects=""' in html and 'data-generated="1790877600"' in html
    assert c.get("/static/radar-live.js").status_code == 200
