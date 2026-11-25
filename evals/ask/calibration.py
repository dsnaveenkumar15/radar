"""How far can the groundedness judge be trusted? Compare it with a human who cannot see its verdicts.

    python -m evals.ask.calibration sample               writes data/ask/blind.json (shown) and data/ask/blind_key.json (hidden)
    open evals/ask/label.html, label, export labels.json to data/ask/labels.json
    python -m evals.ask.calibration score [--labels data/ask/labels.json]

The blind file holds only question, items and answer, in shuffled order under neutral ids. Two labels are compared: supported,
and not supported (partly counts as not supported, unsure is dropped)."""
import argparse
import hashlib
import json
import random
import sys

from evals.ask import answer_eval, data, metrics

BLIND = answer_eval.PRIVATE / "blind.json"
KEY = answer_eval.PRIVATE / "blind_key.json"
LABELS = answer_eval.PRIVATE / "labels.json"
SIZE = 40


def binary(label: str) -> str | None:
    return {"supported": "supported", "partly": "unsupported", "unsupported": "unsupported"}.get(label)


def both_runs(model: str, part: str) -> list[dict]:
    """Records or verdicts of the snapshot run and of the what-if run (every item embedded), the latter under full-... ids. The
    second run only adds more real answers to calibrate on."""
    out = []
    for archive, prefix in (("snapshot", ""), ("full", "full-")):
        path = getattr(answer_eval.Files(model, archive), part)
        if path.exists():
            out += [{**r, "id": prefix + r["id"]} for r in data.read_jsonl(path)]
    return out


def distinct(records: list[dict]) -> list[dict]:
    """Drop a record whose question and shown sentences were already seen: both runs often agree."""
    seen, out = set(), []
    for r in records:
        mark = json.dumps([r["question"], r["sentences"]])
        if mark not in seen:
            seen.add(mark)
            out.append(r)
    return out


def sample(records: list[dict], size: int = SIZE, seed: int = metrics.SEED) -> tuple[dict, list[dict]]:
    """Every real answer first (they are few), then swapped controls to fill up, so unsupported cases are present.
    Returns (blind file, hidden key). The key keeps the stratum; verdicts stay in their own file."""
    rng = random.Random(seed)
    records = distinct(answer_eval.answered(records))
    real = [{**answer_eval.triple(r), "stratum": r["kind"]} for r in records]
    first_run = [r for r in records if not r["id"].startswith("full-")]        # the judge saw exactly these swaps
    swaps = [{**t, "stratum": "swap"} for t in answer_eval.controls(first_run) if t["id"].startswith("swap-")]
    rng.shuffle(swaps)
    rng.shuffle(real)
    keep = min(len(real), size - min(len(swaps), size // 5))         # at least a fifth are known negatives
    chosen = real[:keep] + swaps[:size - keep]
    rng.shuffle(chosen)
    blind, key = [], []
    for n, t in enumerate(chosen):
        public = f"t{n + 1:02d}"
        blind.append({"id": public, "question": t["question"], "context": t["context"], "answer": t["answer"]})
        key.append({"id": public, "record": t["id"], "stratum": t["stratum"]})
    # the set id keeps the page from applying labels saved for an earlier sample
    return {"type": "ask-blind-labels", "set": hashlib.sha256(json.dumps(blind).encode()).hexdigest()[:12], "triples": blind}, key


def score(pairs: list[tuple[str, str]]) -> dict:
    """pairs of (human, judge), both 'supported' or 'unsupported'. 'unsupported' is the class that matters."""
    tp = sum(h == j == "unsupported" for h, j in pairs)
    flagged = sum(j == "unsupported" for _, j in pairs)
    bad = sum(h == "unsupported" for h, _ in pairs)
    kappa, low, high = metrics.bootstrap(pairs, metrics.cohen_kappa)
    return {"n": len(pairs), "agreement": metrics.rate([h == j for h, j in pairs]),
            "precision of unsupported": metrics.rate([True] * tp + [False] * (flagged - tp)),
            "recall of unsupported": metrics.rate([True] * tp + [False] * (bad - tp)),
            "kappa": [round(kappa, 3), round(low, 3), round(high, 3)]}


def compare(key: list[dict], labels: list[dict], verdicts: list[dict], judge_name: str) -> dict:
    human = {x["id"]: binary(x["label"]) for x in labels}
    machine = {v["id"]: binary(v["verdict"]) for v in verdicts if v["judge"] == judge_name}
    rows = [(k["stratum"], human[k["id"]], machine[k["record"]]) for k in key if human.get(k["id"]) and machine.get(k["record"])]
    return {"judge": judge_name, "labelled": len(labels), "compared": len(rows), "all": score([(h, j) for _, h, j in rows]),
            "real answers only": score([(h, j) for s, h, j in rows if s != "swap"])}


def load_labels(path) -> list[dict]:
    """The exported labels, refused if they belong to another sample than the blind file on disk."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        raise SystemExit(f"no labels yet at {path}") from None
    if doc.get("set") != json.loads(BLIND.read_text(encoding="utf-8"))["set"]:
        raise SystemExit("these labels belong to a different sample")
    return doc["labels"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("sample", "score"))
    parser.add_argument("--labels", default=str(LABELS))
    parser.add_argument("--model", default=answer_eval.MODEL, help="the answering model whose recording is sampled or scored")
    args = parser.parse_args(argv)
    if args.command == "sample":
        blind, key = sample(both_runs(args.model, "full"))
        BLIND.write_text(json.dumps(blind, ensure_ascii=False), encoding="utf-8")
        KEY.write_text(json.dumps(key, indent=1), encoding="utf-8")
        print(f"wrote {BLIND} ({len(blind['triples'])} triples) and {KEY}")
        return 0
    labels = load_labels(args.labels)
    key = json.loads(KEY.read_text(encoding="utf-8"))
    verdicts = both_runs(args.model, "verdicts_full")
    for name in sorted({v["judge"] for v in verdicts}):
        print(json.dumps(compare(key, labels, verdicts, name), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
