from radar.api_py import snapshot_python


def snap(files, module="pkg"):
    return snapshot_python(files, module)["symbols"]


def test_functions_and_parameter_kinds():
    s = snap({"pkg/__init__.py": "", "pkg/core.py":
              "def run(a, /, b, c=1, *args, d, e=2, **kw): ...\nasync def go(x): ...\ndef _hidden(): ...\n"})
    assert s["pkg.core.run"]["params"] == [
        {"name": "a", "kind": "pos_only", "default": False}, {"name": "b", "kind": "pos_or_kw", "default": False},
        {"name": "c", "kind": "pos_or_kw", "default": True}, {"name": "args", "kind": "var_pos", "default": False},
        {"name": "d", "kind": "kw_only", "default": False}, {"name": "e", "kind": "kw_only", "default": True},
        {"name": "kw", "kind": "var_kw", "default": False}]
    assert s["pkg.core.go"]["async"] is True and "pkg.core._hidden" not in s


def test_class_members_properties_and_static_methods():
    s = snap({"pkg/a.py": '''
class Agent:
    """Doc."""
    def __init__(self, model, *, tools=None): ...
    def run(self, prompt, retries=3): ...
    @staticmethod
    def make(x): ...
    @property
    def name(self): ...
    def _private(self): ...
    class Inner: ...
'''})
    assert s["pkg.a.Agent"] == {"kind": "class", "deprecated": False, "bases": []}
    assert [p["name"] for p in s["pkg.a.Agent.run"]["params"]] == ["prompt", "retries"]      # self dropped
    assert s["pkg.a.Agent.__init__"]["params"][1] == {"name": "tools", "kind": "kw_only", "default": True}
    assert s["pkg.a.Agent.make"]["static"] is True and s["pkg.a.Agent.make"]["params"][0]["name"] == "x"
    assert s["pkg.a.Agent.name"] == {"kind": "property"}
    assert "pkg.a.Agent._private" not in s and "pkg.a.Agent.Inner" not in s


def test_dataclass_and_model_classes_get_a_synthesized_init():
    s = snap({"pkg/c.py": '''
from dataclasses import dataclass
from pydantic import BaseModel

@dataclass
class Config:
    model: str
    retries: int = 3
    _internal: int = 0

class Options(BaseModel):
    temperature: float = 0.0
    name: str
'''})
    assert s["pkg.c.Config.__init__"]["params"] == [{"name": "model", "kind": "pos_or_kw", "default": False},
                                                   {"name": "retries", "kind": "pos_or_kw", "default": True}]
    assert s["pkg.c.Options.__init__"]["params"] == [{"name": "temperature", "kind": "kw_only", "default": True},
                                                    {"name": "name", "kind": "kw_only", "default": False}]


def test_deprecation_markers():
    s = snap({"pkg/m.py": '''
import warnings
from typing_extensions import deprecated

@deprecated("use b")
def a(): ...

def b():
    warnings.warn("old", DeprecationWarning, stacklevel=2)

def c():
    """Do it.

    .. deprecated:: 1.2
    """

def d(): ...
'''})
    assert [s[f"pkg.m.{n}"]["deprecated"] for n in "abcd"] == [True, True, True, False]


def test_dunder_all_decides_what_is_public():
    s = snap({"pkg/m.py": "__all__ = ['kept', '_forced']\ndef kept(): ...\ndef _forced(): ...\ndef other(): ...\n"})
    assert set(s) == {"pkg.m.kept", "pkg.m._forced"}


def test_reexports_resolve_through_private_modules_and_chains():
    s = snap({
        "pkg/__init__.py": "from .graph import build\nfrom ._impl import Engine\n",
        "pkg/graph/__init__.py": "from .state import build\n",
        "pkg/graph/state.py": "def build(a, b=1): ...\n",
        "pkg/_impl.py": "class Engine:\n    def start(self, now): ...\n",
    })
    assert s["pkg.build"]["params"][0]["name"] == "a" and s["pkg.build"]["alias_of"] == "pkg.graph.state.build"
    assert s["pkg.graph.build"]["alias_of"] == "pkg.graph.state.build"
    assert s["pkg.Engine"]["kind"] == "class"
    assert s["pkg.Engine.start"]["params"] == [{"name": "now", "kind": "pos_or_kw", "default": False}]
    assert "pkg._impl.Engine" not in s


def test_namespace_package_module_roots_and_sdist_prefixes():
    s = snap({"llama_index/core/__init__.py": "from .base import Thing\n", "llama_index/core/base.py": "class Thing: ...\n",
              "llama_index/__init__.py": "def outside(): ...\n"}, module="llama_index.core")
    assert "llama_index.core.Thing" in s and not any(k.endswith("outside") for k in s)
    t = snap({"pkg-1.0/src/pkg/a.py": "def f(): ...\n", "pkg-1.0/tests/test_a.py": "def t(): ...\n",
              "pkg-1.0/src/pkg/tests/t.py": "def g(): ...\n"})
    assert set(t) == {"pkg.a.f"}


def test_unparseable_files_are_skipped_and_counted():
    result = snapshot_python({"pkg/ok.py": "def f(): ...\n", "pkg/bad.py": "def (:\n", "pkg/deep.py": "x = " + "(" * 400 + "1" + ")" * 400},
                             "pkg")
    assert set(result["symbols"]) == {"pkg.ok.f"} and result["skipped"] >= 1


def test_typed_dicts_respect_total_false_and_not_required():
    s = snap({"pkg/t.py": """
from typing import TypedDict
from typing_extensions import NotRequired

class Strict(TypedDict):
    a: str
    b: NotRequired[int]

class Loose(TypedDict, total=False):
    c: str
"""})
    assert s["pkg.t.Strict.__init__"]["params"] == [{"name": "a", "kind": "kw_only", "default": False},
                                                   {"name": "b", "kind": "kw_only", "default": True}]
    assert s["pkg.t.Loose.__init__"]["params"] == [{"name": "c", "kind": "kw_only", "default": True}]


def test_classes_record_their_bases_and_models_are_flagged():
    s = snap({"pkg/b.py": """
from pydantic import BaseModel

class Parent: ...
class Child(Parent): ...
class Out(BaseModel):
    x: int
"""})
    assert s["pkg.b.Child"]["bases"] == ["Parent"] and s["pkg.b.Parent"]["bases"] == []
    assert s["pkg.b.Out.__init__"]["model"] is True
