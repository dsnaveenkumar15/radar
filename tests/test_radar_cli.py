import json

from core.worker import JobType, PermanentError
from radar.cli import main


def test_collect_command_runs_the_job_and_prints_the_stage_output(tmp_path, capsys):
    def build(conn, use_llm, data_root):
        return {"radar.collect": JobType("radar", [("collect", lambda job, out: {"new": 3})])}
    code = main(["collect", "--db", str(tmp_path / "t.db"), "--no-llm"], build=build)
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["result"] == "done" and out["stages"] == {"collect": {"new": 3}}


def test_failing_job_exits_nonzero_and_reports_the_error(tmp_path, capsys):
    def boom(job, out):
        raise PermanentError("bad config")

    def build(conn, use_llm, data_root):
        return {"radar.digest": JobType("radar", [("prepare", boom)])}
    code = main(["digest", "--db", str(tmp_path / "t.db"), "--date", "2026-10-02"], build=build)
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["result"] == "dead" and "bad config" in out["error"]
