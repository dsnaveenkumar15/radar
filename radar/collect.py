"""Run every configured collector. One source failing never stops the others (spec §6.3, §8)."""
import logging
from datetime import datetime, timezone

from core.db import tx
from core.events import emit
from core.github import GitHubError, GitHubRateLimited
from radar import store
from radar.collectors import COLLECTORS, Context
from radar.config import Source
from radar.errors import SourceError

log = logging.getLogger("radar.collect")


def run_collect(ctx: Context, sources: list[Source]) -> dict:
    ok, failed, new_total, github_limited = [], {}, 0, False

    def fail(source: Source, error: str) -> None:
        store.record_source(ctx.conn, source.id, source.tier, "collector", False, error, ctx.now)
        failed[source.id] = error

    for source in sources:
        row = ctx.conn.execute("SELECT watermark FROM radar_sources WHERE source=?", (source.id,)).fetchone()
        baseline = row is None or not row["watermark"]
        if source.type == "github_releases" and github_limited:
            fail(source, "skipped: GitHub rate limit")
            continue
        try:
            items = COLLECTORS[source.type](ctx, source, store.since(ctx.conn, source.id, ctx.now))
        except GitHubRateLimited as exc:
            github_limited = True
            fail(source, f"GitHub rate limited: {exc}")
            continue
        except (SourceError, GitHubError) as exc:
            fail(source, str(exc))
            continue
        except Exception as exc:
            log.exception("collector %s crashed", source.id)
            fail(source, f"unexpected: {exc!r}")
            continue
        for item in items:
            is_server = item["kind"] == "server"
            with tx(ctx.conn):
                item_id = store.add_item(ctx.conn, item, ctx.now, consumed="baseline" if baseline and is_server else None)
                if item_id is not None and is_server and not baseline:
                    emit(ctx.conn, "radar.mcp_server_discovered",
                         {"repo": item["repo"], "source": "registry", "signal": 0}, now=ctx.now)
            new_total += item_id is not None
        store.record_source(ctx.conn, source.id, source.tier, "collector", True, None, ctx.now)
        ok.append(source.id)
    day = datetime.fromtimestamp(ctx.now, timezone.utc).date().isoformat()
    for repo, stars in ctx.stars.items():
        store.save_stars(ctx.conn, repo, day, stars)
    return {"new": new_total, "ok": ok, "failed": failed}
