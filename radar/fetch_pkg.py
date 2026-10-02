"""Fetch package artifacts from the PyPI and npm registries: read-only, host-allowlisted, size-capped."""
from urllib.parse import quote

import httpx

from radar.archive import read_tar_gz, read_zip
from radar.errors import SourceError

PYPI = "https://pypi.org/pypi"
NPM = "https://registry.npmjs.org"
ALLOWED_PREFIXES = ("https://files.pythonhosted.org/", "https://registry.npmjs.org/")
MAX_DOWNLOAD_BYTES = 40_000_000


def _py_wanted(name: str) -> bool:
    return name.endswith(".py")


def _ts_wanted(name: str) -> bool:
    return name.endswith((".d.ts", ".d.mts", ".d.cts")) or (name.endswith("/package.json") and name.count("/") == 1)


def _pick(artifacts: list[dict]) -> dict | None:
    wheels = sorted((a for a in artifacts if a["kind"] == "bdist_wheel"),
                    key=lambda a: (not a["filename"].endswith("py3-none-any.whl"), a["size"]))
    rest = [a for a in artifacts if a["kind"] in ("sdist", "tarball")]
    return next((a for a in wheels + rest if a["size"] <= MAX_DOWNLOAD_BYTES), None)


class PackageFetcher:
    def __init__(self, client: httpx.Client):
        self.client = client

    def _json(self, url: str):
        try:
            response = self.client.get(url)
        except httpx.HTTPError as exc:
            raise SourceError(f"{url} unreachable: {exc}") from exc
        if response.status_code != 200:
            raise SourceError(f"{url} returned {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise SourceError(f"{url} did not return JSON") from exc

    def versions(self, ecosystem: str, name: str) -> list[dict]:
        out = []
        if ecosystem == "pypi":
            for version, files in (self._json(f"{PYPI}/{quote(name)}/json").get("releases") or {}).items():
                arts = [{"url": f["url"], "kind": f.get("packagetype", ""), "size": f.get("size") or 0,
                         "filename": f.get("filename", ""), "time": f.get("upload_time_iso_8601")}
                        for f in files if not f.get("yanked") and f.get("url")]
                if arts:
                    out.append({"version": version, "published": next((a["time"] for a in arts if a["time"]), None),
                                "artifacts": arts})
        elif ecosystem == "npm":
            data = self._json(f"{NPM}/{name.replace('/', '%2F')}")
            for version, meta in (data.get("versions") or {}).items():
                dist = meta.get("dist") or {}
                if dist.get("tarball"):
                    out.append({"version": version, "published": (data.get("time") or {}).get(version),
                                "artifacts": [{"url": dist["tarball"], "kind": "tarball", "size": 0,
                                               "filename": dist["tarball"].rsplit("/", 1)[-1]}]})
        else:
            raise SourceError(f"unsupported ecosystem {ecosystem!r}")
        return out

    def download(self, url: str) -> bytes:
        if not url.startswith(ALLOWED_PREFIXES):
            raise SourceError(f"refusing to download from {url}")
        chunks, total = [], 0
        try:
            with self.client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise SourceError(f"{url} returned {response.status_code}")
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise SourceError(f"{url} exceeds {MAX_DOWNLOAD_BYTES} bytes")
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise SourceError(f"{url} unreachable: {exc}") from exc
        return b"".join(chunks)

    def files(self, ecosystem: str, name: str, version_info: dict) -> dict[str, str]:
        artifact = _pick(version_info["artifacts"])
        if artifact is None:
            raise SourceError(f"no artifact for {name} {version_info['version']} within the size cap")
        data = self.download(artifact["url"])
        if ecosystem == "npm":
            return read_tar_gz(data, _ts_wanted)
        return read_zip(data, _py_wanted) if artifact["filename"].endswith((".whl", ".zip")) \
            else read_tar_gz(data, _py_wanted)
