"""Deterministic version handling. Versions in published text come from here, never from the LLM."""
import re

from packaging.version import InvalidVersion, Version

_TOKEN = re.compile(r"\d+\.\d+(?:\.\d+)*(?:(?:[-.]?(?:a|b|rc|alpha|beta|dev|post)[.-]?\d+)+)?")
_NPM = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


def extract_version(tag: str) -> str | None:
    match = _TOKEN.search(tag or "")
    return match.group(0) if match else None


def find_versions(text: str) -> list[str]:
    return [m.group(0) for m in _TOKEN.finditer(text or "")]


def split_tag(tag: str) -> tuple[str, str] | None:
    """(label before the version, version) of a release tag, e.g. ("langchain-openai==", "1.6.7")."""
    match = _TOKEN.search(tag or "")
    return (tag[:match.start()], match.group(0)) if match else None


def _npm_key(version: str):
    match = _NPM.match(version[1:] if version[:1] in "vV" and version[1:2].isdigit() else version)
    if not match:
        return None
    major, minor, patch, pre = match.groups()
    ids = () if pre is None else tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
    return (int(major), int(minor), int(patch), pre is None, ids)


def sort_key(ecosystem: str, version: str):
    if ecosystem == "npm":
        return _npm_key(version)
    try:
        return Version(version)
    except InvalidVersion:
        return None


def is_stable(ecosystem: str, version: str) -> bool:
    key = sort_key(ecosystem, version)
    if key is None:
        return False
    if ecosystem == "npm":
        return key[3]
    return not key.is_prerelease and not key.is_devrelease


def sorted_stable(ecosystem: str, versions: list[str]) -> list[str]:
    keyed = {}
    for v in versions:
        if v not in keyed and is_stable(ecosystem, v):
            keyed[v] = sort_key(ecosystem, v)
    return sorted(keyed, key=lambda v: (keyed[v], v))


def same(a: str, b: str) -> bool:
    return a.lstrip("vV") == b.lstrip("vV")
