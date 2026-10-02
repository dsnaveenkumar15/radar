"""Atom/RSS parsing with the stdlib. DTDs and entities are refused outright: feeds never need them."""
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from radar.errors import SourceError

MAX_FEED_BYTES = 2_000_000
ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/elements/1.1/}"
_DTD = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.I)
_TAGS = re.compile(r"<[^>]+>")


def _epoch(text: str | None) -> float | None:
    if not text:
        return None
    text = text.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()


def _plain(text: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", text or ""))).strip()


def _text(node, tag: str) -> str:
    child = node.find(tag)
    return (child.text or "").strip() if child is not None else ""


def parse_feed(data: bytes) -> tuple[list[dict], int]:
    if len(data) > MAX_FEED_BYTES:
        raise SourceError("feed too large")
    if _DTD.search(data):
        raise SourceError("feed uses a DTD or entities; refusing to parse")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise SourceError(f"feed is not valid XML: {exc}") from exc
    entries, skipped = [], 0
    for node in list(root.iter(f"{ATOM}entry")) + list(root.iter("item")):
        if node.tag == f"{ATOM}entry":
            link = node.find(f"{ATOM}link[@rel='alternate']")
            if link is None:
                link = node.find(f"{ATOM}link")
            url = (link.get("href") or "").strip() if link is not None else ""
            title = _text(node, f"{ATOM}title")
            published = _epoch(_text(node, f"{ATOM}published") or _text(node, f"{ATOM}updated"))
            summary = _plain(_text(node, f"{ATOM}summary") or _text(node, f"{ATOM}content"))
        else:
            url, title = _text(node, "link"), _text(node, "title")
            published = _epoch(_text(node, "pubDate") or _text(node, f"{DC}date"))
            summary = _plain(_text(node, "description"))
        title = re.sub(r"\s+", " ", title).strip()
        if not (url and title and published):
            skipped += 1
            continue
        entries.append({"url": url, "title": title, "published": published, "summary": summary})
    return entries, skipped
