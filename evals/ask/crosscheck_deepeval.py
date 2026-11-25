"""Optional cross-check of the recorded answers with DeepEval. Not part of CI; needs requirements-eval.txt in its own environment.

    python -m evals.ask.crosscheck_deepeval [--model llama3.2:3b] [--limit 8]

Reads the same recording. Faithfulness with a local Ollama judge, nothing is sent anywhere: telemetry is off and there is no login."""
import os

os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "1"        # before deepeval is imported
os.environ["DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE"] = "900"   # a CPU-only judge is slow
os.environ["DEEPEVAL_PER_TASK_TIMEOUT_SECONDS_OVERRIDE"] = "1800"

import argparse  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402

from evals.ask import answer_eval, data, metrics  # noqa: E402


FILES = answer_eval.Files()


def main(argv=None) -> int:
    from deepeval.metrics import FaithfulnessMetric
    from deepeval.models import OllamaModel
    from deepeval.test_case import LLMTestCase
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="llama3.2:3b")
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args(argv)
    # without a length limit the 3B model ran past 4,000 tokens on one answer
    model = OllamaModel(model=args.model, base_url=answer_eval.OLLAMA, temperature=0, generation_kwargs={"num_predict": 1024})
    scores = []
    for r in [r for r in answer_eval.answered(data.read_jsonl(FILES.full)) if r["kind"] == "answerable"][:args.limit]:
        t = answer_eval.triple(r)
        metric = FaithfulnessMetric(model=model, async_mode=False, include_reason=False)
        try:
            metric.measure(LLMTestCase(input=t["question"], actual_output=t["answer"], retrieval_context=[c["text"] for c in t["context"]]),
                           _show_indicator=False)
            scores.append({"id": r["id"], "faithfulness": metric.score})
        except Exception as exc:                       # a small local model often cannot produce the structure: count it
            scores.append({"id": r["id"], "faithfulness": None, "error": f"{type(exc).__name__}: {exc}"[:160]})
        print(scores[-1], flush=True)
    done = [s["faithfulness"] for s in scores if s["faithfulness"] is not None]
    result = {"model": args.model, "asked": len(scores), "scored": len(done), "mean": metrics.mean(done) if done else None,
              "per_answer": scores}
    print(json.dumps(result, indent=1))
    (answer_eval.PRIVATE / "crosscheck_deepeval.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
