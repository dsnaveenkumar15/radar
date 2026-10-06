# tests/test_radar_agent_tools_verify.py
from radar.agent.tools import TOOLS, Toolbox
from radar.agent.verify import verify

FILES = {"app/main.py": "import x\nt = x.old()\nprint(t)\n"}
CANDS = [{"id": "c1", "change": {"package": "pypi:x", "symbol": "x.old", "kind": "breaking", "detail": "removed", "from": "1", "to": "2"},
          "usage": {"path": "app/main.py", "line": 2, "symbol": "x.old", "package": "pypi:x", "text": "t = x.old()"}}]


def test_toolbox_reads_lines_with_numbers_and_refuses_unknown_paths():
    box = Toolbox(FILES, [CANDS[0]["usage"]], CANDS, {"pypi:x": [{"text": "use x.new"}]})
    assert box.run("ReadLines", {"path": "app/main.py", "start": 2, "end": 3}) == "2: t = x.old()\n3: print(t)"
    assert "not in the snapshot" in box.run("ReadLines", {"path": "../etc/passwd", "start": 1, "end": 2})
    assert "x.old" in box.run("FindUsages", {"symbol": "x.old"}) and "removed" in box.run("GetChange", {"candidate_id": "c1"})
    assert "x.new" in box.run("GetMigrationNote", {"package": "pypi:x"})
    assert "unknown tool" in box.run("Nope", {}) and {t.__name__ for t in TOOLS} >= {"ReadLines", "SubmitVerdict"}


def test_verifier_accepts_exact_quotes_and_rejects_invented_or_traversal_paths():
    good = {"candidate_id": "c1", "verdict": "confirmed", "explanation": "e", "evidence": [{"path": "app/main.py", "line": 2, "quote": "t = x.old()"}]}
    wrong_line = {**good, "evidence": [{"path": "app/main.py", "line": 3, "quote": "t = x.old()"}]}
    invented = {**good, "evidence": [{"path": "app/other.py", "line": 1, "quote": "t = x.old()"}]}
    traversal = {**good, "evidence": [{"path": "../app/main.py", "line": 2, "quote": "t = x.old()"}]}
    unknown = {**good, "candidate_id": "c9"}
    dismissed = {**good, "verdict": "dismissed"}
    out = verify([good, wrong_line, invented, traversal, unknown, dismissed], FILES, CANDS)
    assert [f["verified"] for f in out] == [True, False, False, False, False, False]
    assert "line 3" in out[1]["verify_note"] and "not in the snapshot" in out[2]["verify_note"]


def test_evidence_must_include_the_candidates_own_file():
    files = {**FILES, "app/other.py": "t = x.old()\n"}
    f = {"candidate_id": "c1", "verdict": "confirmed", "explanation": "e",
         "evidence": [{"path": "app/other.py", "line": 1, "quote": "t = x.old()"}]}
    out = verify([f], files, CANDS)[0]
    assert not out["verified"] and "candidate's own file" in out["verify_note"]


def test_quote_must_be_near_the_usage_not_anywhere_in_the_file():
    files = {"app/main.py": "import x\n" + "pad = 1\n" * 10 + "t = x.old()\n"}
    cands = [{"id": "c1", "change": CANDS[0]["change"], "usage": {**CANDS[0]["usage"], "line": 12, "text": "t = x.old()"}}]
    far = {"candidate_id": "c1", "verdict": "confirmed", "explanation": "e", "evidence": [{"path": "app/main.py", "line": 1, "quote": "import x"}]}
    near = {**far, "evidence": [{"path": "app/main.py", "line": 12, "quote": "t = x.old()"}]}
    out = verify([far, near], files, cands)
    assert [f["verified"] for f in out] == [False, True] and "near the usage" in out[0]["verify_note"]
