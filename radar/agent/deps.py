# radar/agent/deps.py
"""Dependency files -> package and version in use. Exact pins only count as a version; ranges are reported as ranges."""
import json
import posixpath
import re
import tomllib

from radar.agent.types import Dependency
from radar.versions import sort_key

_REQ = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")
_EXACT_PY = re.compile(r"^==\s*([A-Za-z0-9.+!_-]+)\s*$")
_EXACT_NPM = re.compile(r"^=?v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)$")
_YARN_HEAD = re.compile(r'^"?(@?[^@"\s]+)@[^:]*"?:\s*$')
_YARN_VERSION = re.compile(r'^\s+version\s+"([^"]+)"')


def normalize_pypi(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _py(spec: str, source: str) -> Dependency | None:
    spec = spec.split(";", 1)[0].split("#", 1)[0].strip()
    m = _REQ.match(spec)
    if not m or spec.startswith(("-", "git+", "http")):
        return None
    exact = _EXACT_PY.match(m.group(3).strip())
    return Dependency("pypi", normalize_pypi(m.group(1)), exact.group(1) if exact else None, spec, source)


def _requirements(text, source):
    return [d for d in (_py(line, source) for line in text.splitlines()) if d]


def _pyproject(text, source):
    data = tomllib.loads(text)
    out = [d for d in (_py(s, source) for s in (data.get("project") or {}).get("dependencies") or []) if d]
    poetry = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
    for name, spec in poetry.items():
        if name.lower() == "python":
            continue
        raw = spec if isinstance(spec, str) else (spec.get("version", "") if isinstance(spec, dict) else "")
        exact = re.fullmatch(r"=?=?\s*(\d[\w.+!-]*)", raw.strip())
        out.append(Dependency("pypi", normalize_pypi(name), exact.group(1) if exact else None, f"{name} {raw}", source))
    return out


def _package_json(text, source):
    data = json.loads(text)
    out = []
    for section in ("dependencies", "devDependencies"):
        for name, spec in (data.get(section) or {}).items():
            exact = _EXACT_NPM.match(str(spec).strip())
            out.append(Dependency("npm", name, exact.group(1) if exact else None, f"{name}@{spec}", source))
    return out


def _package_lock(text, source):
    data = json.loads(text)
    out = []
    for path, meta in (data.get("packages") or {}).items():
        if path.count("node_modules/") != 1 or not isinstance(meta, dict) or not meta.get("version"):
            continue                                    # top-level installs only: nested copies are not what you import
        name = path.split("node_modules/", 1)[1]
        out.append(Dependency("npm", name, meta["version"], f"{name}@{meta['version']}", source))
    return out


def _toml_lock(text, source):
    return [Dependency("pypi", normalize_pypi(p["name"]), p["version"], f"{p['name']}=={p['version']}", source)
            for p in tomllib.loads(text).get("package") or [] if p.get("name") and p.get("version")]


def _yarn_lock(text, source):
    out, current = [], None
    for line in text.splitlines():
        head = _YARN_HEAD.match(line)
        if head:
            current = head.group(1)
            continue
        version = _YARN_VERSION.match(line)
        if version and current:
            out.append(Dependency("npm", current, version.group(1), f"{current}@{version.group(1)}", source))
            current = None
    return out


PARSERS = {"requirements.txt": _requirements, "pyproject.toml": _pyproject, "package.json": _package_json,
           "package-lock.json": _package_lock, "poetry.lock": _toml_lock, "uv.lock": _toml_lock, "yarn.lock": _yarn_lock}


def parse_file(path: str, text: str) -> list[Dependency]:
    base = posixpath.basename(path.replace("\\", "/"))
    parser = PARSERS.get(base) or (_requirements if re.fullmatch(r"requirements[\w.-]*\.txt", base) else None)
    if parser is None:
        return []
    try:
        return parser(text, path)
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, tomllib.TOMLDecodeError):
        return []


def merge(deps: list[Dependency]) -> tuple[list[Dependency], list[str]]:
    """One entry per package: an exact version beats a range; conflicting exact pins resolve to the oldest."""
    grouped: dict[tuple, list[Dependency]] = {}
    for d in deps:
        grouped.setdefault((d.ecosystem, d.name), []).append(d)
    merged, notes = [], []
    for (ecosystem, name), group in sorted(grouped.items()):
        exact = [d for d in group if d.version]
        if not exact:
            merged.append(group[0])
            continue
        versions = sorted({d.version for d in exact}, key=lambda v: (sort_key(ecosystem, v) is None, sort_key(ecosystem, v) or 0, v))
        if len(versions) > 1:
            notes.append(f"{name} is pinned at {', '.join(versions)} in different files; analysing the oldest, {versions[0]}.")
        merged.append(next(d for d in exact if d.version == versions[0]))
    return merged, notes
