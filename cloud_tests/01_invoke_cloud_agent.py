"""
CLOUD TEST 1 - Seed B1, then invoke the DEPLOYED agent and time it.

Runs against AWS only. Nothing in agent/ is imported here: this script
seeds the source bucket, sends one InvokeAgentRuntime call (the same call
`agentcore invoke` makes), and reports what the hosted container returned.

Usage:
    python cloud_tests/01_invoke_cloud_agent.py                 # 5 files
    python cloud_tests/01_invoke_cloud_agent.py --files 20      # bigger batch
    python cloud_tests/01_invoke_cloud_agent.py --no-seed       # invoke against whatever is in B1

Writes cloud_tests/.last_run.json so scripts 02 and 03 can inspect the same run.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _common as C

STATE_FILE = Path(__file__).resolve().parent / ".last_run.json"


def main():
    parser = argparse.ArgumentParser(description="Invoke the deployed AgentCore agent.")
    parser.add_argument("--files", type=int, default=5, help="how many sample files to seed into B1")
    parser.add_argument("--workers", type=int, default=None, help="override AGENT_MAX_WORKERS for this invocation")
    parser.add_argument("--no-seed", action="store_true", help="do not touch B1, invoke as-is")
    args = parser.parse_args()

    config = C.load_config()
    C.banner("CLOUD TEST 1 - invoke the deployed AgentCore Runtime agent")
    region, agent_arn, agent_id = C.require_deployed(config)

    if args.no_seed:
        seeded = len(C.s3_keys(region, config["BUCKET_SOURCE"]))
        print(f"{C.INFO} Skipping seed - B1 currently holds {seeded} file(s).")
    else:
        seeded = C.seed_source_bucket(region, config["BUCKET_SOURCE"], args.files)
        print(f"{C.INFO} Seeded {seeded} file(s) into s3://{config['BUCKET_SOURCE']}/")

    payload = {}
    if args.workers is not None:
        payload["max_workers"] = args.workers

    # Marked before the call so script 03 knows where in CloudWatch to look.
    invoked_at_ms = int(time.time() * 1000) - 5_000

    print(f"{C.INFO} Payload       : {json.dumps(payload) or '{}'}")
    print(f"{C.INFO} Invoking now - the agent runs entirely inside AWS from here on...")

    result, elapsed, session_id = C.invoke_agent(region, agent_arn, payload)

    C.banner("RESPONSE FROM THE HOSTED AGENT")
    print(json.dumps(result, indent=2))

    print()
    print(f"{C.INFO} Session id            : {session_id}")
    print(f"{C.INFO} Wall clock (client)   : {elapsed}s")
    if isinstance(result, dict) and "elapsed_seconds" in result:
        print(f"{C.INFO} Wall clock (in AWS)   : {result['elapsed_seconds']}s")
        print(f"{C.INFO} Parallel workers used : {result.get('max_workers')}")
        print(f"{C.INFO} Reasoning mode        : {result.get('reasoning_mode')}")
        files = result.get("files") or []
        if files:
            per_file = round(result["elapsed_seconds"] / max(len(files), 1), 2)
            print(f"{C.INFO} File-passes completed : {len(files)} ({per_file}s each on average)")

    STATE_FILE.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "agent_id": agent_id,
                "agent_arn": agent_arn,
                "region": region,
                "invoked_at_ms": invoked_at_ms,
                "client_elapsed_seconds": elapsed,
                "seeded_files": seeded,
                "payload": payload,
                "result": result,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"{C.INFO} Saved run details to {STATE_FILE}")

    ok = isinstance(result, dict) and result.get("status") == "complete"
    return C.exit_code([(ok, "hosted agent returned status=complete")])


if __name__ == "__main__":
    sys.exit(main())
