import pytest

from radar.errors import SourceError
from radar.feeds import parse_feed

ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><title>Alpha &amp; omega</title><link rel="alternate" href="https://example.com/a"/>
    <published>2026-09-30T10:00:00Z</published><summary>&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;&lt;/p&gt;</summary></entry>
  <entry><title>No date</title><link href="https://example.com/b"/></entry>
  <entry><link href="https://example.com/c"/><updated>2026-09-30T11:00:00Z</updated></entry>
</feed>"""

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title>Post</title><link>https://example.com/p</link><pubDate>Wed, 30 Sep 2026 10:00:00 +0000</pubDate>
    <description>Body text</description></item>
</channel></rss>"""


def test_atom_entries_parse_with_cleaned_summary_and_skip_count():
    entries, skipped = parse_feed(ATOM)
    assert skipped == 2
    assert entries == [{"url": "https://example.com/a", "title": "Alpha & omega", "published": 1790762400.0,
                        "summary": "Hello world"}]


def test_rss_items_parse():
    entries, skipped = parse_feed(RSS)
    assert skipped == 0 and entries[0]["title"] == "Post" and entries[0]["published"] == 1790762400.0


@pytest.mark.parametrize("data", [
    b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa">]><feed xmlns="http://www.w3.org/2005/Atom"/>',
    b"<feed><entry>",
    b"not xml at all",
    b"<feed>" + b"x" * 3_000_000 + b"</feed>",
], ids=["doctype", "truncated", "garbage", "oversized"])
def test_hostile_or_broken_feeds_are_refused(data):
    with pytest.raises(SourceError):
        parse_feed(data)
