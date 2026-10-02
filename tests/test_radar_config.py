import pytest

from radar.config import Registry, Entity, load_entities, load_sources

GOOD = '''
[[source]]
id = "gh-x"
type = "github_releases"
tier = "official"
repo = "a/x"
package = "pypi:x"
module = "x"
api = true
'''


def write(tmp_path, text):
    path = tmp_path / "sources.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_shipped_sources_load_and_cover_the_spec(tmp_path):
    sources = load_sources()
    ids = [s.id for s in sources]
    assert len(ids) == len(set(ids))
    assert sum(1 for s in sources if s.type == "github_releases") >= 14
    assert {"hn", "arxiv", "mcp_registry"} <= {s.type for s in sources}
    assert all(s.module for s in sources if s.api and s.package.startswith("pypi:"))


def test_valid_source_parses(tmp_path):
    (source,) = load_sources(write(tmp_path, GOOD))
    assert (source.id, source.repo, source.package, source.module, source.api) == ("gh-x", "a/x", "pypi:x", "x", True)


@pytest.mark.parametrize("old, new, message", [
    ('type = "github_releases"', 'type = "teleport"', "unknown source type"),
    ('tier = "official"', 'tier = "famous"', "tier"),
    ('repo = "a/x"', 'repo = "../x"', "repo"),
    ('package = "pypi:x"', 'package = "cargo:x"', "package"),
    ('module = "x"', 'module = ""', "module"),
])
def test_invalid_sources_are_rejected(tmp_path, old, new, message):
    assert old in GOOD
    with pytest.raises(ValueError, match=message):
        load_sources(write(tmp_path, GOOD.replace(old, new)))


def test_duplicate_ids_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        load_sources(write(tmp_path, GOOD + GOOD))


def test_registry_resolves_ids_names_and_aliases_loosely():
    registry = Registry([Entity("langgraph", "LangGraph", "framework", ("lang graph",), True),
                         Entity("mcp", "Model Context Protocol", "protocol", ("MCP",), True),
                         Entity("rag", "RAG", "concept", (), False)])
    assert registry.resolve("LangGraph") == "langgraph" and registry.resolve("lang-graph") == "langgraph"
    assert registry.resolve("model context protocol") == "mcp" and registry.resolve("mcp") == "mcp"
    assert registry.resolve("unknown thing") is None
    assert registry.stack_tags() == ["langgraph", "mcp"]


def test_shipped_entities_load():
    registry = load_entities()
    assert registry.resolve("LangGraph") == "langgraph" and "mcp" in registry.stack_tags()


def test_mentions_finds_whole_word_entities_in_registry_order():
    registry = Registry([Entity("langchain", "LangChain", "framework", (), True),
                         Entity("langgraph", "LangGraph", "framework", ("lang graph",), True),
                         Entity("rag", "RAG", "concept", (), False)])
    assert registry.mentions("Moved from LangChain to Lang Graph") == ["langchain", "langgraph"]
    assert registry.mentions("a paragraph about fragments") == []
    assert registry.mentions("RAG pipelines") == ["rag"]
