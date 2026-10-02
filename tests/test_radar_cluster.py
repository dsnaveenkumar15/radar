import random

from radar.cluster import cluster_items


def item(i, title, published=10_000.0, source="hn", tier="community", url=None, package=None, version=None, kind="discussion"):
    return {"id": i, "title": title, "published": published, "source": source, "tier": tier, "kind": kind,
            "canonical_url": url or f"https://x.test/{i}", "package": package, "version": version}


def groups(clusters):
    return sorted(sorted(i["id"] for i in c.items) for c in clusters)


def test_same_canonical_url_across_sources_is_one_cluster():
    clusters = cluster_items([item(1, "A thing", url="https://x.test/a", source="hn"),
                              item(2, "A thing, reposted", url="https://x.test/a", source="blog", tier="official"),
                              item(3, "Unrelated thing")])
    assert groups(clusters) == [[1, 2], [3]] and clusters[0].id == "c1"
    assert [i["id"] for i in clusters[0].items] == [2, 1]                 # official first


def test_same_package_and_version_clusters_but_different_versions_never_do():
    a = item(1, "x 1.0.0 released", package="pypi:x", version="1.0.0", kind="release", source="gh", tier="official")
    b = item(2, "Blog: what is new in x 1.0.0", package="pypi:x", version="1.0.0")
    c = item(3, "x 1.0.1 released", package="pypi:x", version="1.0.1", kind="release", source="gh", tier="official")
    same_vectors = {1: [1.0, 0.0], 2: [1.0, 0.0], 3: [1.0, 0.0]}
    assert groups(cluster_items([a, b, c], same_vectors)) == [[1, 2], [3]]
    other = item(4, "y 1.0.0 released", package="pypi:y", version="1.0.0", kind="release", source="gh2", tier="official")
    assert groups(cluster_items([a, other], {1: [1.0, 0.0], 4: [1.0, 0.0]})) == [[1], [4]]


def test_embedding_similarity_within_the_time_window_clusters():
    items = [item(1, "alpha", published=0.0), item(2, "beta", published=3600.0), item(3, "gamma", published=3600.0),
             item(4, "delta", published=10 * 86400.0)]
    vectors = {1: [1.0, 0.0], 2: [0.99, 0.1], 3: [0.0, 1.0], 4: [1.0, 0.0]}
    assert groups(cluster_items(items, vectors)) == [[1, 2], [3], [4]]      # 4 is identical but 10 days away


def test_without_vectors_similar_titles_cluster_and_dissimilar_do_not():
    items = [item(1, "Anthropic ships a new agent SDK"), item(2, "Anthropic ships new agent SDK today"),
             item(3, "Quantum chemistry on lattices")]
    assert groups(cluster_items(items)) == [[1, 2], [3]]
    assert groups(cluster_items(items, {1: [1.0, 0.0]})) == [[1, 2], [3]]      # a missing vector falls back to titles


TITLES = ["apple orchard", "banana plantation", "cherry blossom", "damson jam", "elder flower", "fig newton", "grape vine"]


def test_result_does_not_depend_on_input_order():
    items = [item(i, TITLES[i - 1], published=10_000.0 + i, url=f"https://x.test/{i % 3}") for i in range(1, 8)]
    expected = [(c.id, [i["id"] for i in c.items]) for c in cluster_items(items)]
    shuffled = items[:]
    random.Random(1).shuffle(shuffled)
    assert [(c.id, [i["id"] for i in c.items]) for c in cluster_items(shuffled)] == expected
    assert groups(cluster_items(items)) == [[1, 4, 7], [2, 5], [3, 6]]         # shared canonical URL, not titles


def test_the_newest_items_win_when_over_the_cap(monkeypatch):
    from radar import cluster
    monkeypatch.setattr(cluster, "MAX_ITEMS", 2)
    items = [item(1, "old", published=1.0), item(2, "mid", published=2.0), item(3, "new", published=3.0)]
    assert groups(cluster_items(items)) == [[2], [3]]
