"""
CLOUD TEST 3 - Prove it really ran in AWS, in parallel, by reading the
container's own CloudWatch output.

This is the "where do the logs go when I use invoke?" script. When the
agent is invoked on AgentCore Runtime, everything the container prints to
stdout is shipped to CloudWatch Logs at:

    /aws/bedrock-agentcore/runtimes/<agent-id>-DEFAULT

with the OpenTelemetry traces/spans going to the "otel-rt-logs" stream in
the same group. This script fetches the runtime log lines for the run that
script 01 just performed and shows:

  * that the [iteration N] lines came from inside AWS, not from a laptop
  * which worker handled which file
  * the t= timestamps overlapping, which is what parallel execution looks
    like (with one worker the timestamps are strictly sequential instead)

Usage:
    python cloud_tests/03_show_runtime_logs.py
    python cloud_tests/03_show_runtime_logs.py --all     # every line, not just agent lines
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _common as C

STATE_FILE = Path(__file__).resolve().parent / ".last_run.json"

AGENT_LINE = re.compile(r"\[t=\s*([0-9.]+)s\]\s*(.*)")
ITERATION_LINE = re.compile(r"\[iteration (\d+)\] worker (\d+) (processing|\S+) (\S+)")


def load_state():
    if not STATE_FILE.exists():
        raise SystemExit(
            f"{C.FAIL} No {STATE_FILE.name} found.\n"
            f"        Run this first:  python cloud_tests/01_invoke_cloud_agent.py"
        )
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Show the deployed agent's CloudWatch runtime logs.")
    parser.add_argument("--all", action="store_true", help="print every log line, not just the agent's own")
    args = parser.parse_args()

    config = C.load_config()
    state = load_state()
    region = state.get("region", config["AWS_REGION"])
    agent_id = state["agent_id"]
    since_ms = state["invoked_at_ms"]

    group = C.runtime_log_group(agent_id)

    C.banner("CLOUD TEST 3 - the hosted container's own CloudWatch output")
    print(f"{C.INFO} Log group   : {group}")
    print(f"{C.INFO} Console     : CloudWatch -> Log groups -> {group}")
    print(f"{C.INFO} CLI         : aws logs tail {group} --since 15m --follow")
    print(f"{C.INFO} Traces      : same log group, log stream 'otel-rt-logs'")
    print(f"{C.INFO} Dashboard   : https://console.aws.amazon.com/cloudwatch/home?region={region}#gen-ai-observability/agent-core")
    print(f"{C.INFO} Fetching lines since {datetime.fromtimestamp(since_ms / 1000, timezone.utc):%Y-%m-%d %H:%M:%SZ}...")

    events, _ = C.fetch_runtime_logs(region, agent_id, since_ms)
    if not events:
        print(
            f"\n{C.FAIL} No log events found yet. CloudWatch can lag ~30s behind the container.\n"
            f"        Wait a moment and re-run, or check the log group in the console."
        )
        return 1

    streams = sorted({e.get("logStreamName", "?") for e in events})
    print(f"{C.INFO} {len(events)} event(s) across {len(streams)} log stream(s)")
    for s in streams[:5]:
        print(f"         stream: {s}")

    C.banner("AGENT OUTPUT AS RECORDED BY AWS")
    agent_lines = []
    for event in events:
        message = event["message"].rstrip("\n")
        match = AGENT_LINE.search(message)
        if match:
            agent_lines.append((float(match.group(1)), match.group(2), message))
            print(message)
        elif args.all:
            print(message)

    # --- Show the parallelism, rather than just claiming it ---------------
    C.banner("PARALLELISM EVIDENCE")

    per_worker = defaultdict(list)
    starts = {}
    for elapsed, text, _raw in agent_lines:
        m = ITERATION_LINE.search(text)
        if not m:
            continue
        iteration, worker, verb, tail = m.group(1), int(m.group(2)), m.group(3), m.group(4)
        if verb == "processing":
            starts[iteration] = (elapsed, worker, tail)
        else:
            # On a completion line the file name is `verb`, not `tail` -
            # `tail` is the "->" arrow. Take the name from the matching
            # start line either way, which is always right.
            start = starts.get(iteration)
            if start:
                per_worker[worker].append((start[0], elapsed, start[2], iteration))

    if not per_worker:
        print(f"{C.INFO} No completed [iteration N] pairs found in this window.")
        return 0

    workers = sorted(per_worker)
    print(f"{C.INFO} {len(workers)} worker thread(s) did the work: {workers}")
    print()
    print(f"{'worker':>7}  {'iter':>5}  {'start':>7}  {'end':>7}  {'secs':>6}  file")
    print("-" * 74)
    spans = []
    for worker in workers:
        for start, end, file_name, iteration in sorted(per_worker[worker]):
            print(f"{worker:>7}  {iteration:>5}  {start:>6.2f}s  {end:>6.2f}s  {end - start:>5.2f}s  {file_name}")
            spans.append((start, end, worker))

    # Count the maximum number of files being processed at the same instant.
    boundaries = sorted({t for span in spans for t in span[:2]})
    peak = 0
    for t in boundaries:
        overlapping = sum(1 for s, e, _ in spans if s <= t < e)
        peak = max(peak, overlapping)

    total_work = sum(e - s for s, e, _ in spans)
    wall = max(e for _, e, _ in spans) - min(s for s, _, _ in spans)

    print()
    results = []
    ok = C.check(
        peak > 1,
        f"files were genuinely processed at the same time (peak concurrency: {peak})",
        "peak concurrency of 1 means only one file was ever in flight - seed more files",
    )
    results.append((ok, "peak concurrency above 1"))

    if wall > 0:
        print(
            f"{C.INFO} {total_work:.1f}s of file work finished in {wall:.1f}s of wall clock "
            f"= {total_work / wall:.1f}x compression from running in parallel"
        )

    return C.exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
