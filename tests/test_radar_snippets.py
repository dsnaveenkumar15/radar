import pytest

from radar.snippets import check_migration, parses

PY = {"ecosystem": "pypi", "module": "pkg", "symbols": {"pkg.run", "pkg.Agent", "pkg.Agent.go", "pkg.sub.helper"}}
TS = {"ecosystem": "npm", "module": "@acme/sdk", "symbols": {"run", "Agent", "Agent.go", "./client#Client"}}


def mig(language, before, after):
    return {"language": language, "before": before, "after": after}


def test_valid_python_migration_passes():
    assert check_migration(mig("python", "from pkg import old\nold()", "from pkg import run\nrun(1)"), [PY]) is None
    assert check_migration(mig("python", "x = 1", "import pkg\npkg.run(1)"), [PY]) is None
    assert check_migration(mig("python", "x = 1", "import pkg as p\np.Agent.go()"), [PY]) is None
    assert check_migration(mig("python", "x = 1", "import pkg.sub as s\ns.helper()"), [PY]) is None


@pytest.mark.parametrize("before, after, reason", [
    ("x = (", "from pkg import run", "before snippet does not parse"),
    ("x = 1", "def (:", "after snippet does not parse"),
    ("x = 1", "from pkg import missing\nmissing()", "not in the new API"),
    ("x = 1", "import pkg\npkg.nothing()", "not in the new API"),
    ("x = 1", "print('hello')", "no symbol that could be verified"),
])
def test_invalid_python_migrations_are_rejected(before, after, reason):
    assert reason in check_migration(mig("python", before, after), [PY])


def test_no_snapshot_means_nothing_can_be_verified():
    assert "no API snapshot" in check_migration(mig("python", "x = 1", "from pkg import run"), [])
    assert "no symbol that could be verified" in check_migration(mig("python", "x = 1", "from pkg import run"), [TS])


def test_typescript_migrations():
    ok = mig("typescript", 'import { old } from "@acme/sdk";', 'import { run, Agent as A } from "@acme/sdk";\nrun(1);')
    assert check_migration(ok, [TS]) is None
    assert "not in the new API" in check_migration(mig("typescript", "const a = 1;", 'import { gone } from "@acme/sdk";'), [TS])
    assert "does not parse" in check_migration(mig("typescript", "const = ;;;", 'import { run } from "@acme/sdk";'), [TS])
    sub = mig("typescript", "const a = 1;", 'import { Client } from "@acme/sdk/client";\nnew Client();')
    assert check_migration(sub, [TS]) is None                                    # subpath imports match "./client#Client"
    assert "not in the new API" in check_migration(mig("typescript", "const a = 1;", 'import { Nope } from "@acme/sdk/client";'), [TS])
    assert "no symbol that could be verified" in check_migration(mig("typescript", "const a = 1;", 'import { x } from "other-package";'), [TS])


def test_parses():
    assert parses("python", "x = 1\n") and not parses("python", "x = (") and parses("typescript", "const a: number = 1;")
    assert not parses("typescript", "function (")


def test_deeply_nested_attribute_chains_are_rejected_not_fatal():
    import sys
    deep = "import pkg as m\nm" + ".a" * 300 + "()"
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(150)
    try:
        reason = check_migration(mig("python", "x = 1", deep), [PY])
    finally:
        sys.setrecursionlimit(old)
    assert reason is not None and "nested" in reason
