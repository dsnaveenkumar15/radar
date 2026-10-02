import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core.publish import publish
from radar.publishing import build_files
from tests.test_radar_site import NOW, REGISTRY, client, digest, settings  # noqa: F401  (fixture re-export)
from radar.versions import sort_key

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
UPGRADE = {"generated_at": NOW, "packages": {"pypi:libx": {
    "ecosystem": "pypi", "name": "libx", "versions": ["1.0.0", "1.1.0", "1.2.0"], "latest_stable": "1.2.0",
    "diffs": [{"from": "1.0.0", "to": "1.1.0", "breaking": [{"symbol": "libx.run", "detail": "removed"}],
               "deprecations": [{"symbol": "libx.old", "detail": "now deprecated"}], "counts": {"breaking": 1, "deprecations": 1}, "truncated": False},
              {"from": "1.1.0", "to": "1.2.0", "breaking": [], "deprecations": [], "counts": {"breaking": 0, "deprecations": 0}, "truncated": True}],
    "advisories": [{"id": "GHSA-1", "severity": "high", "summary": "boom", "published": NOW, "affected_versions": ["1.0.0"], "undetermined_versions": [], "closed": True, "fixed_in": ["1.1.0"]},
                   {"id": "GHSA-0", "severity": "low", "summary": "old", "published": NOW, "affected_versions": ["0.5.0"], "undetermined_versions": [], "closed": True, "fixed_in": ["0.6.0"]}],
    "migrations": [{"from": "1.0.0", "to": "1.1.0", "title": "libx 1.1.0", "date": "2026-10-02", "cluster": "c1",
                    "language": "python", "before": "run(1)", "after": "run(1, 2)"}]}}}


def publish_upgrade(root, upgrade=UPGRADE):
    files = build_files({}, digest(), [], REGISTRY, NOW, extra={"upgrade.json": json.dumps(upgrade).encode()})
    publish(root, "radar", "digests", files, {})


def node(expression: str):
    script = f"const m = require('./web/static/radar-upgrade.js'); console.log(JSON.stringify({expression}));"
    return json.loads(subprocess.run([NODE, "-"], input=script, cwd=ROOT, capture_output=True, text=True, check=True).stdout)


def test_upgrade_json_is_served_from_the_release_and_empty_when_there_is_none(settings):
    assert client(settings).get("/upgrade.json").json() == {"generated_at": 0, "packages": {}}
    publish_upgrade(settings.data_root)
    assert client(settings).get("/upgrade.json").json()["packages"]["pypi:libx"]["latest_stable"] == "1.2.0"


def test_the_page_has_the_form_the_script_and_a_privacy_note(settings):
    html = client(settings).get("/upgrade").text
    assert 'id="upgrade-form"' in html and 'id="deps"' in html and 'id="upgrade-result"' in html
    assert '<script src="/static/radar-upgrade.js"></script>' in html and "never leaves your browser" in html
    assert client(settings).get("/static/radar-upgrade.js").status_code == 200
    assert 'href="/upgrade"' in html


def test_upgrade_routes_are_radar_only(settings):
    assert client(settings, "trust.localhost").get("/upgrade").status_code == 404


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_requirements_files_are_parsed_pinned_ranged_and_skipped():
    text = ("# comment\nLibX[extra]==1.2.0 ; python_version>='3.9'  # trailing\nrequests>=2.0,<3\nflask~=2.1\n"
            "-r other.txt\n-e .\nhttps://example.com/x.whl\ngit+https://github.com/a/b\nplain\nPyYAML_Thing==6.0 \\\n    --hash=sha256:abc\n")
    out = node(f"m.parseDeps({json.dumps(text)})")
    got = {d["name"]: [d["version"], d["exact"]] for d in out["deps"]}
    assert got == {"libx": ["1.2.0", True], "requests": ["2.0", False], "flask": ["2.1", False], "pyyaml-thing": ["6.0", True]}
    assert out["skipped"] >= 4


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_package_json_package_lock_and_toml_locks_are_parsed():
    pkg = json.dumps({"dependencies": {"left-pad": "^1.3.0", "@scope/x": "~2.0.1", "local": "file:../x", "git": "git+https://x/y",
                                       "star": "*", "ws": "workspace:^", "exact": "3.4.5"}, "devDependencies": {"dev": ">=4.1.0"}})
    lock = json.dumps({"lockfileVersion": 3, "packages": {"": {"name": "root"}, "node_modules/left-pad": {"version": "1.3.0"},
                                                         "node_modules/a/node_modules/@scope/x": {"version": "2.0.1"}}})
    toml = '[[package]]\nname = "LibX"\nversion = "1.2.0"\n\n[[package]]\nname = "other_pkg"\nversion = "0.1"\n'
    a, b, c = node(f"[m.parseDeps({json.dumps(pkg)}), m.parseDeps({json.dumps(lock)}), m.parseDeps({json.dumps(toml)})]")
    assert {d["name"]: [d["version"], d["exact"]] for d in a["deps"]} == {
        "left-pad": ["1.3.0", False], "@scope/x": ["2.0.1", False], "exact": ["3.4.5", True], "dev": ["4.1.0", False]}
    assert {d["ecosystem"] for d in a["deps"]} == {"npm"} and a["skipped"] == 4
    assert {d["name"]: d["version"] for d in b["deps"]} == {"left-pad": "1.3.0", "@scope/x": "2.0.1"} and all(d["exact"] for d in b["deps"])
    assert {d["name"]: d["version"] for d in c["deps"]} == {"libx": "1.2.0", "other-pkg": "0.1"} and c["deps"][0]["ecosystem"] == "pypi"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("text", ["", "   \n", "{not json", "[]", "null", "42", '{"dependencies": null}', '{"packages": 7}',
                                  "[[package]]", "\u0000\u0001", "x" * 200_000, "{" * 5000, "a==1\n" * 20000], ids=lambda t: repr(t[:12]))
def test_junk_input_gives_a_result_not_an_exception(text):
    out = node(f"m.parseDeps({json.dumps(text)})")
    assert isinstance(out["deps"], list) and isinstance(out["skipped"], int)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_version_comparison_agrees_with_the_python_ordering():
    cases = {"pypi": ["1.0", "1.0.0", "1.0.1", "1.0a1", "1.0b2", "1.0rc1", "1.0.dev1", "1.0.post1", "1.1", "2", "10.0", "1.10", "1.9", "0.9.9",
                      "1.0a1.dev1", "2.0.0rc1", "2.0.0"],
             "npm": ["1.0.0", "1.0.1", "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-beta", "1.0.0-rc.1", "1.10.0", "1.9.0", "2.0.0", "0.9.9",
                     "1.0.0-1", "1.0.0-alpha.beta"]}
    for eco, versions in cases.items():
        pairs = [(a, b) for a in versions for b in versions]
        js = node(f"{json.dumps(pairs)}.map(p => m.cmp({json.dumps(eco)}, p[0], p[1]))")
        for (a, b), got in zip(pairs, js):
            ka, kb = sort_key(eco, a), sort_key(eco, b)
            assert got == (ka > kb) - (ka < kb), (eco, a, b, got)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_report_collects_breaking_changes_between_the_pinned_and_the_latest_version():
    deps = [{"ecosystem": "pypi", "name": "libx", "version": "1.0.0", "exact": True}]
    row = node(f"m.buildReport({json.dumps(UPGRADE)}, {json.dumps(deps)})")[0]
    assert row["status"] == "behind" and row["latest"] == "1.2.0"
    assert row["breaking"] == [{"from": "1.0.0", "to": "1.1.0", "symbol": "libx.run", "detail": "removed"}]
    assert row["deprecations"][0]["symbol"] == "libx.old" and row["migrations"][0]["after"] == "run(1, 2)"
    assert [a["id"] for a in row["advisories"]] == ["GHSA-1"] and row["advisories"][0]["state"] == "affected"
    assert any("not listed" in n for n in row["notes"])


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_report_handles_current_unknown_old_new_and_untracked_versions():
    def run(version):
        deps = [{"ecosystem": "pypi", "name": "libx", "version": version, "exact": True}]
        return node(f"m.buildReport({json.dumps(UPGRADE)}, {json.dumps(deps)})")[0]
    current = run("1.2.0")
    assert current["status"] == "current" and current["breaking"] == [] and current["advisories"] == []
    newer = run("9.0.0")
    assert newer["status"] == "newer" and newer["breaking"] == []
    old = run("0.4.0")
    assert old["status"] == "behind" and any("starts at 1.0.0" in n for n in old["notes"])
    assert {a["id"]: a["state"] for a in old["advisories"]} == {"GHSA-1": "unknown", "GHSA-0": "unknown"}
    between = run("1.0.5")
    assert between["breaking"][0]["symbol"] == "libx.run" and {a["id"] for a in between["advisories"]} == {"GHSA-1"}
    gone = node(f"m.buildReport({json.dumps(UPGRADE)}, {json.dumps([{'ecosystem': 'pypi', 'name': 'nope', 'version': '1.0', 'exact': True}])})")[0]
    assert gone["tracked"] is False and gone["status"] == "untracked"
    unpinned = node(f"m.buildReport({json.dumps(UPGRADE)}, {json.dumps([{'ecosystem': 'pypi', 'name': 'libx', 'version': None, 'exact': False}])})")[0]
    assert unpinned["status"] == "unpinned"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_report_survives_odd_published_data():
    deps = [{"ecosystem": "pypi", "name": "libx", "version": "1.0.0", "exact": True}]
    for data in ({}, {"packages": None}, {"packages": {"pypi:libx": {"versions": None}}}, None, []):
        rows = node(f"m.buildReport({json.dumps(data)}, {json.dumps(deps)})")
        assert len(rows) == 1 and "status" in rows[0]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_realistic_five_thousand_package_lockfile_is_read_whole():
    packages = {"": {"name": "app"}}
    for i in range(5000):
        packages[f"node_modules/pkg-{i}"] = {"version": f"1.{i % 50}.0", "resolved": f"https://registry.npmjs.org/pkg-{i}/-/pkg-{i}-1.0.0.tgz",
                                             "integrity": "sha512-" + "A" * 88, "license": "MIT", "dependencies": {"a": "^1.0.0", "b": "^2.0.0"},
                                             "engines": {"node": ">=14"}}
    text = json.dumps({"lockfileVersion": 3, "packages": packages}, indent=2)
    assert len(text) > 1_500_000
    out = node(f"(function () {{ const r = m.parseDeps({json.dumps(text)}); return {{n: r.deps.length, truncated: r.truncated}}; }})()")
    assert out == {"n": 5000, "truncated": False}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_input_over_the_limit_is_flagged_instead_of_silently_cut():
    text = "a==1.0\n" * 1_200_000
    out = node(f"(function () {{ const r = m.parseDeps({json.dumps(text)}); return {{truncated: r.truncated, n: r.deps.length}}; }})()")
    assert out["truncated"] is True and out["n"] > 0
    assert 'maxlength="6000000"' in client_html()


def client_html():
    from fastapi.testclient import TestClient
    from tests.webutil import make_settings
    from web.app import create_app
    import tempfile
    return TestClient(create_app(make_settings(Path(tempfile.mkdtemp()))), base_url="http://radar.localhost").get("/upgrade").text


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_version_newer_than_radars_latest_or_with_no_data_is_never_called_current():
    def run(version, data=UPGRADE):
        deps = [{"ecosystem": "pypi", "name": "libx", "version": version, "exact": True}]
        return node(f"m.buildReport({json.dumps(data)}, {json.dumps(deps)})")[0]
    newer = run("9.0.0")
    assert newer["status"] == "newer" and any("Newer than" in n for n in newer["notes"]) and newer["advisories"] == []
    empty = {"packages": {"pypi:libx": {"ecosystem": "pypi", "name": "libx", "versions": [], "latest_stable": None, "diffs": [],
                                        "advisories": [], "migrations": []}}}
    assert run("1.0.0", empty)["status"] == "nodata"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_advisories_radar_cannot_settle_are_shown_as_check_manually():
    def adv(**over):
        base = {"id": "GHSA-Z", "severity": "high", "summary": "s", "published": NOW, "affected_versions": [], "undetermined_versions": [],
                "closed": True, "fixed_in": ["1.1.0"]}
        return {**base, **over}

    def states(advisories, version):
        data = json.loads(json.dumps(UPGRADE))
        data["packages"]["pypi:libx"]["advisories"] = advisories
        deps = [{"ecosystem": "pypi", "name": "libx", "version": version, "exact": True}]
        row = node(f"m.buildReport({json.dumps(data)}, {json.dumps(deps)})")[0]
        return {a["id"]: a["state"] for a in row["advisories"]}, row
    assert states([adv(undetermined_versions=["1.0.0"])], "1.0.0")[0] == {"GHSA-Z": "unknown"}
    assert states([adv()], "1.0.0")[0] == {}                                          # known and evaluated: not affected
    assert states([adv(closed=False, fixed_in=["0.6.0"])], "1.0.5")[0] == {"GHSA-Z": "unknown"}   # open-ended: cannot call it safe
    assert states([adv(fixed_in=["0.6.0"])], "1.0.5")[0] == {}                        # closed and fixed before the pin
    _, row = states([adv()], "1.0.0")
    assert not any("older advisories" in n for n in row["notes"])
    data = json.loads(json.dumps(UPGRADE))
    data["packages"]["pypi:libx"]["advisories_omitted"] = 7
    deps = [{"ecosystem": "pypi", "name": "libx", "version": "1.0.0", "exact": True}]
    assert any("7 older advisories" in n for n in node(f"m.buildReport({json.dumps(data)}, {json.dumps(deps)})")[0]["notes"])


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_version_comparison_handles_epochs_implicit_post_local_versions_and_the_v_prefix():
    cases = {"pypi": ["1!0.5", "2.0", "1!1.0", "0!3.0", "1.0-1", "1.0-2", "1.0.post1", "1.0a1", "1.0+local", "1.0+local.2", "1.0+abc",
                      "1.0+1", "1.0", "v1.0", "1.0.0+ubuntu.1", "1.0.post1+x", "2!0.1", "1.0rc1+local"],
             "npm": ["v1.0.0", "1.0.0", "v1.0.1", "1.0.1", "v2.0.0-rc.1", "2.0.0-rc.1"]}
    for eco, versions in cases.items():
        pairs = [(a, b) for a in versions for b in versions]
        js = node(f"{json.dumps(pairs)}.map(p => m.cmp({json.dumps(eco)}, p[0], p[1]))")
        for (a, b), got in zip(pairs, js):
            ka, kb = sort_key(eco, a), sort_key(eco, b)
            assert ka is not None and kb is not None, (eco, a, b)
            assert got == (ka > kb) - (ka < kb), (eco, a, b, got)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_exactness_and_specifier_lists_are_read_as_written():
    pkg = json.dumps({"dependencies": {"a": "=0.27.0", "b": "v1.2.3", "c": "^2.0.0", "d": "1.0.0 - 2.0.0"}})
    out = node(f"m.parseDeps({json.dumps(pkg)})")
    assert {d["name"]: d["exact"] for d in out["deps"]} == {"a": True, "b": True, "c": False} and out["skipped"] == 1
    text = "pkg!=0.4.0,>=0.4.2\nwild==0.34.*\ncap<2,!=1.5\nZ-Thing.Name==1\nA__B==2\npinned===3.1 ; python_version>'3'\ncompat~=1.4.2,<2\n"
    got = {d["name"]: [d["version"], d["exact"]] for d in node(f"m.parseDeps({json.dumps(text)})")["deps"]}
    assert got == {"pkg": ["0.4.2", False], "wild": ["0.34", False], "z-thing-name": ["1", True], "a-b": ["2", True],
                   "pinned": ["3.1", True], "compat": ["1.4.2", False]}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_pipfile_lock_and_yarn_lock_are_read_and_pnpm_is_named_as_unsupported():
    pipfile = json.dumps({"_meta": {"hash": {}}, "default": {"Requests": {"version": "==2.31.0"}, "vcs": {"git": "https://x"}},
                          "develop": {"pytest": {"version": "==8.0.0"}}})
    yarn = ('# yarn lockfile v1\n\n\n"@scope/x@^1.0.0", "@scope/x@^1.2.0":\n  version "1.2.3"\n  resolved "https://r/x.tgz"\n\n'
            'left-pad@^1.3.0:\n  version "1.3.0"\n')
    pnpm = "lockfileVersion: '6.0'\n\npackages:\n  /left-pad@1.3.0:\n    resolution: {integrity: sha512-x}\n"
    a, b, c = node(f"[m.parseDeps({json.dumps(pipfile)}), m.parseDeps({json.dumps(yarn)}), m.parseDeps({json.dumps(pnpm)})]")
    assert {d["name"]: d["version"] for d in a["deps"]} == {"requests": "2.31.0", "pytest": "8.0.0"} and a["deps"][0]["ecosystem"] == "pypi" and a["skipped"] == 1
    assert {d["name"]: d["version"] for d in b["deps"]} == {"@scope/x": "1.2.3", "left-pad": "1.3.0"} and all(d["ecosystem"] == "npm" and d["exact"] for d in b["deps"])
    assert c["deps"] == [] and c["unsupported"] == "pnpm-lock.yaml"


def test_the_page_says_which_formats_it_reads():
    html = client(settings_for_page()).get("/upgrade").text
    assert "Pipfile.lock" in html and "yarn.lock" in html and "pnpm-lock.yaml" in html


def settings_for_page():
    import tempfile
    from tests.webutil import make_settings
    return make_settings(Path(tempfile.mkdtemp()))


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_pin_between_two_snapshots_says_the_report_may_include_older_changes():
    def notes(version):
        deps = [{"ecosystem": "pypi", "name": "libx", "version": version, "exact": True}]
        return node(f"m.buildReport({json.dumps(UPGRADE)}, {json.dumps(deps)})")[0]["notes"]
    assert any("between two versions" in n for n in notes("1.0.5"))
    assert not any("between two versions" in n for n in notes("1.1.0")) and not any("between two versions" in n for n in notes("0.4.0"))
