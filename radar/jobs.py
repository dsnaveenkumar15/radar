"""Registers Radar job types and their schedules with the worker (PLANET_MODULES=trust.jobs,radar.jobs)."""
import os
import time

import httpx

from core.claude_cli import ClaudeCliBackend
from core.github import GitHub
from core.jobs import enqueue
from core.periodic import schedule
from core.scheduler import GuardedBackend, in_night_window
from core.worker import JobType
from radar import digest, store, trends
from radar.advisories import refresh_advisories
from radar.apisurface import refresh_api_surface
from radar.collect import run_collect
from radar.collectors import Context
from radar.config import load_entities, load_sources
from radar.embed import OllamaEmbedder
from radar.fetch_pkg import PackageFetcher
from radar.publishing import publish_aux, publish_errata

COLLECT_JOB, COLLECT_EVERY_S = "radar.collect", 3 * 3600
API_JOB, API_EVERY_S = "radar.apisurface", 6 * 3600
ADVISORY_JOB, ADVISORY_EVERY_S = "radar.advisories", 6 * 3600
DIGEST_JOB = "radar.digest"
ERRATA_JOB, ERRATA_EVERY_S = "radar.errata", 1800
AUX_JOB, AUX_EVERY_S = "radar.aux", 6 * 3600
TRENDS_JOB = "radar.trends"


def enqueue_digest(conn, date: str, now=None) -> int:
    return enqueue(conn, "radar", DIGEST_JOB, f"digest:{date}", {"date": date}, priority=2.0, now=now)


def schedule_periodic(conn, now: float) -> None:
    """Worker hook: idempotent per window, so calling it every loop is cheap."""
    store.ensure_schema(conn)
    schedule(conn, "radar", COLLECT_JOB, COLLECT_EVERY_S, now=now)
    schedule(conn, "radar", API_JOB, API_EVERY_S, now=now)
    schedule(conn, "radar", ADVISORY_JOB, ADVISORY_EVERY_S, now=now)
    schedule(conn, "radar", ERRATA_JOB, ERRATA_EVERY_S, now=now)
    schedule(conn, "radar", AUX_JOB, AUX_EVERY_S, now=now)
    if in_night_window(now):
        enqueue_digest(conn, digest.digest_date(now), now=now)
        week = trends.previous_week(now)
        enqueue(conn, "radar", TRENDS_JOB, f"trends:{week}", {"week": week}, priority=1.0, now=now)


def build_job_types(conn, github=None, backend=None, use_llm: bool = True, client=None, fetcher=None, embedder=None,
                    data_root=None, sources=None, registry=None, clock=time.time) -> dict[str, JobType]:
    store.ensure_schema(conn)
    sources = load_sources() if sources is None else sources
    registry = registry or load_entities()
    github = github or GitHub(conn, token=os.environ.get("GITHUB_TOKEN") or None)
    client = client or httpx.Client(timeout=30)
    fetcher = fetcher or PackageFetcher(client)
    embedder = embedder or OllamaEmbedder(base_url=os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    if not use_llm:
        backend = None
    elif backend is None:
        backend = GuardedBackend(conn, ClaudeCliBackend(model=os.environ.get("RADAR_MODEL", "sonnet")))
    model = getattr(getattr(backend, "inner", backend), "model", None) or getattr(backend, "name", None)
    data_root = data_root or os.environ.get("DATA_ROOT", "data/published")
    return {
        COLLECT_JOB: JobType("radar", [("collect", lambda job, out: run_collect(
            Context(conn=conn, github=github, client=client, now=clock()), sources))]),
        API_JOB: JobType("radar", [("apisurface", lambda job, out: refresh_api_surface(conn, fetcher, sources, clock()))]),
        ADVISORY_JOB: JobType("radar", [("advisories", lambda job, out: refresh_advisories(conn, client, sources, clock()))]),
        DIGEST_JOB: JobType("radar", digest.stages(conn, backend=backend, embedder=embedder, registry=registry,
                                                   sources=sources, data_root=data_root, model=model, clock=clock)),
        TRENDS_JOB: JobType("radar", trends.stages(conn, backend=backend, registry=registry, data_root=data_root, clock=clock)),
        AUX_JOB: JobType("radar", [("aux", lambda job, out: publish_aux(conn, data_root, clock()))]),
        ERRATA_JOB: JobType("radar", [("errata", lambda job, out: publish_errata(conn, data_root, registry, clock()))]),
    }
