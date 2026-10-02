import io
import tarfile
import zipfile

import httpx
import pytest

from radar import fetch_pkg
from radar.errors import SourceError
from radar.fetch_pkg import PackageFetcher

WHEEL = io.BytesIO()
with zipfile.ZipFile(WHEEL, "w") as _zf:
    _zf.writestr("pkg/__init__.py", "def f(a): ...\n")
    _zf.writestr("pkg-1.0.dist-info/METADATA", "x")

TARBALL = io.BytesIO()
with tarfile.open(fileobj=TARBALL, mode="w:gz") as _tf:
    for name, text in {"package/package.json": "{}", "package/index.d.ts": "export declare const x: number;",
                       "package/dist/impl.js": "x"}.items():
        info = tarfile.TarInfo(name)
        info.size = len(text)
        _tf.addfile(info, io.BytesIO(text.encode()))

PYPI = {"releases": {
    "1.0.0": [{"url": "https://files.pythonhosted.org/a-1.0.0.tar.gz", "packagetype": "sdist", "size": 100, "filename": "a-1.0.0.tar.gz"},
              {"url": "https://files.pythonhosted.org/a-1.0.0-py3-none-any.whl", "packagetype": "bdist_wheel", "size": 200,
               "filename": "a-1.0.0-py3-none-any.whl", "upload_time_iso_8601": "2026-09-30T10:00:00Z"}],
    "0.9.0": [{"url": "https://files.pythonhosted.org/a-0.9.0.tar.gz", "packagetype": "sdist", "size": 1, "yanked": True,
               "filename": "a-0.9.0.tar.gz"}],
    "0.8.0": []}}
NPM = {"versions": {"2.0.0": {"dist": {"tarball": "https://registry.npmjs.org/@acme/sdk/-/sdk-2.0.0.tgz", "unpackedSize": 5}}},
       "time": {"2.0.0": "2026-09-29T00:00:00.000Z"}}


def make(handler):
    return PackageFetcher(httpx.Client(transport=httpx.MockTransport(handler)))


def handler(request):
    url = str(request.url)
    if url == "https://pypi.org/pypi/a/json":
        return httpx.Response(200, json=PYPI)
    if url == "https://registry.npmjs.org/@acme%2Fsdk":
        return httpx.Response(200, json=NPM)
    if url.endswith("-py3-none-any.whl"):
        return httpx.Response(200, content=WHEEL.getvalue())
    if url.endswith("sdk-2.0.0.tgz"):
        return httpx.Response(200, content=TARBALL.getvalue())
    return httpx.Response(404)


def test_pypi_versions_drop_yanked_and_empty_releases():
    versions = make(handler).versions("pypi", "a")
    assert [v["version"] for v in versions] == ["1.0.0"] and len(versions[0]["artifacts"]) == 2
    assert versions[0]["published"] == "2026-09-30T10:00:00Z"


def test_wheel_is_preferred_and_only_python_sources_are_returned():
    fetcher = make(handler)
    files = fetcher.files("pypi", "a", fetcher.versions("pypi", "a")[0])
    assert files == {"pkg/__init__.py": "def f(a): ...\n"}


def test_npm_scoped_package_and_declaration_files():
    fetcher = make(handler)
    info = fetcher.versions("npm", "@acme/sdk")[0]
    assert info["version"] == "2.0.0" and info["published"] == "2026-09-29T00:00:00.000Z"
    assert set(fetcher.files("npm", "@acme/sdk", info)) == {"package/package.json", "package/index.d.ts"}


def test_downloads_are_allowlisted_and_size_capped(monkeypatch):
    fetcher = make(handler)
    with pytest.raises(SourceError, match="refusing"):
        fetcher.download("https://evil.example/pkg.whl")
    monkeypatch.setattr(fetch_pkg, "MAX_DOWNLOAD_BYTES", 10)
    with pytest.raises(SourceError, match="exceeds"):
        fetcher.download("https://files.pythonhosted.org/a-1.0.0-py3-none-any.whl")


def test_no_artifact_within_the_cap_and_http_errors_are_source_errors():
    fetcher = make(lambda r: httpx.Response(200, json={"releases": {"1.0.0": [
        {"url": "https://files.pythonhosted.org/x.whl", "packagetype": "bdist_wheel", "size": 10**9, "filename": "x.whl"}]}}))
    with pytest.raises(SourceError, match="size cap"):
        fetcher.files("pypi", "x", fetcher.versions("pypi", "x")[0])
    with pytest.raises(SourceError, match="404"):
        make(lambda r: httpx.Response(404)).versions("pypi", "nope")
