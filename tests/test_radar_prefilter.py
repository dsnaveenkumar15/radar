from radar.config import Entity, Registry
from radar.prefilter import ANCHORS, relevance, select_clusters

REGISTRY = Registry([Entity("langgraph", "LangGraph", "framework", (), True)])
EMPTY = {"api_breaking": [], "api_deprecations": [], "advisories": [], "packages": [], "versions": []}


def entry(cid, title, score, tier="community", kind="discussion", facts=None, relevance_=0.5, prerelease=False, package=None):
    return {"id": cid, "facts": facts or EMPTY, "relevance": relevance_, "signal": {"score": score},
            "items": [{"id": int(cid[1:]), "title": title, "body": "", "tier": tier, "kind": kind, "package": package,
                       "extra": {"prerelease": prerelease}}]}


def test_relevance_uses_keywords_entities_and_anchor_similarity():
    def items(title):
        return [{"id": 1, "title": title, "body": ""}]
    assert relevance(items("Quantum chemistry on lattices"), REGISTRY) == 0.0
    assert relevance(items("A tiny LLM agent loop"), REGISTRY) == 0.5
    assert relevance(items("LangGraph agents"), REGISTRY) == 1.0
    assert relevance(items("Quantum chemistry"), REGISTRY, {1: [1.0, 0.0]}, [[1.0, 0.0]]) == 0.5
    assert relevance(items("Quantum chemistry"), REGISTRY, {1: [0.0, 1.0]}, [[1.0, 0.0]]) == 0.0
    assert len(ANCHORS) >= 5


def test_pinned_clusters_always_come_first_and_irrelevant_or_weak_ones_are_dropped():
    breaking = entry("c1", "x 2.0 released", 30, "official", "release", facts=dict(EMPTY, api_breaking=[{"symbols": ["a"]}]))
    stable = entry("c2", "y 1.0 released", 10, "official", "release", package="pypi:y")
    pre = entry("c3", "z 1.0rc1", 10, "official", "release", prerelease=True, package="pypi:z")
    advisory = entry("c4", "GHSA", 5, "official", "advisory", facts=dict(EMPTY, advisories=[{"severity": "critical"}]))
    low_adv = entry("c5", "GHSA low", 5, "official", "advisory", facts=dict(EMPTY, advisories=[{"severity": "low"}]), relevance_=0.0)
    hot = entry("c6", "HN agents", 16)
    weak = entry("c7", "paper", 10, kind="paper")
    off_topic = entry("c8", "quantum", 40, relevance_=0.0)
    chosen = [e["id"] for e in select_clusters([off_topic, weak, hot, low_adv, advisory, pre, stable, breaking])]
    # pinned by signal score (c1 30, c2 10, c4 5), then eligible by score (c6 16, c3 10); c5, c7, c8 are dropped
    assert chosen == ["c1", "c2", "c4", "c6", "c3"]


def test_limit_and_deterministic_tie_breaks():
    entries = [entry(f"c{i}", f"story {i}", 16, kind="paper" if i % 2 else "discussion") for i in range(1, 11)]
    chosen = [e["id"] for e in select_clusters(entries, limit=4)]
    assert chosen == ["c2", "c4", "c6", "c8"]                  # same score and relevance: discussions before papers, then id
    assert [e["id"] for e in select_clusters(list(reversed(entries)), limit=4)] == chosen
