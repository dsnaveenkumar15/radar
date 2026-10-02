import pytest

from radar import store

ITEM = {"source": "gh-x", "tier": "official", "kind": "release", "url": "https://github.com/a/x/releases/tag/v1",
        "title": "x v1", "body": "notes", "published": 1000.0, "package": "pypi:x", "version": "1.0.0",
        "repo": "a/x", "stars": 5, "extra": {"tag": "v1"}}


@pytest.fixture
def db(conn):
    store.ensure_schema(conn)
    return conn


def test_add_item_dedupes_on_source_and_canonical_url(db):
    first = store.add_item(db, ITEM, now=1.0)
    again = store.add_item(db, dict(ITEM, url="HTTPS://github.com/a/x/releases/tag/v1/?utm_source=z"), now=2.0)
    assert first is not None and again is None
    other_source = store.add_item(db, dict(ITEM, source="feed-y"), now=3.0)
    assert other_source is not None


def test_text_is_cleaned_of_hidden_and_control_characters(db):
    item_id = store.add_item(db, dict(ITEM, title="ok​title\x00", body="a‮b\U000e0041c\x07d\nline"), now=1.0)
    row = store.items_unconsumed(db, 0, 10)[0]
    assert row["id"] == item_id and row["title"] == "oktitle" and row["body"] == "abcd\nline"


def test_limits_are_applied(db):
    store.add_item(db, dict(ITEM, title="t" * 1000, body="b" * 50_000), now=1.0)
    row = store.items_unconsumed(db, 0, 10)[0]
    assert len(row["title"]) == 300 and len(row["body"]) == 20_000


def test_unconsumed_excludes_consumed_and_baseline_and_respects_since(db):
    a = store.add_item(db, ITEM, now=1.0)
    b = store.add_item(db, dict(ITEM, url="https://x.test/b", published=2000.0), now=1.0)
    store.add_item(db, dict(ITEM, url="https://x.test/c"), now=1.0, consumed="baseline")
    store.add_item(db, dict(ITEM, url="https://x.test/old", published=10.0), now=1.0)
    assert [r["id"] for r in store.items_unconsumed(db, 500.0, 10)] == [b, a]
    store.mark_consumed(db, [a], "2026-10-02")
    assert [r["id"] for r in store.items_unconsumed(db, 500.0, 10)] == [b]
    assert store.items_unconsumed(db, 500.0, 10)[0]["extra"] == {"tag": "v1"}


def test_source_health_and_since(db):
    day = 86400.0
    assert store.since(db, "s", now=100 * day) == 93 * day            # first run looks back 7 days
    store.record_source(db, "s", "official", "collector", ok=True, error=None, now=100 * day)
    assert store.since(db, "s", now=101 * day) == 98 * day            # watermark minus a 2-day overlap
    assert store.since(db, "s", now=200 * day) == 170 * day           # a long outage back-fills at most 30 days
    store.record_source(db, "s", "official", "collector", ok=False, error="boom", now=100.5 * day)
    row = store.source_health(db, 100.5 * day)[0]
    assert row["ok"] is True and row["consecutive_failures"] == 1 and row["last_error"] == "boom"
    assert store.source_health(db, 102 * day)[0]["ok"] is False        # no success within 36h
    store.record_source(db, "d", "official", "collector", ok=False, error="schema drift: no entries", now=1.0)
    assert [r["drift"] for r in store.source_health(db, 2.0) if r["source"] == "d"] == [True]


def test_star_velocity_uses_the_sample_at_least_seven_days_old(db):
    store.save_stars(db, "a/x", "2026-09-20", 100)
    store.save_stars(db, "a/x", "2026-09-26", 130)
    store.save_stars(db, "a/x", "2026-10-03", 190)
    import datetime as dt
    now = dt.datetime(2026, 10, 3, 12, tzinfo=dt.timezone.utc).timestamp()
    assert store.star_velocity(db, "a/x", now) == 190 - 130      # newest sample minus the latest one <= 7 days back
    assert store.star_velocity(db, "a/unknown", now) == 0
