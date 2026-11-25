"""Human review of the drafted relevance labels.

    python -m evals.ask.labels export                    writes data/ask/review.json (archive text: stays outside git)
    open evals/ask/review.html, review, export reviewed.json
    python -m evals.ask.labels import reviewed.json      writes the grades back and marks confirmed questions reviewed"""
import json
import sys

from evals.ask import data, retrieval_eval

REVIEW = data.DB.parent / "ask" / "review.json"


def export(conn, rows: list[dict]) -> dict:
    questions = []
    for r in rows:
        if not r.get("judgments"):
            continue
        candidates = []
        for j in sorted(r["judgments"], key=lambda j: (-j["grade"], j["id"])):
            item = conn.execute("SELECT title, body, source FROM radar_items WHERE id=?", (j["id"],)).fetchone()
            candidates.append({"id": j["id"], "grade": j["grade"], "title": item["title"], "source": item["source"],
                               "text": item["body"][:600]})
        questions.append({"id": r["id"], "kind": r["kind"], "split": r["split"], "question": r["question"], "candidates": candidates})
    return {"type": "ask-relevance-review", "questions": questions}


def merge(rows: list[dict], reviewed: dict) -> list[dict]:
    """Only confirmed questions change; a question the reviewer never confirmed keeps its drafted labels and reviewed=false."""
    if reviewed.get("type") != "ask-relevance-reviewed":
        raise ValueError("not a reviewed.json file")
    done = {q["id"]: {g["id"]: g["grade"] for g in q["grades"]} for q in reviewed["questions"] if q["confirmed"]}
    out = []
    for r in rows:
        if r["id"] in done:
            grades = done[r["id"]]
            if any(g not in (0, 1, 2) for g in grades.values()):
                raise ValueError(f"{r['id']}: a grade must be 0, 1 or 2")
            r = {**r, "reviewed": True, "judgments": [{**j, "grade": grades.get(j["id"], j["grade"])} for j in r["judgments"]]}
            r["kind"] = "answerable" if any(j["grade"] == 2 for j in r["judgments"]) else "refuse"
        out.append(r)
    return out


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["export"]:
        REVIEW.parent.mkdir(parents=True, exist_ok=True)
        REVIEW.write_text(json.dumps(export(data.open_snapshot(), data.golden()), ensure_ascii=False), encoding="utf-8")
        print(f"wrote {REVIEW}")
        return 0
    if args[:1] == ["import"] and len(args) == 2:
        with open(args[1], encoding="utf-8") as fh:
            rows = merge(data.golden(), json.load(fh))
        data.write_jsonl(data.GOLDEN, rows)
        print(f"{sum(r['reviewed'] for r in rows)} of {len(rows)} questions reviewed; answerable now: "
              f"{len(retrieval_eval.answerable(rows))}. Rerun the evaluations.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
