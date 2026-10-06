import pytest

from core.github import GitHubError, GitHubRateLimited, RepoNotFound
from radar.agent.inputs import InputError, from_paste, from_repo, parse_repo_url, wanted


@pytest.mark.parametrize("url", ["https://github.com/o/r", "https://github.com/o/r/", "https://github.com/o/r.git",
                                 "https://github.com/o/r/tree/main/x", "http://github.com/o/r", "github.com/o/r"])
def test_repo_url_variants(url):
    assert parse_repo_url(url) == "o/r"


@pytest.mark.parametrize("url", ["https://gitlab.com/o/r", "https://github.com/o", "file:///etc/passwd", "", "o/r/../x",
                                 "https://github.com/o/r/tree/main/../x", "https://github.com/o_x/r", "https://github.com/o/r s",
                                 "https://github.com/o/r%2e"])
def test_bad_urls_are_refused(url):
    with pytest.raises(InputError):
        parse_repo_url(url)


def test_wanted_keeps_code_and_manifests_and_skips_noise():
    assert wanted("requirements.txt") and wanted("api/pyproject.toml") and wanted("src/app.py") and wanted("web/a.tsx")
    for path in ("node_modules/x/index.js", "dist/app.js", "tests/test_a.py", "vendor/a.py", "a.min.js", "README.md",
                 "types/api.d.ts", "x.d.mts", ".github/scripts/a.js", "pkg/.github/b.py"):
        assert not wanted(path), path


def test_from_repo_uses_the_github_snapshot():
    class G:
        def snapshot(self, repo, sha=None, wanted=None):
            assert repo == "o/r" and wanted("app.py") and not wanted("README.md")
            return {"repo": repo, "sha": "a" * 40, "files": {"app.py": "x"}, "skipped": [], "truncated": False, "meta": {}}
    got = from_repo(G(), "https://github.com/o/r")
    assert got == {"mode": "repo", "repo": "o/r", "sha": "a" * 40, "files": {"app.py": "x"}, "skipped": [], "truncated": False}


@pytest.mark.parametrize("exc, text", [(RepoNotFound("u"), "not found or private"),
                                       (GitHubRateLimited("limit", 0.0), "rate limit"),
                                       (GitHubError("boom"), "reach GitHub")])
def test_from_repo_turns_github_errors_into_friendly_input_errors(exc, text):
    class G:
        def snapshot(self, repo, sha=None, wanted=None):
            raise exc
    with pytest.raises(InputError, match=text):
        from_repo(G(), "https://github.com/o/r")


def test_from_repo_refuses_a_repo_with_nothing_readable_or_too_large():
    class G:
        def __init__(self, snap):
            self.snap = snap

        def snapshot(self, repo, sha=None, wanted=None):
            return {"repo": repo, "sha": "a" * 40, "meta": {}, **self.snap}
    with pytest.raises(InputError, match="too large"):
        from_repo(G({"files": {}, "skipped": ["big.py"], "truncated": True}), "github.com/o/r")
    with pytest.raises(InputError, match="No code"):
        from_repo(G({"files": {}, "skipped": [], "truncated": False}), "github.com/o/r")


def test_from_paste_accepts_only_dependency_files_and_size():
    assert from_paste("requirements.txt", "openai==1.0.0")["files"] == {"requirements.txt": "openai==1.0.0"}
    with pytest.raises(InputError):
        from_paste("app.py", "x")
    with pytest.raises(InputError):
        from_paste("requirements.txt", "x" * 600_001)


@pytest.mark.parametrize("url, want", [("HTTPS://GitHub.com/Own/Rep", "Own/Rep"), ("HTTP://WWW.GITHUB.COM/O/R.git", "O/R"),
                                       ("https://github.com/o/r?tab=readme", "o/r"), ("https://github.com/o/r#readme", "o/r"),
                                       ("https://github.com/o/r/tree/main?x=1", "o/r"), ("https://github.com/o/r.git?x=1", "o/r")])
def test_url_case_insensitive_host_and_query_fragment(url, want):
    assert parse_repo_url(url) == want


@pytest.mark.parametrize("url", ["https://user@github.com/o/r", "https://github.com.evil.com/o/r", "https://evil.com/github.com/o/r",
                                 "https://github.com/o/r\n", "https://github.com/o/r\x00", "https://github.com/o/\nr",
                                 "https://github.com/o/.", "https://github.com/o/..", "https://github.com/o/r\nx"])
def test_url_still_refused(url):
    with pytest.raises(InputError):
        parse_repo_url(url)


def test_paste_cap_is_bytes():
    with pytest.raises(InputError):
        from_paste("requirements.txt", "é" * 300_001)
