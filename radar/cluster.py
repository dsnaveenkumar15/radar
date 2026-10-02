"""Group items about the same event. Deterministic for a given set of items and vectors."""
import re
from dataclasses import dataclass

from radar.embed import cosine

MAX_ITEMS = 400           # pairwise comparison cost; the newest items win, older ones wait for a quieter night
WINDOW_S = 72 * 3600
EMBED_SIMILARITY = 0.86
TITLE_JACCARD = 0.6
_TOKEN = re.compile(r"[a-z0-9]{3,}")
_TIER_RANK = {"official": 0, "community": 1}


@dataclass
class Cluster:
    id: str
    items: list[dict]


def _cannot_link(a: dict, b: dict) -> bool:
    pa, pb = a.get("package"), b.get("package")
    if pa and pb and pa != pb:
        return True
    return bool(pa and pb and a.get("version") and b.get("version") and a["version"] != b["version"])


def cluster_items(items: list[dict], vectors: dict[int, list[float]] | None = None) -> list[Cluster]:
    vectors = vectors or {}
    newest = sorted(items, key=lambda i: (-i["published"], i["id"]))[:MAX_ITEMS]
    items = sorted(newest, key=lambda i: i["id"])
    parent = {i["id"]: i["id"] for i in items}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def link(a: dict, b: dict) -> None:
        if _cannot_link(a, b):
            return
        ra, rb = find(a["id"]), find(b["id"])
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    first_by_url, first_by_release = {}, {}
    for item in items:
        release = (item["package"], item["version"]) if item.get("package") and item.get("version") else None
        for table, key in ((first_by_url, item["canonical_url"]), (first_by_release, release)):
            if key is None:
                continue
            if key in table:
                link(table[key], item)
            else:
                table[key] = item
    tokens = {i["id"]: set(_TOKEN.findall(i["title"].lower())) for i in items}
    by_time = sorted(items, key=lambda i: (i["published"], i["id"]))
    for x, a in enumerate(by_time):
        for b in by_time[x + 1:]:
            if b["published"] - a["published"] > WINDOW_S:
                break
            if find(a["id"]) == find(b["id"]) or _cannot_link(a, b):
                continue
            va, vb = vectors.get(a["id"]), vectors.get(b["id"])
            if va and vb:
                similar = cosine(va, vb) >= EMBED_SIMILARITY
            else:
                ta, tb = tokens[a["id"]], tokens[b["id"]]
                similar = bool(ta and tb) and len(ta & tb) / len(ta | tb) >= TITLE_JACCARD
            if similar:
                link(a, b)
    groups: dict[int, list[dict]] = {}
    for item in items:
        groups.setdefault(find(item["id"]), []).append(item)
    return [Cluster(f"c{root}", sorted(members, key=lambda i: (_TIER_RANK.get(i["tier"], 2), -i["published"], i["id"])))
            for root, members in sorted(groups.items())]
