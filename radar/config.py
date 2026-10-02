"""Tracked sources and the canonical entity registry, both read from TOML (stdlib, no extra dependency)."""
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).parent
TYPES = ("github_releases", "feed", "arxiv", "hn", "mcp_registry")
TIERS = ("official", "community")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_PACKAGE = re.compile(r"(pypi|npm):[@A-Za-z0-9_./-]+")


@dataclass(frozen=True)
class Source:
    id: str
    type: str
    tier: str
    repo: str = ""
    package: str = ""
    module: str = ""      # Python import path of the package, e.g. "llama_index.core"
    url: str = ""
    api: bool = False
    options: dict = field(default_factory=dict)


def load_sources(path=None) -> list[Source]:
    data = tomllib.loads(Path(path or HERE / "sources.toml").read_text(encoding="utf-8"))
    sources, seen = [], set()
    for raw in data.get("source", []):
        raw = dict(raw)
        sid = raw.get("id", "")
        if not sid or sid in seen:
            raise ValueError(f"missing or duplicate source id: {sid!r}")
        seen.add(sid)
        if raw.get("type") not in TYPES:
            raise ValueError(f"{sid}: unknown source type {raw.get('type')!r}")
        if raw.get("tier") not in TIERS:
            raise ValueError(f"{sid}: tier must be one of {TIERS}")
        known = {k: raw.pop(k) for k in ("id", "type", "tier", "repo", "package", "module", "url", "api") if k in raw}
        source = Source(**known, options=raw)
        if source.type == "github_releases" and (not _REPO.fullmatch(source.repo) or ".." in source.repo):
            raise ValueError(f"{sid}: github_releases needs a valid owner/name repo")
        if source.package and not _PACKAGE.fullmatch(source.package):
            raise ValueError(f"{sid}: package must look like pypi:name or npm:name")
        if source.api and not source.package:
            raise ValueError(f"{sid}: api = true needs a package")
        if source.api and source.package.startswith("pypi:") and not source.module:
            raise ValueError(f"{sid}: a pypi package with api = true needs its import module")
        if source.type == "feed" and not source.url.startswith("https://"):
            raise ValueError(f"{sid}: a feed needs an https url")
        sources.append(source)
    return sources


@dataclass(frozen=True)
class Entity:
    id: str
    name: str
    kind: str
    aliases: tuple[str, ...]
    stack: bool


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def alias_key(text: str) -> str:
    return _key(text)


class Registry:
    def __init__(self, entities: list[Entity]):
        self.entities = list(entities)
        self._by_key = {}
        self._patterns = {}
        for e in self.entities:
            labels = [label for label in (e.id, e.name, *e.aliases) if len(label) >= 3]
            for label in (e.id, e.name, *e.aliases):
                self._by_key.setdefault(_key(label), e.id)
            if labels:
                alternatives = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
                self._patterns[e.id] = re.compile(rf"(?<![A-Za-z0-9])(?:{alternatives})(?![A-Za-z0-9])", re.I)

    def mentions(self, text: str) -> list[str]:
        return [e.id for e in self.entities if e.id in self._patterns and self._patterns[e.id].search(text)]

    def with_aliases(self, mapping: dict[str, str]) -> "Registry":
        """A copy that also resolves the owner's aliases; aliases pointing at unknown entities are ignored."""
        known = {e.id for e in self.entities}
        merged = Registry(self.entities)
        merged._by_key.update({key: entity for key, entity in mapping.items() if entity in known})
        return merged

    def resolve(self, text: str) -> str | None:
        return self._by_key.get(_key(text))

    def stack_tags(self) -> list[str]:
        return [e.id for e in self.entities if e.stack]


def load_entities(path=None) -> Registry:
    data = tomllib.loads(Path(path or HERE / "entities.toml").read_text(encoding="utf-8"))
    return Registry([Entity(e["id"], e["name"], e["kind"], tuple(e.get("aliases", ())), bool(e.get("stack", False)))
                     for e in data.get("entity", [])])
