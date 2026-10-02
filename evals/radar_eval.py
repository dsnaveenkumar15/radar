"""Labeled API-break set for the Radar diff engine. CI fails if breaking-change precision or recall drops."""
import sys

from radar.api_py import snapshot_python
from radar.api_ts import snapshot_typescript
from radar.apidiff import breaking_symbols, diff_snapshots

MIN_PRECISION = 0.9
MIN_RECALL = 0.8


def py(name, old, new, must=(), must_not=()):
    return {"name": name, "lang": "python", "old": old, "new": new, "must": list(must), "must_not": list(must_not)}


def ts(name, old, new, must=(), must_not=()):
    return {"name": name, "lang": "typescript", "old": old, "new": new, "must": list(must), "must_not": list(must_not)}


CASES = [
    py("py-remove-function", "def f(a): ...\ndef g(): ...\n", "def g(): ...\n", ["pkg.m.f"], ["pkg.m.g"]),
    py("py-new-required-param", "def f(a): ...\n", "def f(a, b): ...\n", ["pkg.m.f"]),
    py("py-new-optional-param", "def f(a): ...\n", "def f(a, b=1): ...\n", [], ["pkg.m.f"]),
    py("py-rename-param", "def f(a, b): ...\n", "def f(a, c): ...\n", ["pkg.m.f"]),
    py("py-make-private", "def f(a): ...\n", "def _f(a): ...\n", ["pkg.m.f"]),
    py("py-method-removed", "class A:\n    def run(self, x): ...\n", "class A:\n    pass\n", ["pkg.m.A.run"], ["pkg.m.A"]),
    py("py-optional-became-required", "def f(a, b=1): ...\n", "def f(a, b): ...\n", ["pkg.m.f"]),
    py("py-became-keyword-only", "def f(a, b=1): ...\n", "def f(a, *, b=1): ...\n", ["pkg.m.f"]),
    py("py-additive-release", "def f(a): ...\n", "def f(a): ...\ndef g(): ...\nclass B:\n    def x(self): ...\n", [],
       ["pkg.m.f", "pkg.m.g", "pkg.m.B"]),
    py("py-dataclass-required-field",
       "from dataclasses import dataclass\n@dataclass\nclass C:\n    a: int\n",
       "from dataclasses import dataclass\n@dataclass\nclass C:\n    a: int\n    b: int\n", ["pkg.m.C.__init__"]),
    py("py-deprecation-is-not-breaking", "def f(): ...\n",
       "import warnings\ndef f():\n    warnings.warn('x', DeprecationWarning)\n", [], ["pkg.m.f"]),
    py("py-private-internals-change", "def _helper(a): ...\ndef f(): ...\n", "def f(): ...\n", [], ["pkg.m.f"]),
    py("py-swallowed-by-varargs", "def f(a, b): ...\n", "def f(a, *args, **kwargs): ...\n", [], ["pkg.m.f"]),
    py("py-removed-override-on-subclass",
       "class Base:\n    def f(self): ...\nclass Child(Base):\n    def f(self): ...\n",
       "class Base:\n    def f(self): ...\nclass Child(Base):\n    pass\n", [], ["pkg.m.Child.f"]),
    py("py-basemodel-new-required-field",
       "from pydantic import BaseModel\nclass M(BaseModel):\n    a: int\n",
       "from pydantic import BaseModel\nclass M(BaseModel):\n    a: int\n    b: int\n", [], ["pkg.m.M.__init__"]),
    py("py-typeddict-total-false-new-key",
       "from typing import TypedDict\nclass T(TypedDict, total=False):\n    a: int\n",
       "from typing import TypedDict\nclass T(TypedDict, total=False):\n    a: int\n    b: int\n", [], ["pkg.m.T.__init__"]),
    py("py-typeddict-required-key-added",
       "from typing import TypedDict\nclass T(TypedDict):\n    a: int\n",
       "from typing import TypedDict\nclass T(TypedDict):\n    a: int\n    b: int\n", ["pkg.m.T.__init__"]),
    ts("ts-remove-export", "export declare function f(a: number): void;\nexport declare function g(): void;\n",
       "export declare function g(): void;\n", ["f"], ["g"]),
    ts("ts-new-required-param", "export declare function f(a: number): void;\n",
       "export declare function f(a: number, b: string): void;\n", ["f"]),
    ts("ts-new-optional-param", "export declare function f(a: number): void;\n",
       "export declare function f(a: number, b?: string): void;\n", [], ["f"]),
    ts("ts-method-removed", "export declare class A {\n  run(x: number): void;\n  stop(): void;\n}\n",
       "export declare class A {\n  stop(): void;\n}\n", ["A.run"], ["A", "A.stop"]),
    ts("ts-param-rename-is-not-breaking", "export declare function f(alpha: number): void;\n",
       "export declare function f(beta: number): void;\n", [], ["f"]),
]


def predict(case: dict) -> set[str]:
    if case["lang"] == "python":
        old = snapshot_python({"pkg/m.py": case["old"]}, "pkg")
        new = snapshot_python({"pkg/m.py": case["new"]}, "pkg")
    else:
        old = snapshot_typescript({"index.d.ts": case["old"]})
        new = snapshot_typescript({"index.d.ts": case["new"]})
    return set(breaking_symbols(diff_snapshots(old, new)))


def evaluate() -> dict:
    cases, tp, fp, fn = {}, 0, 0, 0
    for case in CASES:
        predicted = predict(case)
        must, must_not = set(case["must"]), set(case["must_not"])
        hits, false_positives, missed = must & predicted, must_not & predicted, must - predicted
        tp, fp, fn = tp + len(hits), fp + len(false_positives), fn + len(missed)
        cases[case["name"]] = {"hits": sorted(hits), "false_positives": sorted(false_positives), "missed": sorted(missed)}
    return {"precision": tp / (tp + fp) if tp + fp else 1.0, "recall": tp / (tp + fn) if tp + fn else 1.0, "cases": cases}


def main() -> int:
    result = evaluate()
    for name, case in result["cases"].items():
        print(f"{name:34} hits={case['hits']} false_positives={case['false_positives']} missed={case['missed']}")
    print(f"precision={result['precision']:.2f} (min {MIN_PRECISION})  recall={result['recall']:.2f} (min {MIN_RECALL})")
    return 0 if result["precision"] >= MIN_PRECISION and result["recall"] >= MIN_RECALL else 1


if __name__ == "__main__":
    sys.exit(main())
