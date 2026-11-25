"""Loading for the Ask evaluation: the frozen archive snapshot, the question set and recorded query vectors."""
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path

from core.db import connect
from radar import store
from radar.embed import EmbedError, OllamaEmbedder, embed_items

HERE = Path(__file__).parent
GOLDEN = HERE / "golden.jsonl"
DB = HERE.parent.parent / "data" / "ask_eval.db"          # data/ is gitignored
TABLES = ("radar_sources", "radar_items", "radar_embeddings", "radar_api_diffs", "radar_advisories")
BYTES = {("radar_embeddings", "vector")}


def read_jsonl(path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_jsonl(path, rows) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def snapshot_path() -> Path:
    value = os.environ.get("ASK_EVAL_SNAPSHOT", "")
    if not value:
        raise SystemExit("set ASK_EVAL_SNAPSHOT to the radar.jsonl.gz snapshot file")
    return Path(value)


def load_rows(conn, lines) -> None:
    """Insert {"t": table, "r": row} lines. The app's own schema and triggers build the keyword index."""
    store.ensure_schema(conn)
    by_table: dict[str, list[dict]] = {t: [] for t in TABLES}
    for line in lines:
        rec = json.loads(line) if isinstance(line, (str, bytes)) else line
        row = rec["r"]
        for table, col in BYTES:
            if rec["t"] == table:
                row = {**row, col: base64.b64decode(row[col])}
        by_table[rec["t"]].append(row)
    conn.execute("BEGIN")
    for table in TABLES:                                  # items before embeddings (foreign key)
        for row in by_table[table]:
            cols = ", ".join(row)
            conn.execute(f"INSERT INTO {table}({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
    conn.execute("COMMIT")
    store.ensure_schema(conn)                             # rebuilds the index if the counts disagree


def open_snapshot(path=None, db=None):
    """The snapshot as a SQLite database, built once and reused. The file name carries the snapshot's hash."""
    path = Path(path or snapshot_path())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    db = Path(db or DB.with_name(f"ask_eval_{digest[:12]}.db"))
    fresh = not db.exists()
    conn = connect(db)
    if fresh:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            load_rows(conn, fh)
    return conn


def golden(path=GOLDEN, split: str | None = None) -> list[dict]:
    rows = read_jsonl(path)
    return [r for r in rows if split in (None, r["split"])]


class RecordedEmbedder:
    """Serves query vectors recorded earlier, so a run needs no model. An unknown text is an error, never a silent miss."""
    name = "recorded"

    def __init__(self, vectors: dict[str, list[float]]):
        self.vectors = vectors

    def embed(self, texts):
        missing = [t for t in texts if t not in self.vectors]
        if missing:
            raise KeyError(f"no recorded vector for {missing[0]!r}")
        return [self.vectors[t] for t in texts]


class NoEmbedder:
    """An embedder that is down: retrieve() then runs on keywords only."""
    name = "down"

    def embed(self, texts):
        raise EmbedError("disabled")


def full_coverage_db(path=None) -> Path:
    """A copy of the snapshot database in which every item has a vector (a what-if, not the production state).
    Built by `python -m evals.ask.data`; needs a local Ollama with nomic-embed-text and takes about half an hour."""
    path = Path(path or snapshot_path())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return DB.with_name(f"ask_eval_{digest[:12]}_full.db")


def build_full_coverage() -> None:
    target = full_coverage_db()
    resume = target.exists()
    with open_snapshot() as source, connect(target) as conn:
        if not resume:
            source.backup(conn)
        items = [dict(r) for r in conn.execute("SELECT id, title, body FROM radar_items WHERE id NOT IN "
                                               "(SELECT item_id FROM radar_embeddings) ORDER BY id")]
        embedder = OllamaEmbedder(base_url=os.environ.get("OLLAMA_URL", "http://localhost:11434"), timeout_s=300)
        for start in range(0, len(items), 320):
            if not embed_items(conn, embedder, items[start:start + 320]):
                raise SystemExit("embedding backend failed; run again to resume")
            print(f"{min(start + 320, len(items))}/{len(items)} embedded", flush=True)


if __name__ == "__main__":
    build_full_coverage()
