import pytest

from radar.affected import fixed_versions, is_affected


def rng(*events, kind="ECOSYSTEM"):
    return {"ranges": [{"type": kind, "events": list(events)}], "versions": []}


@pytest.mark.parametrize("version, expected", [("1.0.0", False), ("1.2.0", True), ("1.2.9", True), ("1.3.0", False), ("2.0.0", False)])
def test_introduced_and_fixed_bound_the_affected_versions(version, expected):
    assert is_affected("pypi", version, [rng({"introduced": "1.2.0"}, {"fixed": "1.3.0"})]) is expected


def test_introduced_zero_and_open_ended_ranges():
    assert is_affected("pypi", "0.0.1", [rng({"introduced": "0"}, {"fixed": "1.0"})]) is True
    assert is_affected("pypi", "9.9", [rng({"introduced": "2.0"})]) is True
    assert is_affected("pypi", "1.9", [rng({"introduced": "2.0"})]) is False


def test_last_affected_is_inclusive_and_several_ranges_are_or_ed():
    a = [{"ranges": [{"type": "SEMVER", "events": [{"introduced": "1.0.0"}, {"last_affected": "1.4.0"},
                                                    {"introduced": "3.0.0"}, {"fixed": "3.1.0"}]}]}]
    assert [is_affected("npm", v, a) for v in ("1.4.0", "1.4.1", "3.0.5", "3.1.0")] == [True, False, True, False]


def test_explicit_version_lists_match_ignoring_a_leading_v():
    assert is_affected("npm", "v1.2.3", [{"versions": ["1.2.3"], "ranges": []}]) is True
    assert is_affected("npm", "1.2.4", [{"versions": ["1.2.3"], "ranges": []}]) is False


@pytest.mark.parametrize("affected", [None, [], "junk", [None], [{"ranges": "x"}], [{"ranges": [None, {"type": "GIT", "events": [{"introduced": "0"}]}]}],
                                      [{"ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "banana"}, {"fixed": "also-bad"}]}]}],
                                      [{"ranges": [{"type": "ECOSYSTEM", "events": "nope"}]}]])
def test_malformed_or_git_data_is_never_affected_and_never_raises(affected):
    assert is_affected("pypi", "1.0.0", affected) is False


def test_an_unparseable_version_is_not_affected():
    assert is_affected("pypi", "not-a-version", [rng({"introduced": "0"})]) is False


def test_fixed_versions_lists_the_distinct_fixed_events():
    a = [rng({"introduced": "1.0"}, {"fixed": "1.2"}), rng({"introduced": "2.0"}, {"fixed": "2.1"}, {"fixed": "1.2"})]
    assert fixed_versions(a) == ["1.2", "2.1"] and fixed_versions(None) == []


def test_events_are_sorted_before_they_are_evaluated():
    from radar.affected import is_affected
    a = [rng({"introduced": "0"}, {"introduced": "2.0"}, {"fixed": "1.5"}, {"fixed": "2.5"})]
    assert [is_affected("pypi", v, a) for v in ("1.2", "1.6", "2.2", "2.6")] == [True, False, True, False]


def test_data_radar_cannot_evaluate_is_undetermined_never_safe():
    from radar.affected import evaluate
    git = [{"ranges": [{"type": "GIT", "events": [{"introduced": "0"}]}], "versions": []}]
    assert evaluate("pypi", "1.0", git) == "undetermined"
    assert evaluate("pypi", "1.0", [rng({"introduced": "banana"}, {"fixed": "2"})]) == "undetermined"
    assert evaluate("pypi", "3.0", [{"versions": [f"0.{i}" for i in range(50)], "ranges": []}]) == "undetermined"
    for junk in (None, [], "x", [None]):
        assert evaluate("pypi", "1.0", junk) == "undetermined"
    assert evaluate("pypi", "3.0", [rng({"introduced": "0"}, {"fixed": "1"})]) == "not affected"
    assert evaluate("pypi", "0.5", [rng({"introduced": "0"}, {"fixed": "1"})]) == "affected"


def test_closed_advisories_are_the_fully_bounded_ones():
    from radar.affected import closed
    assert closed([rng({"introduced": "1"}, {"fixed": "2"})]) is True
    assert closed([rng({"introduced": "1"})]) is False
    assert closed([rng({"introduced": "1"}, {"fixed": "2"}, {"introduced": "3"})]) is False
    assert closed([rng({"introduced": "1"}, {"last_affected": "2"})]) is False
    assert closed([{"versions": ["1.0"], "ranges": []}]) is False
    assert closed(None) is False
    assert closed([rng({"introduced": f"{i}.0"}, {"fixed": f"{i}.5"}) for i in range(1, 7)]) is False


def test_only_entries_for_the_package_count_and_old_rows_without_package_info_are_kept():
    from radar.affected import for_package
    other = {"package": {"name": "numexpr", "ecosystem": "PyPI"}, "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}]}]}
    mine = {"package": {"name": "Lang_Chain", "ecosystem": "PyPI"}, "ranges": []}
    assert for_package([other, mine, "junk"], "pypi", "lang-chain") == [mine]
    assert for_package([{"ranges": []}], "pypi", "x") == [{"ranges": []}]
    assert for_package(None, "pypi", "x") == []


def test_alias_groups_merge_the_same_vulnerability_published_under_several_ids():
    from radar.affected import alias_groups
    rows = [{"id": "GHSA-1", "aliases": '["CVE-1"]'}, {"id": "PYSEC-1", "aliases": '["CVE-1", "GHSA-1"]'},
            {"id": "GHSA-2", "aliases": "[]"}, {"id": "GHSA-3", "aliases": "not json"}]
    assert [[r["id"] for r in g] for g in alias_groups(rows)] == [["GHSA-1", "PYSEC-1"], ["GHSA-2"], ["GHSA-3"]]
