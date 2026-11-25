"""Retrieval quality of Ask Radar on the question set, calling radar.ask.retrieve itself.

    python -m evals.ask.retrieval_eval --record-vectors     (once, needs local Ollama with nomic-embed-text)
    python -m evals.ask.retrieval_eval [--fast]             (offline, needs ASK_EVAL_SNAPSHOT; --fast needs numpy, see fast.py)

Variants. keyword: embedder=None. hybrid: the shipped path. vector: the shipped path with fts_query returning None, so the
keyword list is empty and the fused ranking is the vector ranking. CONTEXT_ITEMS is raised from 6 to 10 only to see ranks 7-10;
fusion and order are untouched. The *_full variants run on a copy of the database in which every item has a vector."""
import argparse
import base64
import json
import os
import sys
from contextlib import contextmanager, nullcontext

from evals.ask import data, metrics
from radar import ask
from radar.embed import OllamaEmbedder, cosine, pack, unpack

VECTORS = data.HERE / "recorded" / "query_vectors.jsonl"
RESULTS = data.HERE / "recorded" / "retrieval.json"
VARIANTS = ("keyword", "vector", "hybrid")
K = ask.CONTEXT_ITEMS
DEPTH = 10
OFF_TOPIC = ("How do I bake sourdough bread?", "What is the weather in Paris today?", "Who won the 1998 football world cup?",
             "asdf qwer zxcv", "Best hiking shoes for wide feet")
METRICS = ("hit@6", "recall@6", "recall@10", "precision@6", "mrr@10", "ndcg@6", "ndcg@10", "judged@6")


def query_text(question: str) -> str:
    return "search_query: " + question


def record_vectors(questions: list[str], path=VECTORS) -> None:
    embedder = OllamaEmbedder(base_url=os.environ.get("OLLAMA_URL", "http://localhost:11434"), timeout_s=300)
    rows = []
    for text in map(query_text, questions):
        vector = embedder.embed([text])[0]
        rows.append({"text": text, "vector": base64.b64encode(pack(vector)).decode()})
    data.write_jsonl(path, rows)


def recorded_embedder(path=VECTORS) -> data.RecordedEmbedder:
    return data.RecordedEmbedder({r["text"]: unpack(base64.b64decode(r["vector"])) for r in data.read_jsonl(path)})


@contextmanager
def patched(**names):
    """Temporarily replace module-level names of radar.ask."""
    old = {n: getattr(ask, n) for n in names}
    try:
        for n, value in names.items():
            setattr(ask, n, value)
        yield
    finally:
        for n, value in old.items():
            setattr(ask, n, value)


def run(conn, question: str, variant: str, embedder, depth: int = DEPTH, window: int | None = None) -> dict:
    """{"ids": ranked item ids, "confident": bool} from the real retrieve()."""
    extra = {"fts_query": lambda q: None} if variant == "vector" else {}
    with patched(CONTEXT_ITEMS=depth, VECTOR_WINDOW=window or ask.VECTOR_WINDOW, **extra):
        found = ask.retrieve(conn, question, None if variant == "keyword" else embedder)
    return {"ids": [i["id"] for i in found["items"]], "confident": found["confident"]}


def grades_of(row: dict) -> dict[int, int]:
    return {j["id"]: j["grade"] for j in row["judgments"]}


def score(ids: list[int], grades: dict[int, int]) -> dict:
    """hit and mrr need an item that answers the question (grade 2); recall and precision also count partly useful ones."""
    relevant = {i for i, g in grades.items() if g >= 1}
    answering = {i for i, g in grades.items() if g >= 2}
    return {"hit@6": metrics.hit_at(ids, answering, K), "recall@6": metrics.recall_at(ids, relevant, K),
            "recall@10": metrics.recall_at(ids, relevant, DEPTH), "precision@6": metrics.precision_at(ids, relevant, K),
            "mrr@10": metrics.reciprocal_rank(ids, answering, DEPTH), "ndcg@6": metrics.ndcg_at(ids, grades, K),
            "ndcg@10": metrics.ndcg_at(ids, grades, DEPTH),
            "judged@6": sum(1 for i in ids[:K] if i in grades) / max(1, len(ids[:K]))}


def answerable(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["kind"] == "answerable"]


def evaluate(conn, rows: list[dict], embedder, variants=VARIANTS, window=None) -> dict:
    """Scores for answerable questions and the confident flag for every open question, per variant."""
    out = {}
    for variant in variants:
        runs = {r["id"]: run(conn, r["question"], variant, embedder, window=window)
                for r in rows if r["kind"] in ("answerable", "refuse")}
        out[variant] = {"scores": {r["id"]: score(runs[r["id"]]["ids"], grades_of(r)) for r in answerable(rows)},
                        "ids": {i: x["ids"] for i, x in runs.items()},
                        "confident": {i: x["confident"] for i, x in runs.items()}}
    return out


def restrict(conn, rows: list[dict], embedder, embedded: set[int]) -> dict:
    """The same runs judged on embedded items only: other items are struck from the rankings and from the labels.
    (Rankings are read 100 deep first, so striking items does not shorten the top ten.)"""
    out = {}
    for variant in VARIANTS:
        out[variant] = {"scores": {}, "confident": {}}
        for r in answerable(rows):
            grades = {i: g for i, g in grades_of(r).items() if i in embedded}
            if 2 in grades.values():
                found = run(conn, r["question"], variant, embedder, depth=100)
                out[variant]["scores"][r["id"]] = score([i for i in found["ids"] if i in embedded], grades)
                out[variant]["confident"][r["id"]] = found["confident"]
    return out


def refusal(rows: list[dict], confident: dict[str, bool]) -> dict:
    """false answer: a must-refuse question passed the confidence gate. false refusal: an answerable one was stopped by it."""
    return {"false_answer": metrics.rate([confident[r["id"]] for r in rows if r["kind"] == "refuse"]),
            "false_refusal": metrics.rate([not confident[r["id"]] for r in rows if r["kind"] == "answerable"])}


def report(result: dict, rows: list[dict]) -> dict:
    ids = [r["id"] for r in answerable(rows)]
    out = {"n_answerable": len(ids), "variants": {}, "paired": {}}
    for variant, r in result.items():
        out["variants"][variant] = {m: [round(x, 3) for x in metrics.bootstrap([r["scores"][i][m] for i in ids])] for m in METRICS}
        out["variants"][variant]["refusal"] = refusal(rows, r["confident"])
    for other in result:
        if other != "hybrid":
            out["paired"][f"hybrid - {other}"] = {
                m: {k: round(v, 3) if isinstance(v, float) else v for k, v in metrics.paired_difference(
                    [result["hybrid"]["scores"][i][m] for i in ids], [result[other]["scores"][i][m] for i in ids]).items()}
                for m in ("hit@6", "recall@6", "mrr@10", "ndcg@6")}
    return out


def auc(positives: list[float], negatives: list[float]) -> float:
    """Chance that a random positive scores above a random negative: 0.5 is useless, 1.0 separates perfectly."""
    return sum((p > n) + 0.5 * (p == n) for p in positives for n in negatives) / (len(positives) * len(negatives))


def gate_study(conn, rows: list[dict], embedder) -> dict:
    """How well the signal behind the confidence gate (best cosine) separates answerable from must-refuse questions, and whether
    the gate stops plainly off-topic ones."""
    vectors = [unpack(r["vector"]) for r in conn.execute("SELECT vector FROM radar_embeddings")]

    def best(question):
        q = embedder.embed([query_text(question)])[0]
        return round(max(cosine(q, v) for v in vectors), 3)
    got = {kind: {r["id"]: best(r["question"]) for r in rows if r["kind"] == kind} for kind in ("answerable", "refuse")}
    off = {q: {"best": best(q), "passes the gate": run(conn, q, "hybrid", embedder)["confident"]} for q in OFF_TOPIC}
    return {"gate threshold": ask.CONFIDENT_COSINE, "best cosine": got, "off topic": off,
            "auc": round(auc(list(got["answerable"].values()), list(got["refuse"].values())), 3)}


def show(title: str, rep: dict) -> None:
    print(f"\n== {title} (n={rep['n_answerable']})")
    for variant, r in rep["variants"].items():
        print(f"{variant:41}" + "  ".join(f"{m} {r[m][0]:.2f} [{r[m][1]:.2f},{r[m][2]:.2f}]" for m in METRICS))
        print(f"{'':41}" + "  ".join(f"{name} {x['count']}/{x['n']} [{x['low']:.2f},{x['high']:.2f}]"
                                     for name, x in r["refusal"].items() if x["n"]))
    for name, diffs in rep["paired"].items():
        print(f"{name}: " + "; ".join(f"{m} {d['diff']:+.2f} [{d['low']:+.2f},{d['high']:+.2f}]{' significant' if d['significant'] else ' n.s.'}"
                                      for m, d in diffs.items()))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--record-vectors", action="store_true")
    parser.add_argument("--fast", action="store_true")
    args = parser.parse_args(argv)
    speed = nullcontext
    if args.fast:
        from evals.ask.fast import fast_cosine as speed
    rows = data.golden()
    if args.record_vectors:
        record_vectors([r["question"] for r in rows] + list(OFF_TOPIC))
    embedder = recorded_embedder()
    conn = data.open_snapshot()
    embedded = {r[0] for r in conn.execute("SELECT item_id FROM radar_embeddings")}
    with speed(conn):
        result = evaluate(conn, rows, embedder)
        restricted = restrict(conn, rows, embedder, embedded)
    full = data.full_coverage_db()
    if full.exists():
        conn_full = data.connect(full)
        with speed(conn_full):
            wide = evaluate(conn_full, rows, embedder, ("vector", "hybrid"), window=10 ** 9)
            result["vector, all embedded"], result["hybrid, all embedded"] = wide["vector"], wide["hybrid"]
            result["hybrid, all embedded, newest 5000 only"] = evaluate(conn_full, rows, embedder, ("hybrid",))["hybrid"]
    else:
        print("no full-coverage database (python -m evals.ask.data builds it): those rows are left out")
    has_vector = {r["id"] for r in answerable(rows) if any(g >= 2 and i in embedded for i, g in grades_of(r).items())}
    slices = {"all": rows, "dev": data.golden(split="dev"), "test": data.golden(split="test"),
              "an answering item has a vector": [r for r in answerable(rows) if r["id"] in has_vector],
              "no answering item has a vector": [r for r in answerable(rows) if r["id"] not in has_vector],
              "low word overlap with the answer": [r for r in answerable(rows) if r["overlap"] < 0.4],
              "higher word overlap": [r for r in answerable(rows) if r["overlap"] >= 0.4]}
    reports = {name: report(result, part) for name, part in slices.items() if answerable(part)}
    reports["only embedded items count"] = report(restricted, [r for r in rows if r["id"] in restricted["hybrid"]["scores"]])
    for name, rep in reports.items():
        show(name, rep)
    gate = gate_study(conn, rows, embedder)
    print("\n== confidence gate", json.dumps({k: v for k, v in gate.items() if k != "best cosine"}, indent=1))
    per_question = {v: {"ids": r["ids"], "confident": r["confident"],
                        "scores": {i: {m: round(x, 4) for m, x in s.items()} for i, s in r["scores"].items()}}
                    for v, r in result.items()}
    RESULTS.write_text(json.dumps({"reports": reports, "gate": gate, "per_question": per_question}, indent=1) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
