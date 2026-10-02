"""Embeddings for clustering and, in M2b, retrieval. Ollama's nomic-embed-text; every use degrades without it."""
import array
import math

import httpx

MODEL = "nomic-embed-text"
BATCH = 32
MAX_CHARS = 1500


class EmbedError(Exception):
    """The embedding backend is unavailable or answered badly; callers fall back to keyword similarity."""


class OllamaEmbedder:
    def __init__(self, base_url: str = "http://localhost:11434", model: str = MODEL, timeout_s: float = 60,
                 client: httpx.Client | None = None):
        self.model = self.name = model
        self.client = client or httpx.Client(base_url=base_url, timeout=timeout_s)

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = self.client.post("/api/embed", json={"model": self.model, "input": texts})
        except httpx.HTTPError as exc:
            raise EmbedError(f"ollama unreachable: {exc}") from exc
        if response.status_code != 200:
            raise EmbedError(f"ollama returned {response.status_code}")
        try:
            vectors = response.json()["embeddings"]
        except (ValueError, KeyError, TypeError) as exc:
            raise EmbedError("ollama returned no embeddings") from exc
        if not isinstance(vectors, list) or len(vectors) != len(texts) \
                or not all(isinstance(v, list) and v for v in vectors):
            raise EmbedError("embedding count mismatch")
        return vectors


def pack(vector) -> bytes:
    return array.array("f", vector).tobytes()


def unpack(blob: bytes) -> list[float]:
    values = array.array("f")
    values.frombytes(blob)
    return values.tolist()


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def item_text(item: dict) -> str:
    return f"search_document: {item['title']}\n{item['body'][:500]}"[:MAX_CHARS]


def embed_items(conn, embedder, items: list[dict]) -> bool:
    todo = [i for i in items
            if conn.execute("SELECT 1 FROM radar_embeddings WHERE item_id=?", (i["id"],)).fetchone() is None]
    try:
        for start in range(0, len(todo), BATCH):
            chunk = todo[start:start + BATCH]
            for item, vector in zip(chunk, embedder.embed([item_text(i) for i in chunk])):
                conn.execute("INSERT OR REPLACE INTO radar_embeddings(item_id, model, dim, vector) VALUES (?, ?, ?, ?)",
                             (item["id"], embedder.name, len(vector), pack(vector)))
    except EmbedError:
        return False
    return True


def load_vectors(conn, ids: list[int]) -> dict[int, list[float]]:
    out: dict[int, list[float]] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        rows = conn.execute(f"SELECT item_id, vector FROM radar_embeddings WHERE item_id IN ({','.join('?' * len(chunk))})",
                            chunk).fetchall()
        out.update({r["item_id"]: unpack(r["vector"]) for r in rows})
    return out
