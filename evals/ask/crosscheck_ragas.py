"""Optional cross-check of the recorded answers with RAGAS. Not part of CI; needs requirements-eval.txt in its own environment.

    python -m evals.ask.crosscheck_ragas [--model llama3.2:3b] [--limit 8] [--ids-only]

Reads the same recording the main evaluation reads. The id-based context precision and recall use no model: they must equal the
precision and recall computed here by hand (a check on both). Faithfulness uses a local Ollama model as judge. Context recall by
model is left out: it needs a written reference answer for each question, and there is none."""
import os

os.environ["RAGAS_DO_NOT_TRACK"] = "true"             # before ragas is imported

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402

from evals.ask import answer_eval, data, metrics, retrieval_eval  # noqa: E402


FILES = answer_eval.Files()


def id_metrics(rows: list[dict], records: dict[str, dict]) -> dict:
    from ragas import SingleTurnSample
    from ragas.metrics import IDBasedContextPrecision, IDBasedContextRecall
    ours, theirs = [], []
    for row in retrieval_eval.answerable(rows):
        got = [int(c["id"][1:]) for c in records[row["id"]]["context"]] if row["id"] in records else []
        want = [i for i, g in retrieval_eval.grades_of(row).items() if g >= 1]
        if not got:
            continue
        sample = SingleTurnSample(user_input=row["question"], retrieved_context_ids=got, reference_context_ids=want)
        theirs.append((IDBasedContextPrecision().single_turn_score(sample), IDBasedContextRecall().single_turn_score(sample)))
        ours.append((len(set(got) & set(want)) / len(got), metrics.recall_at(got, set(want), len(got))))
    gap = max(abs(a - b) for x, y in zip(ours, theirs) for a, b in zip(x, y))
    return {"n": len(ours), "ragas context precision (ids)": metrics.mean([t[0] for t in theirs]),
            "ragas context recall (ids)": metrics.mean([t[1] for t in theirs]), "largest gap to our own numbers": gap}


async def faithfulness(records: list[dict], model: str) -> list[dict]:
    import instructor
    from openai import AsyncOpenAI
    from ragas.llms import llm_factory
    from ragas.metrics.collections import Faithfulness
    client = AsyncOpenAI(base_url=f"{answer_eval.OLLAMA}/v1", api_key="local", timeout=900, max_retries=0)
    # schema-constrained decoding: with plain JSON mode the 3B model repeats the schema instead of filling it in (4 of 4 failed)
    metric = Faithfulness(llm=llm_factory(model, client=client, mode=instructor.Mode.JSON_SCHEMA, temperature=0.0, max_tokens=2048))
    out = []
    for r in records:
        t = answer_eval.triple(r)
        try:
            value = (await metric.ascore(user_input=t["question"], response=t["answer"],
                                         retrieved_contexts=[c["text"] for c in t["context"]])).value
            out.append({"id": r["id"], "faithfulness": None if value != value else value})
        except Exception as exc:                       # a small local model often cannot produce the structure: count it
            out.append({"id": r["id"], "faithfulness": None, "error": f"{type(exc).__name__}: {exc}"[:160]})
        print(out[-1], flush=True)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="llama3.2:3b")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--ids-only", action="store_true")
    args = parser.parse_args(argv)
    records = data.read_jsonl(FILES.full if FILES.full.exists() else FILES.recorded)
    result = {"ids": id_metrics(data.golden(), {r["id"]: r for r in records})}
    if not args.ids_only:
        chosen = [r for r in answer_eval.answered(records) if r["kind"] == "answerable"][:args.limit]
        scores = asyncio.run(faithfulness(chosen, args.model))
        done = [s["faithfulness"] for s in scores if s["faithfulness"] is not None]
        result["faithfulness"] = {"model": args.model, "asked": len(scores), "scored": len(done),
                                  "mean": metrics.mean(done) if done else None, "per_answer": scores}
    print(json.dumps(result, indent=1))
    (answer_eval.PRIVATE / "crosscheck_ragas.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
