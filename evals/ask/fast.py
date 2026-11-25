"""Offline speed-up for long evaluation runs: the same radar.ask.retrieve, with only its cosine arithmetic done by numpy.

    python -m evals.ask.fast verify          (compares rankings with the untouched code; needs numpy, see requirements-eval.txt)

retrieve() calls cosine(query, unpack(blob)) once per stored vector. Here unpack returns the blob itself and cosine looks it up in
a table computed in one matrix product, so selection, thresholds, sorting and fusion stay the application's own. The CI gate does
not use this."""
import json
import sys
from contextlib import contextmanager

import numpy as np

from evals.ask import data, retrieval_eval


class Table:
    def __init__(self, conn):
        blobs = [r["vector"] for r in conn.execute("SELECT vector FROM radar_embeddings ORDER BY item_id")]
        self.row = {b: n for n, b in enumerate(blobs)}                # identical vectors share a row, as they share a score
        self.matrix = np.frombuffer(b"".join(blobs), dtype="<f4").reshape(len(blobs), -1).astype("f8")
        self.norm = np.linalg.norm(self.matrix, axis=1)
        self.query, self.scores = None, None

    def cosine(self, query, blob) -> float:
        if query is not self.query:
            q = np.asarray(query, dtype="f8")
            self.query, self.scores = query, (self.matrix @ q / (self.norm * np.linalg.norm(q))).tolist()
        return self.scores[self.row[blob]]


@contextmanager
def fast_cosine(conn):
    table = Table(conn)
    with retrieval_eval.patched(unpack=lambda blob: blob, cosine=table.cosine):
        yield


def verify(conn, questions: list[str], embedder, window=None) -> dict:
    """Same ids, same order, same confident flag, at depth 100."""
    variants = ("hybrid", "vector")
    plain = [retrieval_eval.run(conn, q, v, embedder, depth=100, window=window) for q in questions for v in variants]
    with fast_cosine(conn):
        quick = [retrieval_eval.run(conn, q, v, embedder, depth=100, window=window) for q in questions for v in variants]
    return {"runs": len(plain), "identical": sum(a == b for a, b in zip(plain, quick))}


def main() -> int:
    embedder = retrieval_eval.recorded_embedder()
    rows = [r["question"] for r in data.golden() if r["kind"] in ("answerable", "refuse")]
    result = {"snapshot, every question": verify(data.open_snapshot(), rows, embedder),
              "fully embedded copy, every eighth question": verify(data.connect(data.full_coverage_db()), rows[::8], embedder, window=10 ** 9)}
    print(json.dumps(result, indent=1))
    return 0 if all(r["runs"] == r["identical"] for r in result.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
