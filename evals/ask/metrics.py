"""Ranking metrics, bootstrap intervals and Cohen's kappa. Pure functions on plain lists; stdlib only."""
import math
import random

SEED = 20261124
RESAMPLES = 2000


def precision_at(ranked: list, relevant: set, k: int) -> float:
    """Relevant items in the top k, divided by k (a short list is not rewarded for being short)."""
    return sum(1 for i in ranked[:k] if i in relevant) / k


def recall_at(ranked: list, relevant: set, k: int) -> float:
    return sum(1 for i in ranked[:k] if i in relevant) / len(relevant)


def hit_at(ranked: list, wanted: set, k: int) -> float:
    return float(any(i in wanted for i in ranked[:k]))


def reciprocal_rank(ranked: list, relevant: set, k: int) -> float:
    return next((1 / (n + 1) for n, i in enumerate(ranked[:k]) if i in relevant), 0.0)


def ndcg_at(ranked: list, grades: dict, k: int) -> float:
    """Gain 2^grade - 1, log2 discount, ideal taken over every judged item."""
    def dcg(values):
        return sum((2 ** g - 1) / math.log2(n + 2) for n, g in enumerate(values))
    ideal = dcg(sorted(grades.values(), reverse=True)[:k])
    return dcg([grades.get(i, 0) for i in ranked[:k]]) / ideal if ideal else 0.0


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def quantile(ordered: list[float], q: float) -> float:
    pos = q * (len(ordered) - 1)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def bootstrap(values: list, statistic=mean, resamples: int = RESAMPLES, seed: int = SEED) -> tuple[float, float, float]:
    """(estimate, low, high): percentile bootstrap over questions, 95%."""
    if not values:
        return float("nan"), float("nan"), float("nan")
    rng = random.Random(seed)
    draws = [statistic(rng.choices(values, k=len(values))) for _ in range(resamples)]
    draws = sorted(d for d in draws if d == d)          # a resample with a single label has no kappa
    if not draws:
        return statistic(values), float("nan"), float("nan")
    return statistic(values), quantile(draws, 0.025), quantile(draws, 0.975)


def paired_difference(a: list[float], b: list[float], **kw) -> dict:
    """Mean of a - b over the same questions. 'significant' only if the 95% interval excludes zero."""
    est, low, high = bootstrap([x - y for x, y in zip(a, b, strict=True)], **kw)
    return {"diff": est, "low": low, "high": high, "significant": low > 0 or high < 0}


def wilson(successes: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """95% interval for a rate. Unlike the bootstrap it stays honest at 0/n and n/n."""
    if not n:
        return 0.0, 1.0
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def rate(flags: list) -> dict:
    n, x = len(flags), sum(map(bool, flags))
    low, high = wilson(x, n)
    return {"count": x, "n": n, "rate": round(x / n, 3) if n else None, "low": round(low, 3), "high": round(high, 3)}


def cohen_kappa(pairs: list[tuple]) -> float:
    """Agreement beyond chance for (rater a, rater b) pairs. Not a number when both raters use a single label."""
    n = len(pairs)
    if not n:
        return float("nan")
    labels = {x for p in pairs for x in p}
    observed = sum(a == b for a, b in pairs) / n
    expected = sum((sum(a == lab for a, _ in pairs) / n) * (sum(b == lab for _, b in pairs) / n) for lab in labels)
    return (observed - expected) / (1 - expected) if expected < 1 else float("nan")
