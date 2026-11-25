"""Write the human-labelled answers as a groundedness test set, one JSON object a line:

    {"id", "question", "context": [{"id", "text"}], "answer", "label": "supported" | "unsupported", "source", "split"}

    python -m evals.ask.export_groundedness [--labels data/ask/labels.json] [--out data/ask/groundedness.jsonl]

partly becomes unsupported, unsure is dropped. split is always "test": the set is for measuring, never for training. The output
holds archive text, so it stays in data/, outside git."""
import argparse
import json
import sys

from evals.ask import answer_eval, calibration, data

OUT = answer_eval.PRIVATE / "groundedness.jsonl"


def records(blind: dict, key: list[dict], labels: list[dict]) -> list[dict]:
    triples = {t["id"]: t for t in blind["triples"]}
    stratum = {k["id"]: k["stratum"] for k in key}
    out = []
    for item in labels:
        label = calibration.binary(item["label"])
        if label:
            t = triples[item["id"]]
            source = "radar-ask-swap" if stratum[item["id"]] == "swap" else "radar-ask"
            out.append({"id": t["id"], "question": t["question"], "context": t["context"], "answer": t["answer"], "label": label,
                        "source": source, "split": "test"})
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--labels", default=str(calibration.LABELS))
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args(argv)
    labels = calibration.load_labels(args.labels)
    rows = records(json.loads(calibration.BLIND.read_text(encoding="utf-8")), json.loads(calibration.KEY.read_text(encoding="utf-8")), labels)
    data.write_jsonl(args.out, rows)
    print(f"wrote {len(rows)} records to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
