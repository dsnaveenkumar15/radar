"""Public API surface of an npm package from its .d.ts files. Parsed with tree-sitter; never executed."""
import json
import posixpath
import re

import tree_sitter as ts
import tree_sitter_typescript as tst

MAX_DEPTH = 8
_PARSER: ts.Parser | None = None
_DECLARATIONS = {"function_signature", "function_declaration", "class_declaration", "abstract_class_declaration",
                 "interface_declaration", "type_alias_declaration", "lexical_declaration", "variable_declaration",
                 "enum_declaration", "internal_module", "module"}
_DTS = (".d.ts", ".d.mts", ".d.cts")


def _parser() -> ts.Parser:
    global _PARSER
    if _PARSER is None:
        _PARSER = ts.Parser(ts.Language(tst.language_typescript()))
    return _PARSER


def _text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _deprecated(node, src: bytes) -> bool:
    prev = node.prev_sibling
    return prev is not None and prev.type == "comment" and "@deprecated" in _text(prev, src)


def _name_of(node, src: bytes) -> str:
    named = node.child_by_field_name("name")
    if named is None:
        named = next((c for c in node.children if c.type in ("type_identifier", "identifier", "property_identifier")), None)
    return _text(named, src) if named is not None else ""


def _params(formal, src: bytes) -> list[dict]:
    out = []
    for child in (formal.named_children if formal is not None else []):
        if child.type not in ("required_parameter", "optional_parameter") or not child.named_children:
            continue
        first = child.named_children[0]
        if first.type == "rest_pattern":
            out.append({"name": _text(first, src).lstrip("."), "kind": "var_pos", "default": False})
            continue
        name = _text(first, src) if first.type == "identifier" else f"arg{len(out)}"
        if name == "this" or first.type == "this":
            continue
        out.append({"name": name, "kind": "pos_only", "default": child.type == "optional_parameter"})
    return out


def _member(member, owner: str, src: bytes, in_interface: bool = False) -> dict:
    if member.type not in ("method_signature", "method_definition", "abstract_method_signature",
                           "public_field_definition", "property_signature"):
        return {}
    mods = [c for c in member.children if c.type == "accessibility_modifier"]
    if mods and _text(mods[0], src) in ("private", "protected"):
        return {}
    name_node = member.child_by_field_name("name") or next(
        (c for c in member.children if c.type == "property_identifier"), None)
    if name_node is None or name_node.type != "property_identifier":
        return {}                                   # #private and computed names are not API
    kinds = {c.type for c in member.children}
    key = f"{owner}.{_text(name_node, src)}"
    if member.type in ("public_field_definition", "property_signature") or "get" in kinds or "set" in kinds:
        return {key: {"kind": "property", "optional": "?" in kinds, "static": "static" in kinds}} if not in_interface \
            else {key: {"kind": "property", "optional": "?" in kinds}}
    return {key: {"kind": "method", "params": _params(member.child_by_field_name("parameters"), src),
                  "static": "static" in kinds}}


def _declaration(node, src: bytes, deprecated: bool) -> dict[str, dict]:
    kind, name = node.type, _name_of(node, src)
    body = node.child_by_field_name("body")
    members = body.named_children if body is not None else []
    if kind in ("function_signature", "function_declaration"):
        return {name: {"kind": "function", "params": _params(node.child_by_field_name("parameters"), src),
                       "deprecated": deprecated}} if name else {}
    if kind in ("class_declaration", "abstract_class_declaration"):
        out = {name: {"kind": "class", "deprecated": deprecated}}
        for m in members:
            out.update(_member(m, name, src))
        return out
    if kind == "interface_declaration":
        out = {name: {"kind": "interface", "deprecated": deprecated}}
        for m in members:
            out.update(_member(m, name, src, in_interface=True))
        return out
    if kind == "type_alias_declaration":
        return {name: {"kind": "type", "deprecated": deprecated}}
    if kind in ("lexical_declaration", "variable_declaration"):
        out = {}
        for d in node.named_children:
            n = d.child_by_field_name("name") if d.type == "variable_declarator" else None
            if n is not None and n.type == "identifier":
                out[_text(n, src)] = {"kind": "constant", "deprecated": deprecated}
        return out
    if kind == "enum_declaration":
        out = {name: {"kind": "enum", "deprecated": deprecated}}
        for m in members:
            n = m.child_by_field_name("name") or m
            out[f"{name}.{_text(n, src)}"] = {"kind": "enum_member"}
        return out
    if kind in ("internal_module", "module"):
        return {name: {"kind": "namespace", "deprecated": deprecated}}
    return {}


def _unwrap(node):
    if node.type in _DECLARATIONS:
        return node
    for child in node.children:
        if child.type == "ambient_declaration":
            return next((c for c in child.children if c.type in _DECLARATIONS), None)
        if child.type in _DECLARATIONS:
            return child
    return None


def _rename(table: dict, original: str, public: str) -> dict:
    if original not in table:
        return {}
    out = {public: table[original]}
    out.update({public + k[len(original):]: v for k, v in table.items() if k.startswith(original + ".")})
    return out


def _scan(text: str):
    src = text.encode("utf-8", "replace")
    root = _parser().parse(src).root_node
    hidden: dict[str, dict] = {}
    local: dict[str, dict] = {}
    reexports: list[tuple[str, str, str]] = []
    star: list[str] = []
    pending_local: list[tuple[str, str]] = []
    for node in root.children:
        if node.type == "export_statement":
            clause = next((c for c in node.children if c.type == "export_clause"), None)
            source = next((c for c in node.children if c.type == "string"), None)
            spec = _text(source, src).strip("\"'") if source is not None else None
            if clause is not None:
                for item in clause.named_children:
                    names = [c for c in item.named_children if c.type == "identifier"]
                    if item.type != "export_specifier" or not names:
                        continue
                    original, public = _text(names[0], src), _text(names[-1], src)
                    if spec is not None:
                        reexports.append((original, public, spec))
                    else:
                        pending_local.append((original, public))
            elif spec is not None and any(c.type == "*" for c in node.children):
                star.append(spec)
            elif spec is None:
                decl = _unwrap(node)
                if decl is not None:
                    entries = _declaration(decl, src, _deprecated(node, src))
                    if any(c.type == "default" for c in node.children):
                        first = next(iter(entries.values()), None)
                        if first is not None:
                            local["default"] = first
                    else:
                        local.update(entries)
        elif node.type == "ambient_declaration" or node.type in _DECLARATIONS:
            decl = _unwrap(node)
            if decl is not None:
                hidden.update(_declaration(decl, src, _deprecated(node, src)))
    for original, public in pending_local:
        local.update(_rename({**hidden, **local}, original, public))
    return local, reexports, star


def _resolve(files: dict[str, str], from_path: str, spec: str) -> str | None:
    if not spec.startswith("."):
        return None
    base = posixpath.normpath(posixpath.join(posixpath.dirname(from_path), spec))
    candidates = [base + ".d.ts", base, base + "/index.d.ts"]
    if base.endswith(".js"):
        candidates.insert(0, base[:-3] + ".d.ts")
    return next((c for c in candidates if c in files and c.endswith(_DTS)), None)


def _exports(files: dict[str, str], path: str, stack: frozenset, cache: dict) -> dict:
    if path in cache:
        return cache[path]
    if path in stack or len(stack) >= MAX_DEPTH:
        return {}
    local, reexports, star = _scan(files[path])
    out = dict(local)
    for spec in star:
        target = _resolve(files, path, spec)
        if target:
            for key, value in _exports(files, target, stack | {path}, cache).items():
                out.setdefault(key, value)
    for original, public, spec in reexports:
        target = _resolve(files, path, spec)
        if target:
            out.update(_rename(_exports(files, target, stack | {path}, cache), original, public))
    cache[path] = out
    return out


def _types_in(node):
    if isinstance(node, str):
        return node if node.endswith(_DTS) else None
    if isinstance(node, dict):
        for key in ("types", "import", "require", "default"):
            found = _types_in(node.get(key))
            if found:
                return found
    return None


def _entries(files: dict[str, str]) -> dict[str, str]:
    """Public type entry points: "." for the package root plus every literal subpath whose export lists a types file."""
    try:
        pkg = json.loads(files.get("package.json") or "{}")
    except ValueError:
        pkg = {}
    pkg = pkg if isinstance(pkg, dict) else {}
    exports = pkg.get("exports")
    root = [pkg.get("types"), pkg.get("typings"),
            _types_in(exports.get(".", exports) if isinstance(exports, dict) else exports)]
    if isinstance(pkg.get("main"), str):
        root.append(re.sub(r"\.[cm]?jsx?$", ".d.ts", pkg["main"]))
    root += ["index.d.ts", "dist/index.d.ts"]
    out: dict[str, str] = {}
    for candidate in root:
        if isinstance(candidate, str) and posixpath.normpath(candidate.removeprefix("./")) in files:
            out["."] = posixpath.normpath(candidate.removeprefix("./"))
            break
    if isinstance(exports, dict):
        for sub, target in exports.items():
            declared = _types_in(target) if sub.startswith("./") and "*" not in sub else None
            if declared and posixpath.normpath(declared.removeprefix("./")) in files:
                out[sub] = posixpath.normpath(declared.removeprefix("./"))
    return out


def snapshot_typescript(files: dict[str, str]) -> dict:
    """Symbols of the root export are keyed by name; symbols of a subpath export by "./subpath#name"."""
    if files and all(name.startswith("package/") for name in files):
        files = {name.removeprefix("package/"): text for name, text in files.items()}
    entries = _entries(files)
    if not entries:
        raise ValueError("no TypeScript declaration entry point (types / exports / index.d.ts)")
    symbols: dict[str, dict] = {}
    for subpath, path in entries.items():
        for key, value in _exports(files, path, frozenset(), {}).items():
            symbols[key if subpath == "." else f"{subpath}#{key}"] = value
    # ponytail: overloaded signatures keep the last declaration only; type aliases are not compared structurally.
    return {"entries": entries, "symbols": dict(sorted(symbols.items()))}
