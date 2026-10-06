from radar.agent.code_index import candidates, index_python, module_roots
from radar.config import Source

ROOTS = {"langchain_core": "pypi:langchain-core", "openai": "pypi:openai"}
CODE = '''import openai as oa
from langchain_core.prompts import PromptTemplate as PT
from langchain_core import messages

# PT.from_template is mentioned in a comment only
s = "langchain_core.prompts.PromptTemplate.old"
t = PT.from_template("x")
client = oa.OpenAI()
msg = messages.HumanMessage(content="hi")
t.partial(a=1)
'''


def test_module_roots_from_sources():
    srcs = [Source(id="a", type="t", tier="o", package="pypi:llama-index-core", module="llama_index.core", api=True),
            Source(id="b", type="t", tier="o", package="npm:openai", api=True)]
    assert module_roots(srcs) == {"llama_index.core": "pypi:llama-index-core", "openai": "npm:openai"}


def test_python_usages_resolve_aliases_and_ignore_comments_and_strings():
    usages = index_python({"app/main.py": CODE}, ROOTS)
    got = {(u["line"], u["symbol"]) for u in usages}
    assert (7, "langchain_core.prompts.PromptTemplate.from_template") in got
    assert (8, "openai.OpenAI") in got and (9, "langchain_core.messages.HumanMessage") in got
    assert not any(u["line"] in (5, 6) for u in usages)
    assert all(u["package"] in ROOTS.values() and u["text"] for u in usages)


def test_candidates_exact_and_possible_and_unrelated_ignored():
    usages = index_python({"app/main.py": CODE}, ROOTS)
    changes = [
        {"package": "pypi:langchain-core", "symbol": "langchain_core.prompts.PromptTemplate.from_template", "kind": "breaking", "change": "removed", "detail": "removed", "from": "0.2", "to": "0.3"},
        {"package": "pypi:langchain-core", "symbol": "langchain_core.prompts.PromptTemplate.partial", "kind": "breaking", "change": "params", "detail": "x", "from": "0.2", "to": "0.3"},
        {"package": "pypi:openai", "symbol": "openai.Completion", "kind": "breaking", "change": "removed", "detail": "removed", "from": "0.2", "to": "0.3"},
    ]
    cands = candidates(changes, usages, {"app/main.py": CODE})
    by_symbol = {c["change"]["symbol"]: c for c in cands}
    assert by_symbol["langchain_core.prompts.PromptTemplate.from_template"]["strength"] == "exact"
    assert by_symbol["langchain_core.prompts.PromptTemplate.partial"]["strength"] == "possible"   # `.partial` on an instance
    assert "openai.Completion" not in by_symbol
    assert cands[0]["risk"] == 3 and [c["id"] for c in cands] == [f"c{i}" for i in range(1, len(cands) + 1)]


def _symbols(code, roots):
    return {(u["line"], u["symbol"]) for u in index_python({"a.py": code}, roots)}


def test_import_submodule_as_alias():
    assert (2, "x.sub.f") in _symbols("import x.sub as y\ny.f()\n", {"x": "pypi:x"})


def test_plain_import_with_attribute_chain():
    assert (2, "x.sub.f") in _symbols("import x\nx.sub.f()\n", {"x": "pypi:x"})


def test_star_import_ignored():
    assert _symbols("from x import *\nf()\n", {"x": "pypi:x"}) == set()


def test_syntax_error_file_skipped():
    files = {"bad.py": "def (:\n", "ok.py": "import x\nx.f()\n"}
    assert {u["path"] for u in index_python(files, {"x": "pypi:x"})} == {"ok.py"}


def test_non_python_file_skipped():
    assert index_python({"a.txt": "import x\nx.f()\n"}, {"x": "pypi:x"}) == []


def test_module_roots_longest_prefix_wins():
    roots = {"a": "pypi:a", "a.b": "pypi:a-b"}
    usages = index_python({"m.py": "from a.b import c\nc.f()\nimport a\na.g()\n"}, roots)
    assert {(u["symbol"], u["package"]) for u in usages} == {("a.b.c.f", "pypi:a-b"), ("a.g", "pypi:a")}


def test_shadowed_name_is_a_known_false_positive():
    # known limitation: no scope analysis, a parameter named like an import alias still matches
    code = "import openai\ndef f(openai):\n    openai.Foo()\n"
    assert (3, "openai.Foo") in _symbols(code, {"openai": "pypi:openai"})


def test_one_candidate_per_change_and_file_and_exact_beats_possible():
    code = "import x\nx.fine()\nx.other()\n"
    usages = index_python({"a.py": code}, {"x": "pypi:x"})
    mk = lambda s: {"package": "pypi:x", "symbol": s, "kind": "breaking", "change": "params", "detail": "d", "from": "1", "to": "2"}
    cands = candidates([mk("x.fine"), mk("x.other"), mk("x.ghost")], usages, {"a.py": code})
    assert sorted((c["change"]["symbol"], c["usage"]["line"], c["strength"]) for c in cands) == [
        ("x.fine", 2, "exact"), ("x.other", 3, "exact")]
    only_sibling = candidates([mk("x.later")], index_python({"a.py": "import x\nx.fine()\n# note\nz = y.later\n"}, {"x": "pypi:x"}),
                              {"a.py": "import x\nx.fine()\n# note\nz = y.later\n"})
    assert [(c["strength"], c["usage"]["line"]) for c in only_sibling] == [("possible", 2)]       # one, not one per sibling line


LC = "from langchain_core.prompts import ChatPromptTemplate\nt = ChatPromptTemplate.from_messages([])\nu = ChatPromptTemplate\nv = ChatPromptTemplate()\n"


def _lc(symbol):
    usages = index_python({"a.py": LC}, {"langchain_core": "pypi:langchain-core"})
    ch = {"package": "pypi:langchain-core", "symbol": symbol, "kind": "breaking", "change": "removed", "detail": "d", "from": "1", "to": "2"}
    return candidates([ch], usages, {"a.py": LC})


def test_public_import_paths_match_radars_defining_module_paths():
    # Radar names symbols by where they are defined; code imports them from the package's public path.
    exact = _lc("langchain_core.prompts.chat.ChatPromptTemplate.from_messages")
    assert [(c["strength"], c["usage"]["line"]) for c in exact] == [("exact", 2)]
    cls = _lc("langchain_core.prompts.chat.ChatPromptTemplate")
    assert {c["usage"]["line"] for c in cls} == {2, 3, 4} and all(c["strength"] == "exact" for c in cls)


def test_unrelated_symbols_in_other_modules_do_not_match():
    assert _lc("langchain_core.messages.chat.ChatPromptTemplate") == []          # module not on the usage path
    assert _lc("langchain_core.prompts.chat.OtherThing.from_messages") == []


def test_pathological_python_files_are_skipped_not_fatal():
    deep = "import x\ny = x" + ".b" * 100_000 + "\n"
    assert index_python({"deep.py": deep, "ok.py": "import x\nx.f()\n"}, {"x": "pypi:x"}) == [
        {"path": "ok.py", "line": 2, "symbol": "x.f", "package": "pypi:x", "text": "x.f()"}]
