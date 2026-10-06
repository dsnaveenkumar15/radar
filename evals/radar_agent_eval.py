# evals/radar_agent_eval.py
"""Quality gate for the upgrade agent. Offline (CI): a scripted model answers per candidate from script.json, so the
score measures grounding, indexing and verification. Live (--live, a few cents): real models through OpenRouter."""
import json
import sys
import time
from pathlib import Path

from langchain_core.messages import AIMessage

from radar.agent.config import load_config
from radar.agent.gateway import Budget, Gateway, chat_factory
from radar.agent.graph import Context, run_graph


class _Scripted:
    """Answers SubmitPlan with the default order, SubmitVerdict from the case script, ProposeChanges with nothing."""

    def __init__(self, script: dict):
        self.script = script

    def bind_tools(self, tools):
        self.names = [t.__name__ for t in tools]
        return self

    def invoke(self, messages):
        text = messages[1].content if len(messages) > 1 else ""
        if "SubmitPlan" in self.names:
            return AIMessage(content="", tool_calls=[])                    # code's risk order is kept
        if "ProposeChanges" in self.names:
            return AIMessage(content="", tool_calls=[{"name": "ProposeChanges", "args": {"summary": "ok", "changes": []}, "id": "p", "type": "tool_call"}])
        for key, ans in self.script.items():
            symbol, where = key.split("@", 1)
            path, line = where.rsplit(":", 1)
            if symbol in text and f"{path}:{line}" in text:
                ev = [{"path": ans.get("path", path), "line": int(ans.get("line", line)), "quote": ans["quote"]}] if ans["verdict"] == "confirmed" else []
                return AIMessage(content="", tool_calls=[{"name": "SubmitVerdict", "id": "v", "type": "tool_call",
                                                          "args": {"verdict": ans["verdict"], "explanation": "scripted", "evidence": ev}}])
        return AIMessage(content="", tool_calls=[{"name": "SubmitVerdict", "id": "v", "type": "tool_call",
                                                  "args": {"verdict": "dismissed", "explanation": "not in script", "evidence": []}}])


def _files(case: Path) -> dict:
    root = case / "files"
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file()}


def evaluate(cases_dir, live: bool = False) -> dict:
    tp = fp = fn = 0
    usd, started, count, failures = 0.0, time.monotonic(), 0, 0
    cfg = load_config()
    for case in sorted(Path(cases_dir).iterdir()):
        if not case.is_dir():
            continue
        count += 1
        files = _files(case)
        api = json.loads((case / "api_changes.json").read_text(encoding="utf-8"))
        truth = {tuple(x) for x in json.loads((case / "truth.json").read_text(encoding="utf-8"))["breaks"]}
        script = json.loads((case / "script.json").read_text(encoding="utf-8"))
        factory = chat_factory(cfg) if live else (lambda name, s=script: _Scripted(s))
        spent = []
        gateway = Gateway({"fast": cfg.fast_models or ["scripted"], "strong": cfg.strong_models or ["scripted"]}, factory,
                          cfg.prices, Budget(0.05, 60_000, 30, time.monotonic() + 120),
                          on_usage=lambda m, p, u, t: spent.append(u), day_guard=lambda: None)
        roots = {"pypi": {"x": "pypi:x"}, "npm": {"xpkg": "npm:xpkg"}}
        ctx = Context(load_input=lambda f=files: {"mode": "repo", "repo": case.name, "sha": None, "files": f, "skipped": [], "truncated": False},
                      api_changes=api, upgrade={"packages": {}}, roots=roots, gateway=gateway, emit=lambda t, p: None)
        state = run_graph(ctx, case.name)
        found = {(f["evidence"][0]["path"], f["evidence"][0]["line"]) for f in state.get("verified") or [] if f["verified"]}
        tp += len(found & truth); fp += len(found - truth); fn += len(truth - found)
        usd += sum(spent)
        failures += gateway.failures
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "cases": count, "usd": round(usd, 4), "model_failures": failures,
            "seconds": round(time.monotonic() - started, 1)}


if __name__ == "__main__":
    here = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "radar_agent"
    print(json.dumps(evaluate(here, live="--live" in sys.argv), indent=2))
