"""
Read CloudWatch log entries for a Lambda's most recent invocation.

Used by agent.py to get the log TEXT for L1/L2 so the LLM can form its own
plain-language conclusion (CLAUDE.md Section 3.3, steps 5 and 7). This
module never makes pass/fail decisions itself - it only returns raw text
for the LLM to read.

Assumption: the agent processes one file at a time (per CLAUDE.md's
control-flow rule), so "the most recent log stream" reliably means "the
invocation we just made." This would not hold under concurrent invocations.
"""

import time

import boto3


def get_log_text_for_request(function_name, request_id, region, wait_seconds=3, max_attempts=5, streams_to_check=5):
    """Return the exact CloudWatch log text for one specific Lambda
    invocation, identified by its request_id (from context.aws_request_id,
    which L1/L2 include in their structured output). This is precise -
    unlike get_latest_log_text() below, it can't be confused by a
    concurrent or out-of-order invocation.

    Checks the most recent log streams directly (raw stream reads) rather
    than filter_log_events - filter_log_events goes through CloudWatch's
    search index, which lags behind raw log storage by more than the couple
    of seconds a direct describe_log_streams/get_log_events read needs."""
    logs = boto3.client("logs", region_name=region)
    log_group = f"/aws/lambda/{function_name}"

    # CloudWatch Logs can lag a couple seconds behind the Lambda finishing.
    time.sleep(wait_seconds)

    for _ in range(max_attempts):
        try:
            streams = logs.describe_log_streams(
                logGroupName=log_group,
                orderBy="LastEventTime",
                descending=True,
                limit=streams_to_check,
            )["logStreams"]
        except logs.exceptions.ResourceNotFoundException:
            streams = []

        for stream in streams:
            events = logs.get_log_events(
                logGroupName=log_group,
                logStreamName=stream["logStreamName"],
                limit=200,
                startFromHead=False,
            )["events"]
            lines = [e["message"].rstrip("\n") for e in events]
            invocation_text = _extract_invocation(lines, request_id)
            if invocation_text is not None:
                return invocation_text

        time.sleep(2)

    return ""


def _extract_invocation(lines, request_id):
    """Lambda reuses warm execution environments across calls, so one log
    stream can hold several invocations back to back. Slice out just the
    START...REPORT block for this one request_id, so the LLM (and the
    learner reading LOG) sees only the single relevant invocation."""
    marker = f"RequestId: {request_id}"

    start_idx = next(
        (i for i, line in enumerate(lines) if line.startswith("START") and marker in line),
        None,
    )
    if start_idx is None:
        return None

    end_idx = next(
        (i for i in range(start_idx, len(lines)) if lines[i].startswith("REPORT") and marker in lines[i]),
        None,
    )
    if end_idx is None:
        end_idx = next(
            (i for i in range(start_idx, len(lines)) if lines[i].startswith("END") and marker in lines[i]),
            start_idx,
        )

    return "\n".join(lines[start_idx : end_idx + 1])


def get_latest_log_text(function_name, region, wait_seconds=3, max_attempts=5):
    """Return the concatenated log message text from the most recent
    CloudWatch log stream for the given Lambda function."""
    logs = boto3.client("logs", region_name=region)
    log_group = f"/aws/lambda/{function_name}"

    # CloudWatch Logs can lag a couple seconds behind the Lambda finishing.
    time.sleep(wait_seconds)

    streams = []
    for _ in range(max_attempts):
        try:
            streams = logs.describe_log_streams(
                logGroupName=log_group,
                orderBy="LastEventTime",
                descending=True,
                limit=1,
            )["logStreams"]
        except logs.exceptions.ResourceNotFoundException:
            streams = []

        if streams:
            break
        time.sleep(2)

    if not streams:
        return ""

    stream_name = streams[0]["logStreamName"]
    events = logs.get_log_events(
        logGroupName=log_group,
        logStreamName=stream_name,
        limit=50,
        startFromHead=False,
    )["events"]

    lines = [e["message"].rstrip("\n") for e in events]
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    def load_config(path="config.env"):
        config = {}
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, _, value = line.partition("=")
                config[key.strip()] = value.strip()
        return config

    config = load_config()
    function_name = sys.argv[1] if len(sys.argv) > 1 else "agentcore-demo-l1-copy"
    text = get_latest_log_text(function_name, config["AWS_REGION"])
    print(f"--- Latest log for {function_name} ---")
    print(text)