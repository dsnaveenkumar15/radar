"""Keyword search over markdown notes. Plain Python: nothing here depends on an AI library."""
from pathlib import Path

NOTES = Path("notes")


def search_notes(phrase: str) -> str:
    hits = []
    for path in sorted(NOTES.glob("*.md")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if phrase.lower() in line.lower():
                hits.append(f"{path.name}:{number}: {line.strip()}")
    return "\n".join(hits[:5]) or "no matching notes"
