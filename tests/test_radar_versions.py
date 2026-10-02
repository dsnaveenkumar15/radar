import pytest

from radar.versions import extract_version, is_stable, same, sort_key, sorted_stable


@pytest.mark.parametrize("tag, expected", [
    ("v0.2.40", "0.2.40"), ("1.5.0", "1.5.0"), ("python-v0.5.1", "0.5.1"), ("langgraph==0.3.0rc1", "0.3.0rc1"),
    ("release 2.0", "2.0"), ("sdk-v1.2.3-beta.2", "1.2.3-beta.2"), ("nightly", None), ("", None),
])
def test_extract_version(tag, expected):
    assert extract_version(tag) == expected


def test_pypi_ordering_and_stability():
    assert sorted_stable("pypi", ["1.10.0", "1.2.0", "1.2.0rc1", "2.0.0.dev1", "junk", "1.2.0"]) == ["1.2.0", "1.10.0"]
    assert is_stable("pypi", "1.0.0") and not is_stable("pypi", "1.0.0b1") and not is_stable("pypi", "junk")


def test_npm_ordering_and_stability():
    versions = ["1.10.0", "1.2.0", "1.2.0-rc.1", "1.2.0-beta.10", "1.2.0-beta.2", "bad", "1.2.0+build5"]
    assert sorted_stable("npm", versions) == ["1.2.0", "1.2.0+build5", "1.10.0"]   # build metadata does not order
    keys = [sort_key("npm", v) for v in ["1.2.0-beta.2", "1.2.0-beta.10", "1.2.0-rc.1", "1.2.0"]]
    assert keys == sorted(keys)               # numeric identifiers compare as numbers; release sorts after prerelease
    assert not is_stable("npm", "1.2.0-rc.1") and sort_key("npm", "bad") is None


def test_same_ignores_a_leading_v():
    assert same("v1.2.3", "1.2.3") and not same("1.2.3", "1.2.4")


def test_split_tag_returns_the_label_before_the_version():
    from radar.versions import split_tag
    assert split_tag("langchain-openai==1.6.7") == ("langchain-openai==", "1.6.7")
    assert split_tag("v0.2.40") == ("v", "0.2.40") and split_tag("1.0") == ("", "1.0") and split_tag("nightly") is None


def test_npm_versions_accept_the_v_prefix_that_release_tags_carry():
    from radar.versions import sort_key
    assert sort_key("npm", "v1.2.3") == sort_key("npm", "1.2.3") and sort_key("npm", "v1.2.3") is not None
    assert sort_key("npm", "vx") is None and sort_key("npm", "v1.2") is None
