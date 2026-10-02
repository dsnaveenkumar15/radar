import io
import tarfile
import zipfile

import pytest

from radar import archive
from radar.archive import ArchiveError, read_tar_gz, read_zip


def make_zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def make_tar(members, extra=()):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            raw = data.encode() if isinstance(data, str) else data
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            tf.addfile(info, io.BytesIO(raw))
        for info in extra:
            tf.addfile(info)
    return buf.getvalue()


def py(name):
    return name.endswith(".py")


@pytest.mark.parametrize("make, read", [(make_zip, read_zip), (make_tar, read_tar_gz)])
def test_only_wanted_text_members_are_returned(make, read):
    data = make({"pkg/a.py": "x = 1\n", "pkg/readme.md": "hi", "pkg/blob.py": b"\x00\x01", "pkg/latin.py": "caf\xe9".encode("latin-1")})
    files = read(data, py)
    assert set(files) == {"pkg/a.py", "pkg/latin.py"}
    assert files["pkg/a.py"] == "x = 1\n" and "caf" in files["pkg/latin.py"]


@pytest.mark.parametrize("make, read", [(make_zip, read_zip), (make_tar, read_tar_gz)])
@pytest.mark.parametrize("name", ["../evil.py", "/abs.py", "a/../../b.py", "c:evil.py"])
def test_unsafe_member_names_are_skipped(make, read, name):
    assert read(make({name: "x = 1", "ok.py": "y = 2"}), py) == {"ok.py": "y = 2"}


@pytest.mark.parametrize("make, read", [(make_zip, read_zip), (make_tar, read_tar_gz)])
def test_caps_reject_or_skip(make, read, monkeypatch):
    monkeypatch.setattr(archive, "MAX_MEMBER_BYTES", 10)
    assert read(make({"big.py": "x" * 11, "small.py": "x"}), py) == {"small.py": "x"}
    monkeypatch.setattr(archive, "MAX_MEMBERS", 2)
    with pytest.raises(ArchiveError, match="members"):
        read(make({"a.py": "1", "b.py": "2", "c.py": "3"}), py)
    monkeypatch.setattr(archive, "MAX_MEMBERS", 100)
    monkeypatch.setattr(archive, "MAX_UNPACKED_BYTES", 15)
    monkeypatch.setattr(archive, "MAX_MEMBER_BYTES", 100)
    with pytest.raises(ArchiveError, match="too large"):
        read(make({"a.py": "x" * 10, "b.py": "y" * 10}), py)


def test_backslash_names_are_unsafe():
    assert archive._safe("a\\b.py") is False and archive._safe("pkg/a.py") is True


def test_tar_links_are_never_read():
    link = tarfile.TarInfo("pkg/link.py")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    assert read_tar_gz(make_tar({"ok.py": "1"}, extra=[link]), py) == {"ok.py": "1"}


@pytest.mark.parametrize("read", [read_zip, read_tar_gz])
def test_garbage_is_an_archive_error(read):
    with pytest.raises(ArchiveError):
        read(b"definitely not an archive", py)


def test_a_huge_pax_header_cannot_exhaust_memory(monkeypatch):
    monkeypatch.setattr(archive, "MAX_UNPACKED_BYTES", 1000)
    monkeypatch.setattr(archive, "MAX_MEMBERS", 2)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tf:
        info = tarfile.TarInfo("a.py")
        info.size = 1
        info.pax_headers = {"comment": "x" * 3_000_000}
        tf.addfile(info, io.BytesIO(b"1"))
    with pytest.raises(ArchiveError, match="too large"):
        read_tar_gz(buf.getvalue(), py)


def test_a_corrupt_zip_member_is_skipped_not_fatal():
    data = bytearray(make_zip({"bad.py": "x = 1\n" * 200, "ok.py": "y = 2\n"}))
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as zf:
        info = zf.getinfo("bad.py")
        start = info.header_offset + 30 + len(info.filename) + len(info.extra)
    data[start] = 0xFF                                     # BTYPE=11 is not a valid deflate block
    assert read_zip(bytes(data), py) == {"ok.py": "y = 2\n"}
