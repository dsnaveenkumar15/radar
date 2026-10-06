from radar.agent.code_index import index_typescript

ROOTS = {"openai": "npm:openai", "@modelcontextprotocol/sdk": "npm:@modelcontextprotocol/sdk"}
CODE = '''import { APIError as E, Stream } from "openai";
import * as mcp from "@modelcontextprotocol/sdk";
import other from "lodash";
// E.code in a comment
const s = "E.code in a string";
if (err instanceof E) { log(E.code); }
const srv = new mcp.Server({});
Stream.fromSSE(x);
'''


def test_typescript_named_namespace_imports_without_comments_or_strings():
    usages = index_typescript({"src/a.ts": CODE}, ROOTS)
    got = {(u["line"], u["symbol"], u["package"]) for u in usages}
    assert (6, "APIError.code", "npm:openai") in got
    assert (7, "Server", "npm:@modelcontextprotocol/sdk") in got
    assert (8, "Stream.fromSSE", "npm:openai") in got
    assert not any(u["line"] in (4, 5) for u in usages)
    assert not any("lodash" in u["package"] for u in usages)


def test_unparseable_or_non_ts_files_are_skipped():
    assert index_typescript({"x.py": "import openai", "y.ts": "@@@ ((("}, ROOTS) == []


def _syms(code):
    return {(u["line"], u["symbol"]) for u in index_typescript({"a.ts": code}, ROOTS)}


def test_default_import_uses_default_convention_of_snapshots():
    got = _syms('import OpenAI from "openai";\nconst c = new OpenAI();\nc2 = OpenAI.chat.x;\n')
    assert (2, "default") in got
    assert (3, "default.chat.x") in got


def test_require_is_not_supported():
    assert _syms('const o = require("openai");\no.APIError.code;\n') == set()


def test_subpath_import_does_not_match_root():
    assert _syms('import { X } from "openai/resources";\nX.y;\n') == set()
    roots = {"openai/resources": "npm:openai"}
    got = {(u["line"], u["symbol"]) for u in index_typescript({"a.ts": 'import { X } from "openai/resources";\nX.y;\n'}, roots)}
    assert got == {(2, "X.y")}


def test_multibyte_before_import_keeps_blanking_aligned():
    code = '// héllo wörld ✓ 日本語\nimport { Stream } from "openai";\nconst é = "Stream.no"; Stream.fromSSE(x); // Stream.no\n'
    assert _syms(code) == {(3, "Stream.fromSSE")}


def test_template_string_alias_ignored_but_interpolation_kept():
    got = _syms('import { Stream } from "openai";\nconst t = `Stream.no ${Stream.yes}`;\nconst u = `Stream.no2`;\n')
    assert (3, "Stream.no2") not in got and (2, "Stream.no") not in got
    assert (2, "Stream.yes") in got
