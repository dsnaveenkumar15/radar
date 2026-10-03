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
clusters of the last 14 digests) and `/ask`.

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

### Radar v1.1

- `/upgrade`: paste a `requirements.txt`, `package.json`, `package-lock.json`, `poetry.lock` or `uv.lock`. The file is read in the browser and never sent anywhere; no model is involved. The report lists breaking changes, deprecations, migrations and open advisories between your pinned versions and the latest, from `upgrade.json`. Radar diffs the releases it has watched (the newest two stable versions per package at each refresh), so coverage grows over time; anything before the earliest snapshot is reported as not covered, and a package Radar does not track is never reported as safe.
- "updated" banners: an advisory published after a digest that lists a package version the cluster covered adds a banner automatically (`supersessions.json`); for a yanked release or a regression fix, use "Mark a cluster as updated" in `/admin`.
- `/trends`: weekly counts per entity computed from the published digests, plus one validated model note per week (rising and falling entities, patterns). The code drops any claim whose direction, cluster ids or numbers the counts do not support; without a model a week is published with counts only.
- Jobs: `radar.aux` (every 6 h) refreshes `packages.json`, `upgrade.json` and `supersessions.json` inside the current release; `radar.trends` runs in the night window and publishes the previous ISO week once.

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
