"""Read files out of a downloaded package archive in memory. Nothing is extracted, imported or run."""
import gzip
import io
import tarfile
import zipfile
import zlib
from typing import Callable

MAX_MEMBERS = 5000
MAX_MEMBER_BYTES = 2_000_000
MAX_UNPACKED_BYTES = 60_000_000


class ArchiveError(Exception):
    """The archive is malformed or exceeds a safety cap."""


def _safe(name: str) -> bool:
    return bool(name) and not name.startswith("/") and "\\" not in name and ":" not in name \
        and ".." not in name.split("/")


def _keep(blob: bytes) -> bool:
    return len(blob) <= MAX_MEMBER_BYTES and b"\x00" not in blob


class _Capped:
    """Read side of a gzip stream that refuses to hand out more than `cap` decompressed bytes in total.
    tarfile reads extended (pax/GNU) headers fully into memory, so the cap has to sit below tarfile."""

    def __init__(self, raw, cap: int):
        self.raw, self.cap, self.count = raw, cap, 0

    def read(self, size: int = -1) -> bytes:
        data = self.raw.read(size)
        self.count += len(data)
        if self.count > self.cap:
            raise ArchiveError("archive unpacks too large")
        return data


def read_zip(data: bytes, wanted: Callable[[str], bool]) -> dict[str, str]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"not a zip archive: {exc}") from exc
    files: dict[str, str] = {}
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise ArchiveError(f"archive has {len(infos)} members (cap {MAX_MEMBERS})")
        if sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
            raise ArchiveError("archive unpacks too large")
        for info in infos:
            if info.is_dir() or not _safe(info.filename) or not wanted(info.filename) \
                    or info.file_size > MAX_MEMBER_BYTES:
                continue
            try:
                with zf.open(info) as handle:
                    blob = handle.read(MAX_MEMBER_BYTES + 1)   # a lying header cannot make us read more
            except (RuntimeError, NotImplementedError, zipfile.BadZipFile, zlib.error, EOFError, OSError):
                continue
            if _keep(blob):
                files[info.filename] = blob.decode("utf-8", "replace")
    return files


def read_tar_gz(data: bytes, wanted: Callable[[str], bool]) -> dict[str, str]:
    files: dict[str, str] = {}
    seen = total = 0
    try:
        capped = _Capped(gzip.GzipFile(fileobj=io.BytesIO(data)), MAX_UNPACKED_BYTES + MAX_MEMBERS * 1536)
        with tarfile.open(fileobj=capped, mode="r|") as tf:
            for member in tf:
                seen += 1
                total += member.size
                if seen > MAX_MEMBERS:
                    raise ArchiveError(f"archive has more than {MAX_MEMBERS} members")
                if total > MAX_UNPACKED_BYTES:       # checked from the header, before the data is skipped or read
                    raise ArchiveError("archive unpacks too large")
                if not member.isfile() or not _safe(member.name) or not wanted(member.name) \
                        or member.size > MAX_MEMBER_BYTES:
                    continue
                blob = tf.extractfile(member).read(MAX_MEMBER_BYTES + 1)
                if _keep(blob):
                    files[member.name] = blob.decode("utf-8", "replace")
    except (tarfile.TarError, OSError, EOFError, zlib.error) as exc:
        raise ArchiveError(f"not a readable tar.gz archive: {exc}") from exc
    return files
