import hashlib
import math
import re


class FakeEmbedder:
    """Deterministic bag-of-words embedder for tests: texts sharing words get similar vectors."""
    name = "fake-embed"

    def __init__(self, dim: int = 64):
        self.dim, self.calls = dim, []

    def embed(self, texts):
        self.calls.append(list(texts))
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for token in re.findall(r"[a-z0-9]{3,}", text.lower().replace("search_document:", "").replace("search_query:", "")):
                vec[int(hashlib.md5(token.encode()).hexdigest(), 16) % self.dim] += 1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out
