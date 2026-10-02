import pytest

from radar.api_ts import snapshot_typescript

FILES = {
    "package.json": '{"name":"@acme/sdk","types":"./dist/index.d.ts"}',
    "dist/index.d.ts": (
        'export * from "./agent";\n'
        'export { helper, util as tool } from "./helpers";\n'
        "export declare const VERSION: string;\n"
        "/** @deprecated use run2 */\n"
        "export declare function run(a: number, b?: string, ...rest: any[]): void;\n"
        "export interface Options { model: string; temperature?: number; call(x: number): void; }\n"
        'export type Mode = "a" | "b";\n'
        "export default function main(): void;\n"
        "declare function hiddenFn(z: number): void;\n"
        "export { hiddenFn as exposed };\n"),
    "dist/agent.d.ts": (
        "export declare class Agent<T> {\n"
        "  constructor(opts: Options, extra?: number);\n"
        "  run(input: string, flag?: boolean): Promise<void>;\n"
        "  static create(x: number): Agent<any>;\n"
        "  private secret(y: number): void;\n"
        "  protected guarded(): void;\n"
        "  name: string;\n"
        "  readonly id?: number;\n"
        "  get size(): number;\n"
        "}\n"),
    "dist/helpers.d.ts": ("export declare function helper(a: string): void;\n"
                          "export declare function util(): void;\n"
                          "export declare function notExported(): void;\n"),
}


def test_exports_are_collected_through_star_named_and_local_export_lists():
    s = snapshot_typescript(FILES)["symbols"]
    assert {"Agent", "Agent.constructor", "Agent.run", "Agent.create", "Agent.name", "Agent.id", "Agent.size"} <= set(s)
    assert {"helper", "tool", "VERSION", "run", "Options", "Mode", "default", "exposed"} <= set(s)
    assert not {"util", "notExported", "hiddenFn", "Agent.secret", "Agent.guarded"} & set(s)


def test_signatures_optionality_and_deprecation():
    s = snapshot_typescript(FILES)["symbols"]
    assert s["run"] == {"kind": "function", "deprecated": True, "params": [
        {"name": "a", "kind": "pos_only", "default": False}, {"name": "b", "kind": "pos_only", "default": True},
        {"name": "rest", "kind": "var_pos", "default": False}]}
    assert s["Agent.constructor"]["params"][1]["default"] is True
    assert s["Agent.create"]["static"] is True and s["Agent.run"]["static"] is False
    assert s["Agent.id"] == {"kind": "property", "optional": True, "static": False}
    assert s["Options.temperature"]["optional"] is True and s["Options.model"]["optional"] is False
    assert s["Options.call"]["params"][0]["name"] == "x" and s["Mode"]["kind"] == "type"
    assert s["VERSION"]["kind"] == "constant" and s["default"]["kind"] == "function"


def test_package_prefix_is_stripped_and_entry_falls_back():
    files = {f"package/{k}": v for k, v in FILES.items()}
    assert snapshot_typescript(files)["entries"] == {".": "dist/index.d.ts"}
    fallback = {"index.d.ts": "export declare function f(a: number): void;\n"}
    assert "f" in snapshot_typescript(fallback)["symbols"]
    via_exports = {"package.json": '{"exports": {".": {"import": {"types": "./lib/x.d.ts"}}}}',
                   "lib/x.d.ts": "export declare function g(): void;\n"}
    assert snapshot_typescript(via_exports)["entries"] == {".": "lib/x.d.ts"}


def test_subpath_only_packages_are_keyed_by_subpath():
    files = {"package.json": '{"exports": {".": {"types": "./dist/missing.d.ts"}, "./client": {"types": "./dist/client.d.ts"},'
                             ' "./server": {"import": {"types": "./dist/server.d.ts"}}, "./gone": {"types": "./dist/gone.d.ts"},'
                             ' "./util/*": {"types": "./dist/util/*.d.ts"}, "./hidden": null}}',
             "dist/client.d.ts": "export declare class Client {\n  connect(url: string): void;\n}\n",
             "dist/server.d.ts": "export declare function serve(port: number): void;\n"}
    result = snapshot_typescript(files)
    assert result["entries"] == {"./client": "dist/client.d.ts", "./server": "dist/server.d.ts"}
    assert set(result["symbols"]) == {"./client#Client", "./client#Client.connect", "./server#serve"}


def test_missing_entry_is_a_value_error():
    with pytest.raises(ValueError, match="entry point"):
        snapshot_typescript({"package.json": "{}", "other.d.ts": "export declare const x: number;"})


def test_export_cycles_terminate():
    files = {"index.d.ts": 'export * from "./a";\nexport declare const root: number;\n',
             "a.d.ts": 'export * from "./b";\nexport declare const a: number;\n',
             "b.d.ts": 'export * from "./a";\nexport declare const b: number;\n'}
    assert {"root", "a", "b"} <= set(snapshot_typescript(files)["symbols"])
