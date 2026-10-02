"""Hold the synthesizer to the evidence. Whatever cannot be verified is dropped, downgraded or sent to review."""
import re
from collections import Counter

from core import review
from core.evidence import missing, quote_in_text
from radar.facts import package_surface
from radar.snippets import check_migration
from radar.synth import ClusterNote
from radar.versions import find_versions, same


URL = re.compile(r"https?://\S+")
BREAK_WORDS = re.compile(r"(?i)breaking|remov|no longer|renam|drop|incompatible|deprecat")
MIN_BREAKING_QUOTE_CHARS = 20


def _strip_urls(text: str) -> tuple[str, int]:
    cleaned, n = URL.subn("", text)
    return re.sub(r" {2,}", " ", cleaned).strip(), n


def validate_notes(conn, notes: list[dict], clusters: list[dict], registry, now: float) -> tuple[dict, dict]:
    by_id = {c["id"]: c for c in clusters}
    stack = set(registry.stack_tags())
    report: Counter = Counter()
    reasons: list[str] = []
    accepted: dict[str, dict] = {}
    for raw in notes:
        try:
            note = ClusterNote.model_validate(raw)
        except ValueError:
            report["invalid_note"] += 1
            continue
        cluster = by_id.get(note.cluster)
        if cluster is None:
            report["unknown_cluster"] += 1
            continue
        if note.cluster in accepted:
            report["duplicate"] += 1
            continue
        items = {f"i{i['id']}": i for i in cluster["items"]}
        citations = [c for c in dict.fromkeys(note.citations) if c in items]
        report["bad_citations"] += len(missing(note.citations, items))
        if not citations:
            report["no_valid_citation"] += 1
            continue
        facts = cluster["facts"]
        if any(not any(same(v, k) for k in facts["versions"]) for v in note.versions):
            report["unknown_version"] += 1
            continue
        known = set(facts["versions"]) | {v for i in cluster["items"] for v in find_versions(f"{i['title']} {i['body']}")}
        if any(not any(same(v, k) for k in known) for v in find_versions(f"{note.what_changed} {note.action}")):
            report["unknown_version_in_text"] += 1
            continue
        what_changed, n1 = _strip_urls(note.what_changed)
        action, n2 = _strip_urls(note.action)
        report["urls_removed"] += n1 + n2
        quote = note.quote.model_dump() if note.quote else None
        verified = bool(quote and quote["item"] in citations and items[quote["item"]]["kind"] == "release"
                        and len(quote["quote"].strip()) >= MIN_BREAKING_QUOTE_CHARS and BREAK_WORDS.search(quote["quote"])
                        and quote_in_text(items[quote["item"]]["body"], quote["quote"]))
        impact = note.impact
        if facts["api_breaking"]:
            if impact != "breaking":
                report["forced_breaking"] += 1
            impact = "breaking"
        elif impact == "breaking" and not verified:
            impact = "notable"
            report["downgraded_breaking"] += 1
        affects = [t for t in dict.fromkeys(note.affects) if t in stack]
        report["unknown_stack_tags"] += len(set(note.affects)) - len(affects)
        entities: list[str] = []
        for name in note.entities:
            entity_id = registry.resolve(name)
            if entity_id:
                if entity_id not in entities:
                    entities.append(entity_id)
                continue
            report["unknown_entities"] += 1
            ref = re.sub(r"[^a-z0-9]+", "", name.lower())[:80]
            if ref:
                review.add(conn, "radar", "unknown-entity", ref, {"name": name, "cluster": cluster["id"]}, now=now)
        migration = None
        if note.migration:
            surfaces = [s for s in (package_surface(conn, p["package"], p["version"]) for p in facts["packages"]) if s]
            try:
                reason = check_migration(note.migration.model_dump(), surfaces)
            except Exception as exc:                      # hostile snippets must never kill the night's digest
                reason = f"snippet check failed: {type(exc).__name__}"
            if reason is None:
                migration = note.migration.model_dump()
            else:
                report["migration_dropped"] += 1
                reasons.append(reason)
        accepted[note.cluster] = {
            "what_changed": what_changed, "affects": affects, "impact": impact,
            "migration_effort": note.migration_effort, "action": action, "citations": citations,
            "quote": quote if verified else None, "versions": list(note.versions), "entities": entities,
            "migration": migration}
    return accepted, {**report, "reasons": reasons[:10]}
