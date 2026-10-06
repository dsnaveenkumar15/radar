"""Run one Radar job by hand: collect | apisurface | backfill | advisories | digest. Prints the stage outputs as JSON."""
import argparse
import json
import os
import time

from core.db import connect
from core.jobs import enqueue
from core.worker import run_once
from radar import jobs as radar_jobs
from radar.digest import digest_date

COMMANDS = {"collect": radar_jobs.COLLECT_JOB, "apisurface": radar_jobs.API_JOB,
            "backfill": radar_jobs.BACKFILL_JOB,
            "advisories": radar_jobs.ADVISORY_JOB, "digest": radar_jobs.DIGEST_JOB}


def main(argv=None, build=None) -> int:
    parser = argparse.ArgumentParser(prog="radar.cli")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--db", default=os.environ.get("DB_PATH", "data/planets.db"))
    parser.add_argument("--data-root", default=os.environ.get("DATA_ROOT", "data/published"))
    parser.add_argument("--date", help="digest date, YYYY-MM-DD (default: tonight's)")
    parser.add_argument("--no-llm", action="store_true", help="skip Claude: the digest is published unsynthesized")
    args = parser.parse_args(argv)
    conn = connect(args.db)
    types = (build or radar_jobs.build_job_types)(conn, use_llm=not args.no_llm, data_root=args.data_root)
    now = time.time()
    job_type = COMMANDS[args.command]
    payload = {"date": args.date or digest_date(now)} if args.command == "digest" else {}
    key = f"cli:{int(now * 1000)}"
    enqueue(conn, "radar", job_type, key, payload, priority=100.0, now=now)    # claimed before scheduled work
    result = run_once(conn, types, {"radar": 1.0}, "cli")
    job = conn.execute("SELECT id, state, last_error FROM jobs WHERE job_type=? AND key=?", (job_type, key)).fetchone()
    stages = {r["stage"]: json.loads(r["output"])
              for r in conn.execute("SELECT stage, output FROM stage_artifacts WHERE job_id=?", (job["id"],))}
    print(json.dumps({"result": result, "state": job["state"], "error": job["last_error"], "stages": stages},
                     indent=2, default=str))
    return 0 if result == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
