# Radar: what was built, and when

Radar's completed work, in order. How it works: [`ARCHITECTURE.md`](ARCHITECTURE.md). Commit messages have the
detail.

## Where things stand (2026-10-09)

- Live at `radar.naveenlabs.tech`: daily digest, archive, sources, feed, Ask Radar, upgrade report, trends, upgrade
  agent.
- Digest notes run on OpenRouter under a $0.03 daily cap; the upgrade agent's daily cap is $0.25 (console override).
- About 850 tests.

## Timeline

| Date | Work |
|---|---|
| 2026-09-30 | Designed: a release radar for AI libraries where code clusters and scores and a model only writes cited notes |
| 2026-10-01 | Engine: collection, API-surface snapshots and diffs, advisories, clustering, nightly digest with "unsynthesized" and "partial" labels. Site: digests, archive, cluster pages, sources, feed, Ask Radar, error reports and errata |
| 2026-10-02 | v1.1: browser-side upgrade report, "updated" banners for late advisories, weekly trends; publishes the API surface of starter packages. Became its own repository |
| 2026-10-03 | Deployed; product design with light and dark themes; hardened container and headers |
| 2026-10-06 | Upgrade agent: two years of backfilled API history, dependency parsing, Python and TypeScript usage indexes, read-only investigator, code verifier, report with route, difficulty, security, patch and checklist; three recorded showcase runs on public demo repositories; live. "Instrument panel" identity |
| 2026-10-07 | Agent runtime moved into the shared core (no behaviour change); salted visitor keys; "Live" label by digest age; ticker wraps on phones |
| 2026-10-08 | Owner controls (agent switch and limits, batch cap, weekly brake, stop-all) and the private control interface; digest notes moved to OpenRouter with a daily cap; rate-limited calls retry in minutes; agent daily cap lowered to $0.25 |
| 2026-10-09 | Own architecture and history documents |

## Open items

- `langgraph.prebuilt.*` reported as removed (moved to a sister package): static-analysis blind spot.
- Verified provider changelog feeds still to be added to `radar/sources.toml`.
- The backfill retries unparseable old versions daily.
