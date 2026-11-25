"""The small public-safe slice of the archive that CI evaluates, and the quality gate over it.

    python -m evals.ask.fixture build          (needs ASK_EVAL_SNAPSHOT and the recorded query vectors)
    python -m evals.ask.fixture check          (what CI runs; offline, about two seconds)

Only GitHub-release and arXiv items, at most 600 characters each, with the production vector where the item has one. Vectors are
kept as half floats. The slice is far smaller than the archive and holds only part of the questions, so its numbers are not comparable
with the real ones and only mean "ranking did not get worse"; the real numbers are in recorded/retrieval.json."""
import base64
import json
import struct
import sys

from evals.ask import data, metrics, retrieval_eval
from radar import store
from radar.embed import pack, unpack

DIR = data.HERE.parent.parent / "tests" / "fixtures" / "radar_ask"
ITEMS, QUERIES, THRESHOLDS = DIR / "items.jsonl", DIR / "queries.jsonl", DIR / "thresholds.json"
MAX_CHARS = 600
DISTRACTORS_PER_QUESTION = 5
MARGIN = 0.05                             # one question of about twenty-five is 0.04
GATED = ("hit@6", "recall@6", "mrr@10", "ndcg@6")


def to_half(vector) -> str:
    return base64.b64encode(struct.pack(f"<{len(vector)}e", *vector)).decode()


def from_half(text: str) -> list[float]:
    raw = base64.b64decode(text)
    return list(struct.unpack(f"<{len(raw) // 2}e", raw))


def public_safe(source: str) -> bool:
    return source == "arxiv-ai" or source.startswith("gh-")


def build() -> None:
    conn = data.open_snapshot()
    vectors = retrieval_eval.recorded_embedder().vectors
    wanted, queries = {}, []
    for row in retrieval_eval.answerable(data.golden()):
        judged = []
        for j in row["judgments"]:
            item = conn.execute("SELECT i.id, source, tier, kind, url, title, body, published, vector FROM radar_items i LEFT JOIN "
                                "radar_embeddings e ON e.item_id = i.id WHERE i.id=?", (j["id"],)).fetchone()
            if public_safe(item["source"]):
                judged.append((j["grade"], dict(item)))
        if not any(grade == 2 for grade, _ in judged):
            continue
        keep = [x for x in judged if x[0]] + [x for x in judged if not x[0]][:DISTRACTORS_PER_QUESTION]
        wanted.update({item["id"]: item for _, item in keep})
        text = retrieval_eval.query_text(row["question"])
        queries.append({"id": row["id"], "question": row["question"], "grades": {item["id"]: grade for grade, item in keep if grade},
                        "vector": to_half(vectors[text])})
    items = []
    for item in sorted(wanted.values(), key=lambda i: i["id"]):
        vector = item.pop("vector")
        items.append({**item, "body": item["body"][:MAX_CHARS - len(item["title"])], "vector": to_half(unpack(vector)) if vector else None})
    data.write_jsonl(ITEMS, items)
    data.write_jsonl(QUERIES, queries)
    observed = measure_fresh()
    THRESHOLDS.write_text(json.dumps({"observed": observed, "margin": MARGIN,
                                      "minimum": {m: round(observed[m] - MARGIN, 3) for m in GATED}}, indent=1) + "\n",
                          encoding="utf-8", newline="\n")


def load(conn) -> tuple[list[dict], data.RecordedEmbedder]:
    """Fill an empty database through the app's own insert path; returns (questions, recorded embedder). Item ids are reassigned
    by the database, so the grades are translated."""
    store.ensure_schema(conn)
    new_id = {}
    for item in data.read_jsonl(ITEMS):
        new_id[item["id"]] = store.add_item(conn, item, now=0.0)
        if item["vector"]:
            vector = from_half(item["vector"])
            conn.execute("INSERT INTO radar_embeddings(item_id, model, dim, vector) VALUES (?, 'recorded', ?, ?)",
                         (new_id[item["id"]], len(vector), pack(vector)))
    queries = data.read_jsonl(QUERIES)
    for q in queries:
        q["grades"] = {new_id[int(i)]: grade for i, grade in q["grades"].items()}
    return queries, data.RecordedEmbedder({retrieval_eval.query_text(q["question"]): from_half(q["vector"]) for q in queries})


def measure(conn, queries: list[dict], embedder, variant: str = "hybrid") -> dict[str, float]:
    scores = [retrieval_eval.score(retrieval_eval.run(conn, q["question"], variant, embedder)["ids"], q["grades"]) for q in queries]
    return {m: round(metrics.mean([s[m] for s in scores]), 3) for m in GATED}


def measure_fresh() -> dict[str, float]:
    conn = data.connect(":memory:")
    return measure(conn, *load(conn))


def failures(observed: dict[str, float], minimum: dict[str, float]) -> list[str]:
    return [f"{m} {observed[m]:.3f} is below {minimum[m]:.3f}" for m in minimum if observed[m] < minimum[m]]


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["build"]:
        build()
    elif args != ["check"]:
        print(__doc__)
        return 2
    observed = measure_fresh()
    bad = failures(observed, json.loads(THRESHOLDS.read_text(encoding="utf-8"))["minimum"])
    print(observed, "\n".join(bad) or "gate passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
