"""Public API surface of a Python package from its source files. Parsed with ast; never imported or run."""
import ast

IGNORED_DIRS = {"tests", "test", "testing", "docs", "examples", "benchmarks", "scripts"}
RESOLVE_PASSES = 5
_WARNING_NAMES = {"DeprecationWarning", "PendingDeprecationWarning", "FutureWarning"}
_PROPERTY_DECORATORS = {"property", "cached_property", "setter", "getter", "deleter"}
_KW_ONLY_BASES = ("BaseModel", "TypedDict")


def _module_name(path: str, module: str) -> tuple[str, bool] | None:
    """Dotted module name of `path` relative to the package root, and whether it is an __init__ file."""
    if not path.endswith(".py"):
        return None
    root, parts = module.split("."), path.split("/")
    for i in range(len(parts) - len(root) + 1):
        if parts[i:i + len(root)] == root:
            rel = parts[i:]
            break
    else:
        return None
    if any(p in IGNORED_DIRS for p in rel[len(root):-1]):
        return None
    last = rel[-1][:-3]
    return ".".join(rel[:-1] + ([] if last == "__init__" else [last])), last == "__init__"


def _dotted(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    if isinstance(node, (ast.Subscript,)):
        return _dotted(node.value)
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ""


def _decorator_names(node) -> list[str]:
    return [_dotted(d).rsplit(".", 1)[-1] for d in node.decorator_list]


def _params(args: ast.arguments, drop_first: bool) -> list[dict]:
    positional = [*args.posonlyargs, *args.args]
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    out = [{"name": a.arg, "kind": "pos_only" if i < len(args.posonlyargs) else "pos_or_kw", "default": d is not None}
           for i, (a, d) in enumerate(zip(positional, defaults))]
    if args.vararg:
        out.append({"name": args.vararg.arg, "kind": "var_pos", "default": False})
    out += [{"name": a.arg, "kind": "kw_only", "default": d is not None} for a, d in zip(args.kwonlyargs, args.kw_defaults)]
    if args.kwarg:
        out.append({"name": args.kwarg.arg, "kind": "var_kw", "default": False})
    if drop_first and out and out[0]["kind"] in ("pos_only", "pos_or_kw"):
        out = out[1:]
    return out


def _warns_deprecated(body) -> bool:
    for stmt in body[:6]:
        call = stmt.value if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) else None
        if call is not None and _dotted(call.func).rsplit(".", 1)[-1] == "warn":
            names = {_dotted(a).rsplit(".", 1)[-1] for a in [*call.args, *(k.value for k in call.keywords)]}
            if names & _WARNING_NAMES:
                return True
    return False


def _deprecated(node) -> bool:
    if any("deprecat" in n.lower() for n in _decorator_names(node)):
        return True
    if ".. deprecated::" in (ast.get_docstring(node) or ""):
        return True
    return not isinstance(node, ast.ClassDef) and _warns_deprecated(node.body)


def _dunder_all(tree: ast.Module) -> set[str] | None:
    for node in tree.body:
        if (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
                and isinstance(node.value, (ast.List, ast.Tuple))):
            return {e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    return None


def _dataclass_kw_only(node: ast.ClassDef) -> bool:
    for dec in node.decorator_list:
        if isinstance(dec, ast.Call):
            for kw in dec.keywords:
                if kw.arg == "kw_only" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    return True
    return False


def _class(node: ast.ClassDef) -> tuple[dict, dict]:
    decorators = _decorator_names(node)
    bases = [b for b in (_dotted(b) for b in node.bases) if b and b != "object"]
    typed = any(b.endswith("TypedDict") for b in bases)
    model = any(b.endswith("BaseModel") for b in bases)
    total = not any(kw.arg == "total" and isinstance(kw.value, ast.Constant) and kw.value.value is False
                    for kw in node.keywords)
    members, fields = {}, []
    for sub in node.body:
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names = _decorator_names(sub)
            if "overload" in names:
                continue
            public = not sub.name.startswith("_") or sub.name == "__init__"
            if set(names) & _PROPERTY_DECORATORS:
                members[sub.name] = {"kind": "property", "_public": public}
            else:
                static = "staticmethod" in names
                members[sub.name] = {"kind": "method", "params": _params(sub.args, drop_first=not static),
                                     "deprecated": _deprecated(sub), "async": isinstance(sub, ast.AsyncFunctionDef),
                                     "static": static, "_public": public}
        elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
            fields.append((sub.target.id, sub.value is not None, _dotted(sub.annotation).rsplit(".", 1)[-1]))
    if "__init__" not in members and ("dataclass" in decorators or typed or model
                                      or any(b.endswith("NamedTuple") for b in bases)):
        kw_only = typed or model or _dataclass_kw_only(node)
        params = []
        for name, has_default, annotation in fields:
            if name.startswith("_") or annotation == "ClassVar":
                continue
            optional = has_default or (typed and annotation != "Required" and (not total or annotation == "NotRequired"))
            params.append({"name": name, "kind": "kw_only" if kw_only else "pos_or_kw", "default": optional})
        members["__init__"] = {"kind": "method", "deprecated": False, "async": False, "static": False,
                               "params": params, "_public": True, **({"model": True} if model else {})}
    return {"kind": "class", "deprecated": _deprecated(node), "bases": bases}, members


def _import_target(node: ast.ImportFrom, name: str, is_init: bool, root: str) -> str | None:
    if node.level == 0:
        mod = node.module or ""
        return mod if mod == root or mod.startswith(root + ".") else None
    base = name.split(".") if is_init else name.split(".")[:-1]
    up = node.level - 1
    if up > len(base):
        return None
    base = base[:len(base) - up]
    if node.module:
        base = base + node.module.split(".")
    result = ".".join(base)
    return result if result == root or result.startswith(root + ".") else None


def _scan_module(name: str, is_init: bool, tree: ast.Module, root: str):
    defs: dict[str, dict] = {}
    visible: set[str] = set()
    pending: list[tuple[str, str]] = []
    module_public = not any(p.startswith("_") for p in name.split(".")[len(root.split(".")):])
    exported = _dunder_all(tree)

    def public(n: str) -> bool:
        return n in exported if exported is not None else not n.startswith("_")

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if "overload" in _decorator_names(node):
                continue
            key = f"{name}.{node.name}"
            defs[key] = {"kind": "function", "params": _params(node.args, False), "deprecated": _deprecated(node),
                         "async": isinstance(node, ast.AsyncFunctionDef)}
            if module_public and public(node.name):
                visible.add(key)
        elif isinstance(node, ast.ClassDef):
            key = f"{name}.{node.name}"
            entry, members = _class(node)
            defs[key] = entry
            class_visible = module_public and public(node.name)
            if class_visible:
                visible.add(key)
            for member, member_entry in members.items():
                defs[f"{key}.{member}"] = member_entry
                if class_visible and member_entry["_public"]:
                    visible.add(f"{key}.{member}")
        elif (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
              and node.targets[0].id.isupper()):
            key = f"{name}.{node.targets[0].id}"
            defs[key] = {"kind": "constant"}
            if module_public and public(node.targets[0].id):
                visible.add(key)
        elif isinstance(node, ast.ImportFrom) and (is_init or exported is not None):
            target = _import_target(node, name, is_init, root)
            if target is None:
                continue
            for alias in node.names:
                shown = alias.asname or alias.name
                if alias.name != "*" and module_public and public(shown):
                    pending.append((f"{name}.{shown}", f"{target}.{alias.name}"))
    return defs, visible, pending


def _plain(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if k != "_public"}


def snapshot_python(files: dict[str, str], module: str) -> dict:
    defs: dict[str, dict] = {}
    visible: set[str] = set()
    pending: list[tuple[str, str]] = []
    skipped = 0
    for path in sorted(files):
        parsed = _module_name(path, module)
        if parsed is None:
            continue
        try:
            found = _scan_module(parsed[0], parsed[1], ast.parse(files[path]), module)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            skipped += 1
            continue
        defs.update(found[0])
        visible |= found[1]
        pending += found[2]
    symbols = {key: _plain(defs[key]) for key in visible}
    for _ in range(RESOLVE_PASSES):
        progress = False
        for key, target in pending:
            source = defs.get(target) or symbols.get(target)
            if source is None or key in symbols:
                continue
            origin = source.get("alias_of", target)
            symbols[key] = {**_plain(source), "alias_of": origin}
            for member_key, member in list(defs.items()):
                if member_key.startswith(target + ".") and member.get("_public"):
                    symbols.setdefault(key + member_key[len(target):], {**_plain(member), "alias_of": member_key})
            for member_key, member in list(symbols.items()):
                if member_key.startswith(target + "."):
                    symbols.setdefault(key + member_key[len(target):],
                                       {**member, "alias_of": member.get("alias_of", member_key)})
            progress = True
        if not progress:
            break
    return {"module": module, "symbols": dict(sorted(symbols.items())), "skipped": skipped}
