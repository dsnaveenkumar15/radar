"""One collector per source type: (ctx, source, since) -> normalised items. Pure fetch + parse, no LLM."""
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from core.github import repo_from_url
from radar.config import Source
from radar.errors import SchemaDrift, SourceError
from radar.feeds import parse_feed
from radar.versions import split_tag

REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0/servers"
MAX_BODY_BYTES = 2_000_000


@dataclass
class Context:
    conn: sqlite3.Connection
    github: object
    client: httpx.Client
    now: float
    stars: dict[str, int] = field(default_factory=dict)


def _get(client: httpx.Client, url: str, params: dict | None = None) -> httpx.Response:
    try:
        response = client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise SourceError(f"{url} unreachable: {exc}") from exc
    if response.status_code != 200:
        raise SourceError(f"{url} returned {response.status_code}")
    if len(response.content) > MAX_BODY_BYTES:
        raise SourceError(f"{url} response too large")
    return response


def _json(client: httpx.Client, url: str, params: dict | None = None):
    try:
        return _get(client, url, params).json()
    except ValueError as exc:
        raise SourceError(f"{url} did not return JSON") from exc


def _epoch(text: str) -> float | None:
    try:
        parsed = datetime.fromisoformat((text or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _release_identity(source: Source, tag: str) -> tuple[str | None, str | None]:
    """(package, version) a release tag really belongs to. Monorepos tag every sub-package in one feed, so a tag
    like "langchain-openai==1.6.7" must not be filed under the tracked "langchain" package."""
    prefix = source.options.get("tag_prefix")
    if prefix:
        found = split_tag(tag[len(prefix):]) if tag.startswith(prefix) else None
        return (source.package or None, found[1]) if found else (None, None)
    found = split_tag(tag)
    if found is None:
        return None, None
    if not source.package:
        return None, found[1]
    label = _norm(found[0].rstrip("=@-_vV "))
    own = {_norm(source.package.partition(":")[2]), _norm(source.repo.partition("/")[2])}
    if re.fullmatch(r"(?i)(v|release[ _-]?|version[ _-]?)?", found[0]) or label in own:
        return source.package, found[1]
    return None, None


def collect_github_releases(ctx: Context, source: Source, since: float) -> list[dict]:
    releases = ctx.github.releases(source.repo)
    if not releases:
        raise SchemaDrift(f"schema drift: {source.repo} returned no releases")
    stars = ctx.github.repo_meta(source.repo)["stars"]
    ctx.stars[source.repo] = stars
    name = source.repo.split("/")[1]
    items, usable = [], 0
    for rel in releases:
        published = _epoch(rel["published"])
        if published is None or not rel["url"] or not (rel["name"] or rel["tag"]):
            continue
        usable += 1
        if published <= since:
            continue
        package, version = _release_identity(source, rel["tag"])
        items.append({"source": source.id, "tier": source.tier, "kind": "release", "url": rel["url"],
                      "title": f"{name} {rel['name'] or rel['tag']}", "body": rel["body"], "published": published,
                      "package": package, "version": version,
                      "repo": source.repo, "stars": stars,
                      "extra": {"tag": rel["tag"], "prerelease": rel["prerelease"]}})
    if not usable:
        raise SchemaDrift(f"schema drift: no release of {source.repo} had the fields we need")
    return items


def collect_feed(ctx: Context, source: Source, since: float) -> list[dict]:
    entries, skipped = parse_feed(_get(ctx.client, source.url).content)
    if not entries:
        raise SchemaDrift(f"schema drift: {source.url} has no parseable entries ({skipped} skipped)")
    return [{"source": source.id, "tier": source.tier, "kind": "post", "url": e["url"], "title": e["title"],
             "body": e["summary"], "published": e["published"]} for e in entries if e["published"] > since]


def _published_at(entry: dict) -> float | None:
    for value in (entry.get("_meta") or {}).values():
        if isinstance(value, dict) and value.get("publishedAt"):
            return _epoch(value["publishedAt"])
    return None


def collect_mcp_registry(ctx: Context, source: Source, since: float) -> list[dict]:
    """The registry lists servers alphabetically, so read what changed since the watermark, to the last page."""
    updated_since = datetime.fromtimestamp(since, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    pages, cursor, entries = int(source.options.get("max_pages", 30)), None, []
    for _ in range(pages):
        params = {"version": "latest", "limit": 100, "updated_since": updated_since, **({"cursor": cursor} if cursor else {})}
        data = _json(ctx.client, REGISTRY_URL, params)
        if not isinstance(data, dict):
            raise SchemaDrift("schema drift: registry did not return an object")
        entries += data.get("servers", [])
        cursor = (data.get("metadata") or {}).get("nextCursor")
        if not cursor:
            break
    items = []
    for entry in entries:
        server = entry.get("server") or {}
        info = server.get("repository") or {}
        repo = repo_from_url(info.get("url", "")) if info.get("source") == "github" else None
        if not repo or not server.get("name"):
            continue
        items.append({"source": source.id, "tier": source.tier, "kind": "server", "url": f"https://github.com/{repo}",
                      "title": f"New MCP server: {server['name']}", "body": server.get("description") or "",
                      "published": _published_at(entry) or ctx.now, "repo": repo,
                      "extra": {"version": server.get("version") or ""}})
    return items


ARXIV_URL = "https://export.arxiv.org/api/query"
HN_URL = "https://hn.algolia.com/api/v1/search_by_date"


def collect_arxiv(ctx: Context, source: Source, since: float) -> list[dict]:
    categories = source.options.get("categories") or ["cs.AI", "cs.CL", "cs.MA"]
    params = {"search_query": " OR ".join(f"cat:{c}" for c in categories), "sortBy": "submittedDate",
              "sortOrder": "descending", "max_results": 100}
    entries, skipped = parse_feed(_get(ctx.client, ARXIV_URL, params).content)
    if not entries:
        raise SchemaDrift(f"schema drift: arXiv returned no parseable entries ({skipped} skipped)")
    return [{"source": source.id, "tier": source.tier, "kind": "paper", "url": e["url"], "title": e["title"],
             "body": e["summary"], "published": e["published"]} for e in entries if e["published"] > since]


def collect_hn(ctx: Context, source: Source, since: float) -> list[dict]:
    min_points = int(source.options.get("min_points", 50))
    items, seen = [], set()
    for query in source.options.get("queries") or []:
        params = {"tags": "story", "query": query, "hitsPerPage": 50,
                  "numericFilters": f"created_at_i>{int(since)},points>{min_points}"}
        data = _json(ctx.client, HN_URL, params)
        if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
            raise SchemaDrift("schema drift: HN search did not return hits")
        for hit in data["hits"]:
            hn_id, title, created = hit.get("objectID"), hit.get("title"), hit.get("created_at_i")
            if not (hn_id and title and created) or hn_id in seen:
                continue
            seen.add(hn_id)
            items.append({"source": source.id, "tier": source.tier, "kind": "discussion",
                          "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hn_id}", "title": title,
                          "body": "", "published": float(created),
                          "extra": {"points": hit.get("points") or 0, "comments": hit.get("num_comments") or 0,
                                    "hn_id": hn_id}})
    return items


COLLECTORS = {"github_releases": collect_github_releases, "feed": collect_feed, "arxiv": collect_arxiv,
              "hn": collect_hn, "mcp_registry": collect_mcp_registry}
