import pytest

from radar.api_py import snapshot_python
from radar.apidiff import breaking_symbols, diff_snapshots, param_changes


def py(code):
    return snapshot_python({"pkg/m.py": code}, "pkg")


def breaking(old, new):
    return breaking_symbols(diff_snapshots(py(old), py(new)))


@pytest.mark.parametrize("old, new, expected", [
    ("def f(a): ...", "", ["pkg.m.f"]),                                              # removed
    ("def f(a): ...", "def f(a, b): ...", ["pkg.m.f"]),                              # new required parameter
    ("def f(a): ...", "def f(a, b=1): ...", []),                                      # new optional parameter
    ("def f(a, b=1): ...", "def f(a, b): ...", ["pkg.m.f"]),                          # optional became required
    ("def f(a, b): ...", "def f(a, c): ...", ["pkg.m.f"]),                            # renamed parameter
    ("def f(a, b): ...", "def f(b, a): ...", ["pkg.m.f"]),                            # reordered
    ("def f(a, b): ...", "def f(a): ...", ["pkg.m.f"]),                               # parameter removed
    ("def f(a, b): ...", "def f(a, *args, **kw): ...", []),                           # swallowed by *args/**kw
    ("def f(a, b=1): ...", "def f(a, *, b=1): ...", ["pkg.m.f"]),                     # now keyword-only
    ("def f(a, *, k=1): ...", "def f(a): ...", ["pkg.m.f"]),                          # keyword parameter removed
    ("def f(a): ...", "def f(a, *, k): ...", ["pkg.m.f"]),                            # new required keyword parameter
    ("def f(): ...", "def f(): ...\ndef g(): ...", []),                               # added symbol
    ("class A:\n    def run(self, x): ...", "class A:\n    pass", ["pkg.m.A.run"]),     # method removed
    ("class A: ...\ndef f(): ...", "def A(): ...\ndef f(): ...", ["pkg.m.A"]),         # kind changed
    ("def f(a): ...", "def _f(a): ...", ["pkg.m.f"]),                                 # made private
])
def test_breaking_classification(old, new, expected):
    assert breaking(old, new) == expected


def test_removed_class_reports_once_not_per_member():
    d = diff_snapshots(py("class A:\n    def run(self): ...\n    def stop(self): ..."), py(""))
    assert [c["symbol"] for c in d["breaking"]] == ["pkg.m.A"] and d["counts"]["removed"] == 3


def test_likely_rename_is_paired_and_not_listed_as_added():
    d = diff_snapshots(py("def run_agent(a, b): ..."), py("def run_agents(a, b): ..."))
    assert d["breaking"][0]["detail"] == "likely renamed to pkg.m.run_agents" and d["breaking"][0]["to"] == "pkg.m.run_agents"
    assert d["added"] == []
    dissimilar = diff_snapshots(py("def alpha(a): ..."), py("def zzzzzz(a): ..."))
    assert dissimilar["added"] == ["pkg.m.zzzzzz"] and "renamed" not in dissimilar["breaking"][0]["detail"]


def test_new_deprecation_is_notable_and_listed_separately():
    d = diff_snapshots(py("def f(): ..."), py('import warnings\ndef f():\n    warnings.warn("x", DeprecationWarning)\n'))
    assert d["breaking"] == [] and d["deprecations"][0]["symbol"] == "pkg.m.f" and d["deprecations"][0]["severity"] == "notable"
    assert diff_snapshots(py("def f(): ..."), py("def f(): ..."))["counts"] == {"breaking": 0, "deprecations": 0, "added": 0, "removed": 0}


def test_param_changes_for_typescript_style_positions_ignores_names():
    old = [{"name": "a", "kind": "pos_only", "default": False}]
    renamed = [{"name": "z", "kind": "pos_only", "default": False}]
    assert param_changes(old, renamed) == []
    assert param_changes(old, old + [{"name": "b", "kind": "pos_only", "default": True}]) == []
    assert param_changes(old, old + [{"name": "b", "kind": "pos_only", "default": False}]) == ["new required parameter 'b'"]


def test_lists_are_capped_and_flagged():
    old = "\n".join(f"def f{i}(): ..." for i in range(150))
    d = diff_snapshots(py(old), py(""))
    assert len(d["breaking"]) == 100 and d["counts"]["breaking"] == 150 and d["truncated"] is True


def test_a_member_removed_from_a_subclass_may_be_inherited_so_it_is_not_breaking():
    old = """
class Base:
    def f(self): ...
class Child(Base):
    def f(self): ...
    def g(self): ...
"""
    new = """
class Base:
    def f(self): ...
class Child(Base):
    pass
"""
    d = diff_snapshots(py(old), py(new))
    assert d["breaking"] == [] and [c["symbol"] for c in d["other"]] == ["pkg.m.Child.f", "pkg.m.Child.g"]
    assert d["other"][0]["change"] == "removed_maybe_inherited" and d["other"][0]["severity"] == "notable"
    assert breaking("class A:\n    def g(self): ...", "class A:\n    pass") == ["pkg.m.A.g"]       # no bases: really gone


def test_model_field_changes_are_notable_but_dataclass_and_typeddict_changes_break():
    model = "from pydantic import BaseModel\nclass M(BaseModel):\n    a: int\n"
    d = diff_snapshots(py(model), py(model + "    b: int\n"))
    assert d["breaking"] == [] and d["other"][0]["symbol"] == "pkg.m.M.__init__" and d["other"][0]["severity"] == "notable"
    typed = "from typing import TypedDict\nclass T(TypedDict):\n    a: int\n"
    assert breaking(typed, typed + "    b: int\n") == ["pkg.m.T.__init__"]
    loose = "from typing import TypedDict\nclass T(TypedDict, total=False):\n    a: int\n"
    assert breaking(loose, loose + "    b: int\n") == []
