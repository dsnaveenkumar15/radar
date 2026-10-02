import json

import httpx
import pytest

from radar import embed, store
from radar.embed import EmbedError, OllamaEmbedder, cosine, embed_items, load_vectors, pack, unpack
from tests.radar_fakes import FakeEmbedder


@pytest.fixture
def db(conn):
    store.ensure_schema(conn)
    return conn


def add(db, n):
    ids = [store.add_item(db, {"source": "hn", "tier": "community", "kind": "discussion", "url": f"https://x.test/{i}",
                               "title": f"agent framework number {i}", "published": 1000.0 + i}, now=1.0) for i in range(n)]
    return [dict(id=i, title=f"agent framework number {k}", body="") for k, i in enumerate(ids)]


def test_pack_roundtrip_and_cosine():
    assert unpack(pack([0.5, -1.25, 3.0])) == pytest.approx([0.5, -1.25, 3.0])
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0) and cosine([1, 0], [0, 1]) == 0.0 and cosine([0, 0], [1, 1]) == 0.0


def test_ollama_embedder_posts_model_and_texts():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]})
    embedder = OllamaEmbedder(client=httpx.Client(base_url="http://x", transport=httpx.MockTransport(handler)))
    assert embedder.embed(["a", "b"]) == [[0.1, 0.2], [0.3, 0.4]]
    assert seen == {"model": "nomic-embed-text", "input": ["a", "b"]} and embedder.name == "nomic-embed-text"


@pytest.mark.parametrize("response", [httpx.Response(500), httpx.Response(200, json={}),
                                      httpx.Response(200, json={"embeddings": [[1.0]]}),
                                      httpx.Response(200, content=b"nope")])
def test_bad_answers_are_embed_errors(response):
    embedder = OllamaEmbedder(client=httpx.Client(base_url="http://x", transport=httpx.MockTransport(lambda r: response)))
    with pytest.raises(EmbedError):
        embedder.embed(["a", "b"])


def test_unreachable_backend_is_an_embed_error():
    def boom(request):
        raise httpx.ConnectError("down")
    embedder = OllamaEmbedder(client=httpx.Client(base_url="http://x", transport=httpx.MockTransport(boom)))
    with pytest.raises(EmbedError):
        embedder.embed(["a"])


def test_embed_items_stores_vectors_and_only_embeds_new_items(db):
    items, fake = add(db, 3), FakeEmbedder()
    assert embed_items(db, fake, items) is True
    assert embed_items(db, fake, items) is True and len(fake.calls) == 1            # nothing re-embedded
    vectors = load_vectors(db, [i["id"] for i in items])
    assert set(vectors) == {i["id"] for i in items} and len(vectors[items[0]["id"]]) == 64
    assert db.execute("SELECT model, dim FROM radar_embeddings LIMIT 1").fetchone()["model"] == "fake-embed"


def test_a_failing_batch_returns_false_and_keeps_earlier_vectors(db, monkeypatch):
    monkeypatch.setattr(embed, "BATCH", 1)
    items = add(db, 3)

    class Flaky(FakeEmbedder):
        def embed(self, texts):
            if len(self.calls) == 1:
                raise EmbedError("down")
            return super().embed(texts)
    assert embed_items(db, Flaky(), items) is False
    assert len(load_vectors(db, [i["id"] for i in items])) == 1
    assert load_vectors(db, []) == {}
