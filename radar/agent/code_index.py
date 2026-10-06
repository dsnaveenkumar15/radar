"""Where the repo uses each tracked library: imports, aliases, attribute chains, with file and line. No model."""
import ast
import re


def module_roots(sources) -> dict[str, dict[str, str]]:
    """Import root -> package key, one map per ecosystem: `openai` is both a PyPI module and an npm package."""
    roots: dict[str, dict[str, str]] = {"pypi": {}, "npm": {}}
    for s in sources:
        if not (s.api and s.package):
            continue
        ecosystem, _, name = s.package.partition(":")
        roots.setdefault(ecosystem, {})[(s.module or name.replace("-", "_")) if ecosystem == "pypi" else name] = s.package
    return roots


def _package_for(dotted: str, roots: dict[str, str]) -> str | None:
    best = None
    for root, key in roots.items():
        if (dotted == root or dotted.startswith(root + ".")) and (best is None or len(root) > len(best[0])):
            best = (root, key)
    return best[1] if best else None


def _chain(node) -> list[str] | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return [node.id] + parts[::-1]
    return None


def index_python(files: dict[str, str], roots: dict[str, str]) -> list[dict]:
    usages = []
    for path, text in sorted(files.items()):
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            continue
        lines = text.splitlines()
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if _package_for(a.name, roots):
                        aliases[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for a in node.names:
                    full = f"{node.module}.{a.name}"
                    if a.name != "*" and _package_for(full, roots):
                        aliases[a.asname or a.name] = full
        seen = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Attribute, ast.Name)) or not hasattr(node, "lineno"):
                continue
            chain = _chain(node)
            if not chain or chain[0] not in aliases:
                continue
            symbol = ".".join([aliases[chain[0]]] + chain[1:])
            package = _package_for(symbol, roots)
            if package and (node.lineno, symbol) not in seen:
                seen.add((node.lineno, symbol))
                usages.append({"path": path, "line": node.lineno, "symbol": symbol, "package": package,
                               "text": lines[node.lineno - 1].strip()[:300]})
    # keep the longest chain per line (PT.from_template, not also PT)
    longest = {}
    for u in usages:
        k = (u["path"], u["line"])
        longest.setdefault(k, []).append(u)
    out = []
    for group in longest.values():
        out.extend(u for u in group if not any(o is not u and o["symbol"].startswith(u["symbol"] + ".") for o in group))
    return sorted(out, key=lambda u: (u["path"], u["line"], u["symbol"]))


def _same_symbol(use: list[str], defined: list[str]) -> bool:
    """Radar names a symbol by the module that defines it (pkg.prompts.chat.Cls); code imports it from the package's
    public path (pkg.prompts.Cls). They match when the public path is the defined path with modules left out."""
    if len(use) == 1 or len(defined) == 1:
        return use == defined
    if use[0] != defined[0] or use[-1] != defined[-1]:
        return False
    rest = iter(defined[1:-1])
    return all(seg in rest for seg in use[1:-1])


def _is_or_extends(use: list[str], defined: list[str]) -> bool:
    return any(_same_symbol(use[:k], defined) for k in range(1, len(use) + 1))


def _member_line(text: str, leaf: str) -> tuple[int, str] | None:
    """First line that accesses `.leaf` (leaf is already regex-escaped)."""
    pattern = re.compile(r"\." + leaf + r"\b")
    for n, line in enumerate(text.splitlines(), 1):
        if pattern.search(line):
            return n, line.strip()[:300]
    return None


def candidates(changes: list[dict], usages: list[dict], files: dict[str, str]) -> list[dict]:
    out = []
    for change in changes:
        target = change["symbol"].split(".")
        parent = target[:-1]
        leaf = re.escape(target[-1])
        for u in usages:
            if u["package"] != change["package"]:
                continue
            used = u["symbol"].split(".")
            if _is_or_extends(used, target):
                strength = "exact"
            elif parent and _is_or_extends(used, parent) and (member := _member_line(files.get(u["path"], ""), leaf)):
                strength = "possible"        # a member of something the file uses, reached through an instance
                u = {**u, "line": member[0], "text": member[1]}         # point at the line that uses the member
            else:
                continue
            risk = 1 if change["kind"] == "deprecation" else (3 if strength == "exact" else 2)
            out.append({"change": change, "usage": u, "strength": strength, "risk": risk})
    # One candidate per change and file: exact matches are all kept, a file with none keeps its first "possible" one.
    exact = {(c["change"]["symbol"], c["usage"]["path"]) for c in out if c["strength"] == "exact"}
    unique = {}
    for c in sorted(out, key=lambda c: (c["strength"] != "exact", c["usage"]["line"])):
        key = (c["change"]["symbol"], c["usage"]["path"])
        if c["strength"] == "exact":
            unique.setdefault(key + (c["usage"]["line"],), c)
        elif key not in exact:
            unique.setdefault(key, c)
    # Radar lists a symbol under its defining module and under its public name; both on one line are one change.
    one_per_line: dict[tuple, dict] = {}
    for c in sorted(unique.values(), key=lambda c: (c["strength"] != "exact", len(c["change"]["symbol"].split(".")), c["change"]["symbol"])):
        one_per_line.setdefault((c["usage"]["path"], c["usage"]["line"], c["change"]["symbol"].rsplit(".", 1)[-1], c["change"]["kind"]), c)
    ranked = sorted(one_per_line.values(), key=lambda c: (-c["risk"], c["usage"]["path"], c["usage"]["line"]))
    for i, c in enumerate(ranked, 1):
        c["id"] = f"c{i}"
    return ranked


# --- TypeScript / JavaScript -------------------------------------------------
# Supported: ES `import` (named, aliased, namespace, default) from a root or subpath listed in `roots`.
# Not supported: CommonJS `require("pkg")` (no alias tracking); dynamic import().
from radar.api_ts import _parser

TS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
_IDENT_CHAIN = r"(?<![\w$.]){name}(?![\w$])((?:\.[A-Za-z_$][\w$]*)*)"


def _t(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _ts_imports(root, src: bytes, roots: dict[str, str]) -> dict[str, tuple[str, str]]:
    """local name -> (package key, export path prefix); '' prefix means a namespace import."""
    aliases = {}
    for node in root.children:
        if node.type != "import_statement":
            continue
        source = node.child_by_field_name("source")
        if source is None:
            continue
        package = roots.get(_t(source, src).strip("'\""))
        if package is None:
            continue
        clause = next((c for c in node.children if c.type == "import_clause"), None)
        if clause is None:
            continue
        for part in clause.children:
            if part.type == "identifier":  # default import; snapshots key default exports as "default"
                aliases[_t(part, src)] = (package, "default")
            elif part.type == "namespace_import":
                name = next(c for c in part.children if c.type == "identifier")
                aliases[_t(name, src)] = (package, "")
            elif part.type == "named_imports":
                for spec_node in part.children:
                    if spec_node.type != "import_specifier":
                        continue
                    names = [c for c in spec_node.children if c.type == "identifier"]
                    if names:
                        aliases[_t(names[-1], src)] = (package, _t(names[0], src))
    return aliases


def _blank(root, src: bytes) -> str:
    """Source with comments, strings and import statements replaced by spaces (line numbers preserved)."""
    out = bytearray(src)
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in ("comment", "string", "import_statement", "string_fragment"):
            for i in range(node.start_byte, node.end_byte):
                if out[i] not in (10, 13):
                    out[i] = 32
            continue
        stack.extend(node.children)  # template_string: only its fragments are blanked, ${...} code stays
    return out.decode("utf-8", "replace")


def index_typescript(files: dict[str, str], roots: dict[str, str]) -> list[dict]:
    usages = []
    for path, text in sorted(files.items()):
        if not path.endswith(TS_EXT):
            continue
        src = text.encode("utf-8")
        tree = _parser().parse(src)
        if tree.root_node.has_error and not any(c.type == "import_statement" for c in tree.root_node.children):
            continue
        aliases = _ts_imports(tree.root_node, src, roots)
        if not aliases:
            continue
        clean = _blank(tree.root_node, src).splitlines()
        lines = text.splitlines()
        for n, line in enumerate(clean, 1):
            for local, (package, prefix) in aliases.items():
                for m in re.finditer(_IDENT_CHAIN.format(name=re.escape(local)), line):
                    symbol = ".".join(p for p in (prefix, m.group(1).lstrip(".")) if p)
                    if symbol:
                        usages.append({"path": path, "line": n, "symbol": symbol, "package": package,
                                       "text": lines[n - 1].strip()[:300]})
    return sorted({(u["path"], u["line"], u["symbol"]): u for u in usages}.values(),
                  key=lambda u: (u["path"], u["line"], u["symbol"]))
