# radar/agent/verify.py
"""The gate between the model and the visitor: every confirmed finding must quote the real file at the cited line and
point at a change Radar actually recorded. Code, not a model, decides."""
import posixpath

MIN_QUOTE = 6
NEAR = 3


def _check(finding: dict, files: dict, by_id: dict) -> str:
    if finding.get("verdict") != "confirmed":
        return "not a confirmed finding"
    if finding.get("candidate_id") not in by_id:
        return "refers to no known candidate"
    if not finding.get("evidence"):
        return "no evidence quoted"
    for ev in finding["evidence"]:
        path = str(ev.get("path", ""))
        if path != posixpath.normpath(path) or path.startswith(("/", "..")) or path not in files:
            return f"{path!r} is not in the snapshot"
        lines = files[path].splitlines()
        line = int(ev.get("line") or 0)
        quote = str(ev.get("quote", "")).strip()
        if not 1 <= line <= len(lines):
            return f"line {line} does not exist in {path}"
        if len(quote) < MIN_QUOTE or quote not in lines[line - 1]:
            return f"the quote is not on line {line} of {path}"
    usage = by_id[finding["candidate_id"]]["usage"]
    own = usage["path"]
    if own not in {str(ev.get("path", "")) for ev in finding["evidence"]}:
        return f"no evidence from the candidate's own file ({own})"
    if not any(str(ev.get("path", "")) == own and abs(int(ev.get("line") or 0) - usage["line"]) <= NEAR for ev in finding["evidence"]):
        return f"no evidence near the usage (line {usage['line']} of {own})"
    return ""


def verify(findings: list[dict], files: dict, candidates: list[dict]) -> list[dict]:
    by_id = {c["id"]: c for c in candidates}
    out = []
    for f in findings:
        note = _check(f, files, by_id)
        out.append({**f, "verified": note == "", "verify_note": note})
    return out
