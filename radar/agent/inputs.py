# radar/agent/inputs.py
"""What the agent reads: a public GitHub repo (code plus dependency files) or one pasted dependency file."""
import posixpath
import re

from core.github import GitHubError, GitHubRateLimited, RepoNotFound
from radar.agent.deps import PARSERS

_URL = re.compile(r"^(?i:(?:https?://)?(?:www\.)?github\.com)/([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100}?)(?:\.git)?(?:[/?#].*)?\Z")
CODE_EXT = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
SKIP_DIRS = {".github", "node_modules", "dist", "build", "out", "vendor", "venv", ".venv", "site-packages", "__pycache__", "tests",
             "test", "__tests__", "coverage", ".next", ".git"}
MAX_PASTE = 600_000


class InputError(ValueError):
    """A visitor-facing problem with the input (shown as-is)."""


def parse_repo_url(url: str) -> str:
    url = (url or "").strip(" \t")
    m = _URL.match(url)
    if not m or ".." in url or m.group(2) in (".", ""):
        raise InputError("Enter a public GitHub repository URL, like https://github.com/owner/repo.")
    return f"{m.group(1)}/{m.group(2)}"


def wanted(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return False
    base = parts[-1]
    if base in PARSERS or re.fullmatch(r"requirements[\w.-]*\.txt", base):
        return True
    return base.endswith(CODE_EXT) and not base.endswith((".min.js", ".bundle.js", ".d.ts", ".d.mts", ".d.cts")) and not base.startswith("test_")


def from_repo(github, url: str) -> dict:
    repo = parse_repo_url(url)
    try:
        snap = github.snapshot(repo, wanted=wanted)
    except RepoNotFound:
        raise InputError("Repository not found or private. Only public GitHub repositories can be read.") from None
    except GitHubRateLimited:
        raise InputError("GitHub rate limit reached. Try again in a few minutes.") from None
    except GitHubError:
        raise InputError("Could not reach GitHub. Try again shortly.") from None
    if not snap["files"]:
        raise InputError("This repository is too large to read." if snap["skipped"] or snap["truncated"]
                         else "No code or dependency files found in this repository.")
    return {"mode": "repo", "repo": snap["repo"], "sha": snap["sha"], "files": snap["files"], "skipped": snap["skipped"],
            "truncated": snap["truncated"]}


def from_paste(filename: str, text: str) -> dict:
    base = posixpath.basename((filename or "").replace("\\", "/"))
    if base not in PARSERS and not re.fullmatch(r"requirements[\w.-]*\.txt", base):
        raise InputError("Paste a requirements.txt, pyproject.toml, package.json or lock file.")
    if len(text.encode("utf-8")) > MAX_PASTE:
        raise InputError("That file is too large; paste up to 600 KB.")
    return {"mode": "paste", "repo": None, "sha": None, "files": {base: text}, "skipped": [], "truncated": False}
