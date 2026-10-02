"""Canonical URLs: one page reached through tracking links, www, http or a version suffix is one item."""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING = re.compile(r"^(utm_.*|ref|ref_src|fbclid|gclid|mc_.*|source|igshid)$")
_ARXIV = re.compile(r"^/(?:abs|pdf)/(.+?)(?:v\d+)?(?:\.pdf)?$")


def canonical_url(url: str) -> str:
    url = url.strip()
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().removeprefix("www.")
        port = parts.port
    except ValueError:
        return url
    if not host:
        return url
    path = parts.path or "/"
    if host in ("arxiv.org", "export.arxiv.org"):
        host = "arxiv.org"
        match = _ARXIV.match(path)
        if match:
            path = f"/abs/{match.group(1)}"
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                             if not _TRACKING.match(k.lower())))
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    return urlunsplit(("https", netloc, path, query, ""))
