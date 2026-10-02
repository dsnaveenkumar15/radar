"""The nightly digest job: prepare -> synth -> validate -> publish. Each stage is cached, so a retry resumes."""
import time
from datetime import datetime

from core import scheduler
from core.llm import AuthExpired, BackendError, QuotaDeferred, RateLimited, SchemaError
from radar import store, synth
from radar.cluster import cluster_items
from radar.embed import EmbedError, embed_items, load_vectors
from radar.facts import cluster_facts
from radar.prefilter import ANCHORS, relevance, select_clusters
from radar.publishing import COMPLETE_RATIO, publish_digest
from radar.signal import SIGNAL_VERSION, score_cluster
from radar.synth import PROMPT_VERSION, synthesize_batch
from radar.validate import validate_notes

LOOKBACK_S = 14 * 86400
ITEM_LIMIT = 400
BODY_KEEP = 4000
MAX_SERVERS = 25
IMPACT_ORDER = {"breaking": 0, "notable": 1, "minor": 2}


def digest_date(now: float) -> str:
    """The IST date of now + 7h, so the whole 23:00-06:00 night is dated by the morning it ends in."""
    return datetime.fromtimestamp(now + 7 * 3600, scheduler.IST).date().isoformat()


def digest_deadline(date: str) -> float:
    return datetime.fromisoformat(date).replace(tzinfo=scheduler.IST, hour=6).timestamp()


def completeness(conn, sources, now: float) -> dict:
    health = {h["source"]: h for h in store.source_health(conn, now)}
    collectors = [s for s in sources if s.type in ("github_releases", "feed", "arxiv", "hn", "mcp_registry")]
    def fine(s) -> bool:       # succeeded recently AND the latest run did not fail (rate limit tonight = not checked)
        h = health.get(s.id)
        return bool(h and h["ok"] and h["consecutive_failures"] == 0)

    ok = [s for s in collectors if fine(s)]
    official = [s for s in collectors if s.tier == "official"]
    official_ok = [s for s in official if fine(s)]
    status = "full" if not official or len(official_ok) / len(official) >= COMPLETE_RATIO else "partial"
    return {"status": status, "checked": len(ok), "total": len(collectors),
            "failed": [s.id for s in collectors if s not in ok]}


def stage_prepare(conn, embedder, registry, now: float, date: str) -> dict:
    if conn.execute("SELECT 1 FROM radar_digests WHERE date=?", (date,)).fetchone():
        # A digest is immutable once out: a rerun must neither spend quota nor swallow items meant for the next one.
        return {"clusters": [], "considered": 0, "embedded": False, "new_servers": [], "item_ids": [],
                "already_published": True}
    items = store.items_unconsumed(conn, now - LOOKBACK_S, ITEM_LIMIT)
    servers = [i for i in items if i["kind"] == "server"]
    news = [i for i in items if i["kind"] != "server"]
    embedded = bool(embedder) and bool(news) and embed_items(conn, embedder, news)
    vectors = load_vectors(conn, [i["id"] for i in news]) if embedder else {}
    anchors = None
    if embedded:
        try:
            anchors = embedder.embed(ANCHORS)
        except EmbedError:
            anchors = None
    entries = []
    for c in cluster_items(news, vectors):
        facts = cluster_facts(conn, c.items)
        velocity = max((store.star_velocity(conn, i["repo"], now) for i in c.items if i.get("repo")), default=0)
        entries.append({"id": c.id, "items": c.items, "facts": facts,
                        "signal": score_cluster(c.items, velocity, bool(facts["api_breaking"] or facts["api_deprecations"])),
                        "relevance": relevance(c.items, registry, vectors, anchors)})
    selected = select_clusters(entries)
    for entry in selected:
        entry["items"] = [{**i, "body": i["body"][:BODY_KEEP]} for i in entry["items"]]
    return {"clusters": selected, "considered": len(entries), "embedded": embedded,
            "new_servers": [{"repo": s["repo"], "name": s["title"].removeprefix("New MCP server: "),
                             "description": s["body"][:300], "url": s["url"]} for s in servers[:MAX_SERVERS]],
            "item_ids": [i["id"] for i in items]}


def stage_synth(conn, backend, prepared: dict, stack_tags: list[str], now: float, date: str) -> dict:
    clusters = prepared["clusters"]
    if backend is None:
        return {"notes": [], "reason": "no-llm"}
    notes, reason = [], None
    for start in range(0, len(clusters), synth.BATCH_SIZE):
        try:
            notes += [n.model_dump() for n in synthesize_batch(conn, backend, clusters[start:start + synth.BATCH_SIZE],
                                                               stack_tags).notes]
        except (QuotaDeferred, RateLimited) as exc:
            until = getattr(exc, "reset_at", None) or scheduler.next_allowed_at(conn, now)
            if until <= digest_deadline(date):
                raise                                   # the worker defers the job; earlier batches are cached
            reason = reason or type(exc).__name__
            break
        except (AuthExpired, BackendError) as exc:
            reason = reason or type(exc).__name__
            break
        except SchemaError as exc:
            reason = reason or type(exc).__name__       # this batch stays unsynthesized; try the next one
    return {"notes": notes, "reason": reason}


def stage_validate(conn, synthesis: dict, prepared: dict, registry, now: float) -> dict:
    registry = registry.with_aliases(store.aliases(conn))
    accepted, report = validate_notes(conn, synthesis["notes"], prepared["clusters"], registry, now)
    return {"accepted": accepted, "report": report}


def _deterministic_impact(facts: dict) -> str:
    if facts["api_breaking"]:
        return "breaking"
    if facts["api_deprecations"] or any(a["severity"] in ("high", "critical") for a in facts["advisories"]):
        return "notable"
    return "minor"


def assemble(clusters: list[dict], accepted: dict) -> list[dict]:
    out = []
    for c in clusters:
        note = accepted.get(c["id"])
        best = min(c["items"], key=lambda i: (i["tier"] != "official", -i["published"]))
        out.append({"id": c["id"], "title": best["title"], "impact": note["impact"] if note else _deterministic_impact(c["facts"]),
                    "signal": c["signal"], "synthesis": note, "facts": c["facts"],
                    "items": [{"id": f"i{i['id']}", "source": i["source"], "tier": i["tier"], "kind": i["kind"],
                               "url": i["url"], "title": i["title"], "published": i["published"],
                               "content_hash": i["content_hash"], "package": i.get("package"), "version": i.get("version")}
                              for i in c["items"]]})
    return sorted(out, key=lambda c: (IMPACT_ORDER[c["impact"]], -c["signal"]["score"], int(c["id"][1:])))


def stage_publish(conn, out: dict, sources, registry, data_root, model, now: float, date: str) -> dict:
    prepared, synthesis, validated = out["prepare"], out["synth"], out["validate"]
    if prepared.get("already_published"):
        return {"published": False, "reason": "digest already published"}
    clusters = assemble(prepared["clusters"], validated["accepted"])
    noted = sum(1 for c in clusters if c["synthesis"])
    label = "complete" if noted == len(clusters) else "unsynthesized" if noted == 0 else "partial"
    comp = completeness(conn, sources, now)
    digest = {"schema": 1, "date": date, "generated_at": now, "status": comp["status"], "synthesis": label,
              "sources": {"checked": comp["checked"], "total": comp["total"], "failed": comp["failed"]},
              "provenance": {"prompt": PROMPT_VERSION, "model": model, "signal": SIGNAL_VERSION,
                             "embedded": prepared["embedded"], "unsynthesized_reason": synthesis["reason"]},
              "validation": validated["report"], "considered": prepared["considered"], "clusters": clusters,
              "new_servers": prepared["new_servers"]}
    result = publish_digest(conn, data_root, digest, store.source_health(conn, now), registry, now)
    store.mark_consumed(conn, prepared["item_ids"], date)       # consumed whether or not this run wrote the file
    return result


def stages(conn, *, backend, embedder, registry, sources, data_root, model, clock=time.time) -> list:
    return [
        ("prepare", lambda job, out: stage_prepare(conn, embedder, registry, clock(), job.payload["date"])),
        ("synth", lambda job, out: stage_synth(conn, backend, out["prepare"], registry.stack_tags(), clock(),
                                               job.payload["date"])),
        ("validate", lambda job, out: stage_validate(conn, out["synth"], out["prepare"], registry, clock())),
        ("publish", lambda job, out: stage_publish(conn, out, sources, registry, data_root, model, clock(),
                                                   job.payload["date"])),
    ]
