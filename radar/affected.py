"""OSV 'affected' semantics (introduced / fixed / last_affected / explicit versions) for the versions Radar knows.
Deterministic and honest: data Radar cannot evaluate is 'undetermined', never 'not affected'."""
import json
import re

from radar.versions import same, sort_key

RANGE_TYPES = ("SEMVER", "ECOSYSTEM")
VERSIONS_CAP = 50        # radar/advisories.py keeps at most this many listed versions per affected entry
MAX_FIXED = 5
_RANK = {"introduced": 0, "fixed": 1, "last_affected": 2}


def _norm_name(name) -> str:
    return re.sub(r"[-_.]+", "-", str(name).lower())


def for_package(affected, ecosystem: str, name: str) -> list[dict]:
    """OSV lists every package an advisory touches. Keep the entries for this one; rows stored without package info stay."""
    if not isinstance(affected, list):
        return []
    want, out = _norm_name(name), []
    for item in affected:
        if not isinstance(item, dict):
            continue
        pkg = item.get("package")
        if isinstance(pkg, dict) and pkg.get("name") is not None:
            if _norm_name(pkg["name"]) != want or (pkg.get("ecosystem") and str(pkg["ecosystem"]).lower() != ecosystem.lower()):
                continue
        out.append(item)
    return out


def _events(ecosystem: str, events):
    """(sorted [(kind, value, key)], ok): OSV events must be ordered by version before they are read."""
    if not isinstance(events, list):
        return [], False
    parsed, ok = [], True
    for event in events:
        if not isinstance(event, dict):
            ok = False
            continue
        for kind in _RANK:
            if kind in event:
                value = event[kind]
                if kind == "introduced" and value == "0":
                    parsed.append((kind, value, (0, 0)))
                    continue
                key = sort_key(ecosystem, str(value).lstrip("vV"))
                if key is None:
                    ok = False
                else:
                    parsed.append((kind, value, (1, key)))
    parsed.sort(key=lambda e: (e[2], _RANK[e[0]]))
    return parsed, ok


def _in_range(ecosystem: str, version, events) -> tuple[bool, bool]:
    parsed, ok = _events(ecosystem, events)
    affected = False
    for kind, value, key in parsed:
        bound = key[1] if key[0] else None
        if kind == "introduced":
            affected = affected or bound is None or version >= bound
        elif kind == "fixed" and version >= bound:
            affected = False
        elif kind == "last_affected" and version > bound:
            affected = False
    return affected, ok


def evaluate(ecosystem: str, version: str, affected) -> str:
    """'affected', 'not affected' or 'undetermined' (nothing usable, a GIT range, an unparseable bound, a cut-off list)."""
    key = sort_key(ecosystem, version.lstrip("vV")) if isinstance(version, str) else None
    if key is None or not isinstance(affected, list):
        return "undetermined"
    usable = unsure = False
    for item in affected:
        if not isinstance(item, dict):
            continue
        listed = item.get("versions")
        if isinstance(listed, list) and listed:
            usable = True
            if any(isinstance(v, str) and same(v, version) for v in listed):
                return "affected"
            unsure = unsure or len(listed) >= VERSIONS_CAP
        ranges = item.get("ranges") if isinstance(item.get("ranges"), list) else []
        for r in ranges:
            if not isinstance(r, dict) or r.get("type") not in RANGE_TYPES:
                unsure = True
                continue
            hit, ok = _in_range(ecosystem, key, r.get("events"))
            usable = True
            if hit:
                return "affected"
            unsure = unsure or not ok
    return "undetermined" if unsure or not usable else "not affected"


def is_affected(ecosystem: str, version: str, affected) -> bool:
    return evaluate(ecosystem, version, affected) == "affected"


def closed(affected, ecosystem: str = "pypi") -> bool:
    """True only when every range is a plain introduced/fixed sequence ending in a fix (and the fixes fit what is published):
    then a version at or above every fix is safe even if Radar has no snapshot of it."""
    items = [i for i in affected if isinstance(i, dict)] if isinstance(affected, list) else []
    if not items:
        return False
    fixes = 0
    for item in items:
        if item.get("versions"):
            return False
        ranges = item.get("ranges") if isinstance(item.get("ranges"), list) else []
        if not ranges:
            return False
        for r in ranges:
            if not isinstance(r, dict) or r.get("type") not in RANGE_TYPES:
                return False
            parsed, ok = _events(ecosystem, r.get("events"))
            kinds = [k for k, _, _ in parsed]
            if not ok or not kinds or any(k == "last_affected" for k in kinds) or len(kinds) % 2 or \
                    kinds != ["introduced", "fixed"] * (len(kinds) // 2):
                return False
            fixes += len(kinds) // 2
    return fixes <= MAX_FIXED


def fixed_versions(affected, ecosystem: str = "pypi") -> list[str]:
    found: list[str] = []
    for item in affected if isinstance(affected, list) else []:
        for r in (item.get("ranges") or []) if isinstance(item, dict) and isinstance(item.get("ranges"), list) else []:
            for event in (r.get("events") or []) if isinstance(r, dict) and isinstance(r.get("events"), list) else []:
                if isinstance(event, dict) and isinstance(event.get("fixed"), str) and event["fixed"] not in found:
                    found.append(event["fixed"])
    return sorted(found, key=lambda v: (sort_key(ecosystem, v.lstrip("vV")) is None, sort_key(ecosystem, v.lstrip("vV")) or 0, v))[:MAX_FIXED]


def _aliases(row) -> list[str]:
    raw = row["aliases"]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = []
    return [a for a in raw if isinstance(a, str)] if isinstance(raw, list) else []


def alias_groups(rows) -> list[list]:
    """Group records of one vulnerability (GHSA, PYSEC, CVE ... linked by ids and aliases), in order of first appearance."""
    groups: list[tuple[set, list]] = []
    for row in rows:
        names = {row["id"], *_aliases(row)}
        hits = [g for g in groups if g[0] & names]
        if not hits:
            groups.append((names, [row]))
            continue
        first = hits[0]
        first[0].update(names)
        first[1].append(row)
        for other in hits[1:]:
            first[0].update(other[0])
            first[1].extend(other[1])
            groups.remove(other)
    return [g[1] for g in groups]
