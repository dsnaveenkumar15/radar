# radar/agent/tools.py
"""Read-only tools the investigator may call. They only see the fetched snapshot and Radar's data: no web, no writes,
no code execution. Tool errors are returned as text so a bad call costs one turn, not the run."""
import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

MAX_LINES = 60


class ReadLines(BaseModel):
    """Read numbered lines of a file in the repo snapshot (at most 60 lines)."""
    path: str
    start: int = Field(ge=1)
    end: int = Field(ge=1)


class FindUsages(BaseModel):
    """List every place in the repo that uses a library symbol (exact dotted or export path)."""
    symbol: str


class GetChange(BaseModel):
    """Get Radar's recorded API change for a candidate: what changed, between which versions."""
    candidate_id: str


class GetMigrationNote(BaseModel):
    """Get migration notes Radar has published for a package, if any."""
    package: str


class EvidenceItem(BaseModel):
    path: str
    line: int = Field(ge=1)
    quote: str = Field(min_length=3, max_length=400)


class SubmitVerdict(BaseModel):
    """Finish this candidate: confirmed (the code breaks) or dismissed (it does not), with exact quoted evidence."""
    verdict: Literal["confirmed", "dismissed"]
    explanation: str = Field(max_length=800)
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=5)


TOOLS = [ReadLines, FindUsages, GetChange, GetMigrationNote, SubmitVerdict]
_SCHEMAS = {t.__name__: t for t in TOOLS}


class Toolbox:
    def __init__(self, files: dict, usages: list[dict], candidates: list[dict], migrations: dict):
        self.files, self.usages, self.migrations = files, usages, migrations
        self.by_id = {c["id"]: c for c in candidates}

    def run(self, name: str, args: dict) -> str:
        schema = _SCHEMAS.get(name)
        if schema is None or name == "SubmitVerdict":
            return f"unknown tool {name!r}"
        try:
            a = schema.model_validate(args)
        except ValidationError as exc:
            return f"invalid arguments: {exc.errors()[0]['msg']}"
        if name == "ReadLines":
            text = self.files.get(a.path)
            if text is None:
                return f"{a.path!r} is not in the snapshot"
            lines = text.splitlines()
            end = min(a.end, a.start + MAX_LINES - 1, len(lines))
            return "\n".join(f"{n}: {lines[n - 1]}" for n in range(a.start, end + 1)) or "(no lines)"
        if name == "FindUsages":
            hits = [u for u in self.usages if u["symbol"] == a.symbol or u["symbol"].startswith(a.symbol + ".")]
            return "\n".join(f"{u['path']}:{u['line']}: {u['text']}" for u in hits[:30]) or "no usages"
        if name == "GetChange":
            c = self.by_id.get(a.candidate_id)
            return json.dumps(c["change"]) if c else f"no candidate {a.candidate_id!r}"
        notes = self.migrations.get(a.package) or []
        return json.dumps(notes[:5]) if notes else "no migration notes"
