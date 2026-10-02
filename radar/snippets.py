"""Migration snippets must parse, and the symbols they use from the tracked package must exist in its new API."""
import ast

import tree_sitter as ts
import tree_sitter_typescript as tst

_PARSER: ts.Parser | None = None


def _parser() -> ts.Parser:
    global _PARSER
    if _PARSER is None:
        _PARSER = ts.Parser(ts.Language(tst.language_typescript()))
    return _PARSER


def parses(language: str, code: str) -> bool:
    if language == "python":
        try:
            ast.parse(code)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            return False
        return True
    return not _parser().parse(code.encode("utf-8", "replace")).root_node.has_error


def _dotted(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else ""
    return ""


def _python_refs(code: str, module: str) -> set[str]:
    tree = ast.parse(code)
    refs: set[str] = set()
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module \
                and (node.module == module or node.module.startswith(module + ".")):
            for alias in node.names:
                refs.add(f"{node.module}.{alias.name}")
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module or alias.name.startswith(module + "."):
                    aliases[alias.asname or alias.name] = alias.name
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            chain = _dotted(node)
            first, _, rest = chain.partition(".")
            if first in aliases and rest:
                refs.add(f"{aliases[first]}.{rest}")
    return refs


def _typescript_refs(code: str, package: str) -> set[str]:
    """Imported names keyed like the API snapshot: "name" for the root, "./subpath#name" for a subpath import."""
    src = code.encode("utf-8", "replace")
    root = _parser().parse(src).root_node
    refs: set[str] = set()
    for node in root.children:
        if node.type != "import_statement":
            continue
        source = next((c for c in node.children if c.type == "string"), None)
        spec = src[source.start_byte:source.end_byte].decode("utf-8", "replace").strip("\"'") if source else ""
        if spec == package:
            prefix = ""
        elif spec.startswith(package + "/"):
            prefix = f"./{spec[len(package) + 1:]}#"
        else:
            continue
        for clause in node.children:
            if clause.type != "import_clause":
                continue
            for part in clause.children:
                if part.type == "named_imports":
                    for spec_node in part.named_children:
                        ident = next((c for c in spec_node.children if c.type == "identifier"), None)
                        if ident is not None:
                            refs.add(prefix + src[ident.start_byte:ident.end_byte].decode("utf-8", "replace"))
    return refs


def check_migration(migration: dict, surfaces: list[dict]) -> str | None:
    language = migration["language"]
    for label in ("before", "after"):
        if not parses(language, migration[label]):
            return f"{label} snippet does not parse"
    if not surfaces:
        return "no API snapshot to verify symbols against"
    verified = 0
    for surface in surfaces:
        if language == "python" and surface["ecosystem"] == "pypi":
            try:
                refs = _python_refs(migration["after"], surface["module"])
            except RecursionError:
                return "after snippet is too deeply nested to check"
        elif language == "typescript" and surface["ecosystem"] == "npm":
            refs = _typescript_refs(migration["after"], surface["module"])
        else:
            continue
        symbols = surface["symbols"]
        known = set(symbols) | {".".join(n.split(".")[:k]) for n in symbols for k in range(1, len(n.split(".")) + 1)}
        for ref in sorted(refs):
            if ref not in known:
                return f"after snippet uses {ref!r}, which is not in the new API"
        verified += sum(1 for r in refs if r in symbols)
    return None if verified else "after snippet uses no symbol that could be verified"
