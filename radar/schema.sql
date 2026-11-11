CREATE TABLE IF NOT EXISTS radar_items (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  tier TEXT NOT NULL,              -- official | community
  kind TEXT NOT NULL,              -- release | post | paper | discussion | server | advisory
  url TEXT NOT NULL,
  canonical_url TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  published REAL NOT NULL,
  content_hash TEXT NOT NULL,
  package TEXT,                    -- "pypi:name" or "npm:name"
  version TEXT,
  repo TEXT,
  stars INTEGER NOT NULL DEFAULT 0,
  extra TEXT NOT NULL DEFAULT '{}',
  fetched_at REAL NOT NULL,
  digest_date TEXT,                -- digest date that consumed the item, or 'baseline'
  UNIQUE (source, canonical_url)
);
CREATE INDEX IF NOT EXISTS radar_items_open ON radar_items (digest_date, published);

CREATE TABLE IF NOT EXISTS radar_sources (
  source TEXT PRIMARY KEY,
  tier TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'collector',   -- collector | api | advisory
  watermark REAL NOT NULL DEFAULT 0,        -- time of the last successful run
  last_ok REAL,
  last_attempt REAL,
  last_error TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS radar_embeddings (
  item_id INTEGER PRIMARY KEY REFERENCES radar_items (id),
  model TEXT NOT NULL,
  dim INTEGER NOT NULL,
  vector BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS radar_star_samples (
  repo TEXT NOT NULL,
  day TEXT NOT NULL,
  stars INTEGER NOT NULL,
  PRIMARY KEY (repo, day)
);

CREATE TABLE IF NOT EXISTS radar_api_snapshots (
  ecosystem TEXT NOT NULL,
  package TEXT NOT NULL,
  version TEXT NOT NULL,
  symbols TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY (ecosystem, package, version)
);

-- Old releases the backfill could not read (a different layout or a broken archive): not downloaded again.
CREATE TABLE IF NOT EXISTS radar_api_unreadable (
  ecosystem TEXT NOT NULL,
  package TEXT NOT NULL,
  version TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY (ecosystem, package, version)
);

CREATE TABLE IF NOT EXISTS radar_api_diffs (
  ecosystem TEXT NOT NULL,
  package TEXT NOT NULL,
  from_version TEXT NOT NULL,
  to_version TEXT NOT NULL,
  diff TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY (ecosystem, package, from_version, to_version)
);

CREATE TABLE IF NOT EXISTS radar_advisories (
  id TEXT PRIMARY KEY,
  ecosystem TEXT NOT NULL,
  package TEXT NOT NULL,
  severity TEXT NOT NULL,          -- low | medium | high | critical | unknown, as the source states it
  summary TEXT NOT NULL,
  aliases TEXT NOT NULL DEFAULT '[]',
  published REAL,
  affected TEXT NOT NULL DEFAULT '[]',
  first_seen REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS radar_digests (
  date TEXT PRIMARY KEY,
  status TEXT NOT NULL,            -- full | partial
  synthesis TEXT NOT NULL,         -- complete | partial | unsynthesized
  release_id TEXT NOT NULL,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS radar_errata (
  id INTEGER PRIMARY KEY,
  digest_date TEXT NOT NULL,
  cluster_id TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS radar_aliases (
  alias_key TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL,
  created_at REAL NOT NULL
);


CREATE VIRTUAL TABLE IF NOT EXISTS radar_items_fts USING fts5(title, body, content='radar_items', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS radar_items_ai AFTER INSERT ON radar_items BEGIN
  INSERT INTO radar_items_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;
CREATE TRIGGER IF NOT EXISTS radar_items_ad AFTER DELETE ON radar_items BEGIN
  INSERT INTO radar_items_fts(radar_items_fts, rowid, title, body) VALUES ('delete', old.id, old.title, old.body);
END;

CREATE TABLE IF NOT EXISTS radar_supersessions (
  id INTEGER PRIMARY KEY,
  digest_date TEXT NOT NULL,
  cluster_id TEXT NOT NULL,
  text TEXT NOT NULL,
  url TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL
);
