# tests/test_radar_agent_deps.py
from radar.agent.deps import merge, normalize_pypi, parse_file
from radar.agent.types import Dependency


def by_name(deps):
    return {d.name: d for d in deps}


def test_requirements_exact_pins_and_ranges():
    deps = by_name(parse_file("requirements.txt", "LangChain_Core==0.2.14\nopenai>=1.0\n# c\n-r other.txt\nanthropic[vertex]==0.30.1 ; python_version>'3.8'\n"))
    assert deps["langchain-core"].version == "0.2.14" and deps["openai"].version is None
    assert deps["anthropic"].version == "0.30.1"


def test_pyproject_pep621_and_poetry():
    text = '[project]\ndependencies = ["langgraph==0.2.20", "mcp>=1"]\n[tool.poetry.dependencies]\npython="^3.11"\nollama = "0.3.1"\n'
    deps = by_name(parse_file("pyproject.toml", text))
    assert deps["langgraph"].version == "0.2.20" and deps["mcp"].version is None and deps["ollama"].version == "0.3.1"
    assert "python" not in deps


def test_package_json_exact_and_ranges():
    text = '{"dependencies": {"openai": "4.20.0", "@modelcontextprotocol/sdk": "^1.0.0"}, "devDependencies": {"@anthropic-ai/sdk": "=0.20.1"}}'
    deps = by_name(parse_file("web/package.json", text))
    assert deps["openai"].version == "4.20.0" and deps["@modelcontextprotocol/sdk"].version is None
    assert deps["@anthropic-ai/sdk"].version == "0.20.1" and deps["openai"].ecosystem == "npm"


def test_lock_files():
    lock = '{"lockfileVersion": 3, "packages": {"": {}, "node_modules/openai": {"version": "4.20.1"}, "node_modules/a/node_modules/openai": {"version": "3.0.0"}}}'
    assert by_name(parse_file("package-lock.json", lock))["openai"].version == "4.20.1"
    poetry = '[[package]]\nname = "langchain"\nversion = "0.1.20"\n'
    assert by_name(parse_file("poetry.lock", poetry))["langchain"].version == "0.1.20"
    assert by_name(parse_file("uv.lock", poetry))["langchain"].version == "0.1.20"
    yarn = '"openai@^4.0.0":\n  version "4.22.0"\n  resolved "x"\n'
    assert by_name(parse_file("yarn.lock", yarn))["openai"].version == "4.22.0"


def test_unknown_or_broken_files_give_nothing():
    assert parse_file("README.md", "x") == [] and parse_file("package.json", "{not json") == []


def test_merge_prefers_exact_versions_and_picks_the_oldest_conflicting_pin():
    deps, notes = merge([
        Dependency("pypi", "openai", None, "openai>=1", "requirements.txt"),
        Dependency("pypi", "openai", "1.30.0", "openai==1.30.0", "poetry.lock"),
        Dependency("pypi", "langchain", "0.2.0", "langchain==0.2.0", "a/requirements.txt"),
        Dependency("pypi", "langchain", "0.1.0", "langchain==0.1.0", "b/requirements.txt"),
    ])
    got = by_name(deps)
    assert got["openai"].version == "1.30.0" and got["langchain"].version == "0.1.0"
    assert any("langchain" in n and "0.1.0" in n and "0.2.0" in n for n in notes)


def test_malformed_manifests_are_skipped_not_fatal():
    for path, text in [("package.json", "[]"), ("package.json", '{"dependencies": ["a"]}'), ("pyproject.toml", "project = 1"),
                       ("package-lock.json", '{"packages": []}'), ("poetry.lock", "package = 3")]:
        assert parse_file(path, text) == [], (path, text)
