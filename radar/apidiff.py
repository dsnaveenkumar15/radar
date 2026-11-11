"""Deterministic API diff between two snapshots. Code decides what is breaking; the LLM only explains."""
from difflib import SequenceMatcher

MAX_LIST = 100
MAX_ADDED = 200
RENAME_RATIO = 0.6
CONTAINERS = ("class", "interface", "enum", "namespace")
_POSITIONAL = ("pos_only", "pos_or_kw")

# Modules a package split into a sister package that it requires, so the old import path keeps working. Verified by hand:
# langgraph>=0.3 requires langgraph-prebuilt, which ships langgraph/prebuilt/.
# ponytail: hand-kept list, one line per known split; snapshot the sister package too if splits become common.
MOVED = {"pypi:langgraph": {"langgraph.prebuilt": "langgraph-prebuilt"}}


def _parent_leaf(name: str) -> tuple[str, str]:
    """("pkg.m", "f") for "pkg.m.f"; ("./client#Client", "connect") for "./client#Client.connect"."""
    scope, hash_, local = name.rpartition("#")
    parent, _, leaf = local.rpartition(".")
    return (scope + hash_ + parent if parent else scope + hash_), leaf


def _positional(params):
    return [p for p in params if p["kind"] in _POSITIONAL]


def _has(params, kind) -> bool:
    return any(p["kind"] == kind for p in params)


def param_changes(old: list[dict], new: list[dict]) -> list[str]:
    changes = []
    old_pos, new_pos = _positional(old), _positional(new)
    old_kw = {p["name"]: p for p in old if p["kind"] == "kw_only"}
    new_kw = {p["name"]: p for p in new if p["kind"] == "kw_only"}
    by_name = {p["name"]: p for p in new if p["kind"] in ("pos_or_kw", "kw_only")}
    accepts_all = _has(new, "var_pos") and _has(new, "var_kw")
    for i, p in enumerate(old_pos):
        if i < len(new_pos):
            q = new_pos[i]
            if p["kind"] == "pos_or_kw" and q["kind"] == "pos_or_kw" and p["name"] != q["name"]:
                changes.append(f"parameter '{p['name']}' (position {i + 1}) is now '{q['name']}'")
            elif p["kind"] == "pos_or_kw" and q["kind"] == "pos_only":
                changes.append(f"parameter '{p['name']}' is now positional-only")
            elif p["default"] and not q["default"]:
                changes.append(f"parameter '{p['name']}' is now required")
        elif p["kind"] == "pos_or_kw" and p["name"] in new_kw:
            changes.append(f"parameter '{p['name']}' is now keyword-only")
        elif not accepts_all:
            changes.append(f"parameter '{p['name']}' was removed")
    for q in new_pos[len(old_pos):]:
        if not q["default"]:
            changes.append(f"new required parameter '{q['name']}'")
    for name, p in old_kw.items():
        q = by_name.get(name)
        if q is None:
            if not _has(new, "var_kw"):
                changes.append(f"keyword parameter '{name}' was removed")
        elif p["default"] and not q["default"]:
            changes.append(f"keyword parameter '{name}' is now required")
    for name, q in new_kw.items():
        if name not in old_kw and not q["default"] and not any(p["name"] == name for p in old_pos):
            changes.append(f"new required keyword parameter '{name}'")
    if _has(old, "var_pos") and not _has(new, "var_pos"):
        changes.append("*args is no longer accepted")
    if _has(old, "var_kw") and not _has(new, "var_kw"):
        changes.append("**kwargs is no longer accepted")
    return changes


def _renames(removed: list[str], added: list[str], old: dict, new: dict) -> dict[str, str]:
    pairs, used = {}, set()
    for name in removed:
        parent, leaf = _parent_leaf(name)
        best, best_ratio = None, RENAME_RATIO
        for cand in added:
            if cand in used or _parent_leaf(cand)[0] != parent or new[cand]["kind"] != old[name]["kind"]:
                continue
            if old[name].get("params") != new[cand].get("params"):
                continue
            ratio = SequenceMatcher(None, leaf, _parent_leaf(cand)[1]).ratio()
            if ratio > best_ratio:
                best, best_ratio = cand, ratio
        if best:
            pairs[name] = best
            used.add(best)
    return pairs


def _moved_to(package: str, name: str) -> str | None:
    for prefix, sister in MOVED.get(package, {}).items():
        if name == prefix or name.startswith(prefix + "."):
            return sister
    return None


def diff_snapshots(old: dict, new: dict, limit: int | None = MAX_LIST, added_limit: int | None = MAX_ADDED,
                   package: str = "") -> dict:
    a, b = old["symbols"], new["symbols"]
    removed, added = sorted(set(a) - set(b)), sorted(set(b) - set(a))
    renames = _renames(removed, added, a, b)
    containers = {n for n in removed if a[n]["kind"] in CONTAINERS}
    changes = []
    for name in removed:
        if _parent_leaf(name)[0] in containers:
            continue
        sister = _moved_to(package, name)
        if sister:
            changes.append({"symbol": name, "change": "moved", "severity": "notable",
                            "detail": f"moved to the {sister} package, which {package.partition(':')[2]} installs; the import still works"})
            continue
        to = renames.get(name)
        # A member that disappears from a class that still has base classes may simply be inherited now.
        inherited = a[name]["kind"] in ("method", "property") and bool(b.get(_parent_leaf(name)[0], {}).get("bases"))
        if inherited and not to:
            changes.append({"symbol": name, "change": "removed_maybe_inherited", "severity": "notable",
                            "detail": "no longer defined on the class (it may still be inherited)"})
            continue
        changes.append({"symbol": name, "change": "removed", "severity": "breaking",
                        "detail": f"likely renamed to {to}" if to else "removed from the public API",
                        **({"to": to} if to else {})})
    for name in sorted(set(a) & set(b)):
        x, y = a[name], b[name]
        if x["kind"] != y["kind"]:
            changes.append({"symbol": name, "change": "kind_changed", "severity": "breaking",
                            "detail": f"{x['kind']} became {y['kind']}"})
            continue
        if "params" in x and "params" in y:
            # Pydantic models are usually returned, not built, by callers: a changed field list is notable, not breaking.
            severity = "notable" if x.get("model") or y.get("model") else "breaking"
            changes += [{"symbol": name, "change": "signature_changed", "severity": severity, "detail": d}
                        for d in param_changes(x["params"], y["params"])]
        if x.get("optional") is True and y.get("optional") is False:
            changes.append({"symbol": name, "change": "became_required", "severity": "notable",
                            "detail": "optional member is now required"})
        if not x.get("deprecated") and y.get("deprecated"):
            changes.append({"symbol": name, "change": "deprecated", "severity": "notable", "detail": "now deprecated"})
    for name in added:
        parent = _parent_leaf(name)[0]
        if b[name]["kind"] == "property" and b[name].get("optional") is False and b.get(parent, {}).get("kind") == "interface" \
                and parent in a:
            changes.append({"symbol": name, "change": "new_required_member", "severity": "notable",
                            "detail": "new required member on an existing interface"})
    breaking = [c for c in changes if c["severity"] == "breaking"]
    deprecations = [c for c in changes if c["change"] == "deprecated"]
    other = [c for c in changes if c["severity"] != "breaking" and c["change"] != "deprecated"]
    added_names = [n for n in added if n not in set(renames.values())]
    return {"breaking": breaking[:limit], "deprecations": deprecations[:limit], "other": other[:limit],
            "added": added_names[:added_limit],
            "counts": {"breaking": len(breaking), "deprecations": len(deprecations), "added": len(added_names),
                       "removed": len(removed)},
            "truncated": (limit is not None and (len(breaking) > limit or len(deprecations) > limit or len(other) > limit))
                         or (added_limit is not None and len(added_names) > added_limit)}


def breaking_symbols(diff: dict) -> list[str]:
    return list(dict.fromkeys(c["symbol"] for c in diff["breaking"]))
