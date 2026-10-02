import pytest

from radar.urls import canonical_url


@pytest.mark.parametrize("raw, expected", [
    ("HTTP://WWW.Example.com:443/a/b/?utm_source=x&b=2&a=1#frag", "https://example.com/a/b?a=1&b=2"),
    ("https://example.com/", "https://example.com/"),
    ("https://example.com/post/?fbclid=zzz", "https://example.com/post"),
    ("https://arxiv.org/pdf/2401.00001v2", "https://arxiv.org/abs/2401.00001"),
    ("http://arxiv.org/abs/2401.00001v1", "https://arxiv.org/abs/2401.00001"),
    ("https://export.arxiv.org/abs/2401.00001", "https://arxiv.org/abs/2401.00001"),
    ("https://github.com/acme/x/releases/tag/v1.0.0", "https://github.com/acme/x/releases/tag/v1.0.0"),
    ("not a url", "not a url"),
    ("https://example.com:badport/x", "https://example.com:badport/x"),
])
def test_canonical_url(raw, expected):
    assert canonical_url(raw) == expected
