# Radar

What changed in AI engineering, and whether it breaks your stack.

A self-contained app of the naveenlabs AI galaxy (siblings: Trust, Blueprints). Clone it, install, run: nothing else is needed.
Design: `docs/design.md` (one design for all three apps; this one implements §4 (core) and §6 (Radar)).
Architecture, security, reliability and operations: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

Production at a glance: a hardened web container (non-root, read-only image, no Linux capabilities, CPU and memory
limits) with a Docker health check on `/healthz` (which opens the database); HSTS, a strict CSP and a permissions
policy on every response; exact dependency pins in `requirements.txt`.

## Local development (Windows)

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
```

Run the web app (Chrome and Edge resolve `*.localhost` to 127.0.0.1):

```powershell
$env:PUBLIC_SCHEME="http"; $env:PUBLIC_PORT=":8001"; $env:ADMIN_USER="owner"; $env:ADMIN_PASSWORD="local-dev-password-123"
.venv\Scripts\python -m uvicorn web.app:build --factory --port 8001
```

Open http://radar.localhost:8001 and http://radar.localhost:8001/admin. Only this app's host answers; any other host is a 404.

Run the worker (uses the laptop's Claude Code login and Ollama):

```powershell
$env:PLANET_MODULES="radar.jobs"; $env:WEIGHTS="radar=100"
.venv\Scripts\python -m core.worker
```

## Radar engine

With `PLANET_MODULES=trust.jobs,radar.jobs` the worker schedules `radar.collect` every 3 hours, `radar.apisurface`
and `radar.advisories` every 6 hours (none use Claude), and one `radar.digest` per night (Claude, inside the IST night
window). If Claude is unavailable past 06:00 IST the digest is published labeled "unsynthesized"; if fewer than 80% of
official sources answered it is labeled "partial". A published digest is immutable.

Run a job by hand (prints each stage's output):

```powershell
.venv\Scripts\python -m radar.cli collect --db data\planets.db
.venv\Scripts\python -m radar.cli apisurface --db data\planets.db
.venv\Scripts\python -m radar.cli digest --db data\planets.db --data-root data\published --no-llm
```

Tracked sources live in `radar/sources.toml` (add verified provider changelog feeds there with `type = "feed"`);
the canonical entity registry is `radar/entities.toml`. Unknown entities the model names land in `/admin` as
`unknown-entity` review items. Diff quality gate: `.venv\Scripts\python -m evals.radar_eval`.
The Radar web pages, Ask Radar and RSS are M2b.

## Radar site

The Radar host serves the published digests: `/` (latest), `/digests/<date>`, `/archive`, `/c/<date>/<cluster>`,
`/sources` (source health, the public signal formula, what is validated), `/feed.xml` (breaking and notable
clusters of the last 14 digests, for feed readers; the nav's RSS link opens `/feed`, a page that explains the feed,
copies its address and lists its current items) and `/ask`.

- **My stack** and **since last visit** run in the browser (`localStorage`); nothing about a visitor reaches the server.
- **Ask Radar** (`POST /api/ask`, 10 per minute per IP): "what broke in <package> [since <version>]" and
  "CVEs in <package>" are answered from API-diff and advisory data with no model. Anything else searches the archive
  (SQLite FTS5 plus embeddings) and the local Ollama model answers; every sentence must cite an item or is removed.
- **Report an error** (`POST /api/report`, 5 per minute per IP) lands in the `/admin` review queue. Publish an erratum
  there (optionally from a report); the `radar.errata` job republishes the release with `errata.json` within 30
  minutes. Published digests are never rewritten.
- Unknown entities the model names appear in `/admin` as `unknown-entity`; alias them to a canonical entity once and
  the next digest resolves them. The Radar panel on `/admin` lists failing sources and the latest digest.

Preview locally with real data (no Claude needed):

```powershell
$env:DB_PATH = "data\radar.db"; $env:DATA_ROOT = "data\radar-published"
.venv\Scripts\python -m radar.cli collect
.venv\Scripts\python -m radar.cli apisurface
.venv\Scripts\python -m radar.cli advisories
.venv\Scripts\python -m radar.cli digest --no-llm
.venv\Scripts\python -m uvicorn web.app:build --factory --port 8000
# then open http://radar.localhost:8000/
```

- **Look:** Radar has its own theme pack in `web/static/themes/radar/` (instrument panel: graphite and amber, mono type,
  square cells, a radar sweep). `web/planets.py` turns it on (`theme`); `base.html` loads it after `site.css` and sets
  the signature mode (dark) used when a visitor has not picked one. The home is a dashboard (`web/radar_dashboard.py`,
  `radar_home.html`) followed by the full digest. Spec: `docs/superpowers/specs/2026-10-06-ai-apps-visual-identity-design.md`.

### Radar v1.1

- `/upgrade`: paste a `requirements.txt`, `package.json`, `package-lock.json`, `poetry.lock` or `uv.lock`. The file is read in the browser and never sent anywhere; no model is involved. The report lists breaking changes, deprecations, migrations and open advisories between your pinned versions and the latest, from `upgrade.json`. Radar diffs the releases it has watched (the newest two stable versions per package at each refresh), so coverage grows over time; anything before the earliest snapshot is reported as not covered, and a package Radar does not track is never reported as safe.
- "updated" banners: an advisory published after a digest that lists a package version the cluster covered adds a banner automatically (`supersessions.json`); for a yanked release or a regression fix, use "Mark a cluster as updated" in `/admin`.
- `/trends`: weekly counts per entity computed from the published digests, plus one validated model note per week (rising and falling entities, patterns). The code drops any claim whose direction, cluster ids or numbers the counts do not support; without a model a week is published with counts only.
- Jobs: `radar.aux` (every 6 h) refreshes `packages.json`, `upgrade.json` and `supersessions.json` inside the current release; `radar.trends` runs in the night window and publishes the previous ISO week once.

### Radar upgrade agent (`/agent`)

A visitor gives a public GitHub repository (or pastes a dependency file) and watches an agent find what breaks when the
tracked AI libraries are upgraded. Spec: `docs/superpowers/specs/2026-10-06-radar-upgrade-agent-design.md`; plan:
`docs/superpowers/plans/2026-10-06-radar-upgrade-agent.md`.

- **Graph (LangGraph):** `fetch_inputs -> parse_deps -> ground -> index_code -> plan -> investigate (loop) -> verify -> report`.
  Everything except plan, investigate and the report text is plain Python. Findings are accepted only after
  `radar/agent/verify.py` (code, not a model) has matched each quoted line against the real file.
- **Data:** `api_changes.json` (published with the digests) holds every version step of every tracked package, uncapped.
  The daily `radar.apibackfill` job snapshots the last ~2 years of minor releases (40 downloads per run; fill it at once
  with `python -m radar.cli backfill`, a few times).
- **Models:** OpenAI-compatible chat models on OpenRouter through `radar/agent/gateway.py` (backup model, cost from the
  provider response, budgets checked before every call). Settings (web `.env`; the batch worker does not need them):
  `AGENT_ENABLED=1`, `OPENROUTER_API_KEY`, `AGENT_FAST_MODELS` and `AGENT_STRONG_MODELS` (comma lists, primary first),
  `AGENT_PUBLIC_MODELS` (optional; free-tier models that may keep prompts: used first, and only, for public repository runs, never for pasted files), `AGENT_PRICES` (`model=in/out;...` USD per million tokens, used when the provider reports no cost), `AGENT_DAILY_USD`
  (default 1.0), and `GITHUB_TOKEN` (repo fetches; same read-only token as `batch.env`).
- **Limits:** per run 8 candidates, 30 tool calls, about 60k tokens, 3 minutes (plus 45 s always kept for the report), $0.05; per visitor 3 runs a day and one at
  a time; 2 runs at once with a queue of 3; **$1.00 a day** for the whole site. Admin has a kill switch and today's spend.
- **Showcase replays:** run the agent on a demo repository, then mark the run:
  `UPDATE agent_runs SET showcase=1 WHERE id='...'` (in the app database). Replays cost nothing and never expire.
- **Evaluation:** `python -m evals.radar_agent_eval` scores 8 offline cases (precision at least 0.9, recall at least 0.8; CI
  runs the same gate); `--live` runs the same cases with the real models for a few cents before a deploy.
- Runs execute on background threads in the web container; a restart marks runs in flight as failed. No checkpoint
  store is used in production (the graph accepts one: state holds the repo files).

## Deploy (VPS)

1. DNS A record `radar` pointing at the VPS IP; Traefik runs on the VPS.
2. The naveenlabs-ai stack is running (it provides the `naveenlabs-ai_default` network and `ollama`); give its `ollama` service a memory limit (for example `mem_limit: 4g`) so it cannot crowd out other apps.
3. `cp .env.example .env`, `cp batch.env.example batch.env`; set `ADMIN_PASSWORD` (16+ characters; `/admin` stays disabled while it is the default or short); on the laptop run `claude setup-token` and paste the token into `batch.env`.
4. `docker compose up -d --build`; Traefik picks up the labels and issues the certificate. Wait for `docker inspect -f '{{.State.Health.Status}}' radar-web-1` to say `healthy`.
5. Monitoring: probe `https://<host>/healthz` from outside the server (the `atlas` repository's `uptime` workflow does this every 30 minutes); optionally an Uptime Kuma push monitor whose URL goes in `KUMA_PUSH_URL`.


## The shared core

`core/` (LLM gateway, job engine, publishing, events, review queue, scheduler) and the shared web kit (`web/app.py`, `web/admin.py`, `web/config.py`, base templates and stylesheet, and their tests) are identical copies of the master in the `atlas` repository, so this repository runs on its own. The list is `scripts/core_manifest.json`; `CORE_VERSION` names the master commit last synced.

```powershell
python scripts/sync_core.py --source PATH_OR_GIT_URL --check    # what differs from the master
python scripts/sync_core.py --source PATH_OR_GIT_URL            # pull the master's core into this repo
```

A change to the shared core belongs in the master first, then in each app repo with this script.
