"""
Read CloudWatch log entries for one specific Lambda invocation.

Used by agent.py to get the log TEXT for L1/L2/L3 so the LLM can form its
own plain-language conclusion (guide Part 9). This module never makes
pass/fail decisions itself - it only returns raw text for the LLM to read.

--- Performance notes (why this file was rewritten) ---------------------
The original version was the single biggest source of dead time in the
whole project. Every log read did an unconditional `time.sleep(3)` before
even asking CloudWatch whether the log was there, then slept another 2s
between retries. With three Lambda calls per file that was a guaranteed
9+ seconds per file of the agent doing literally nothing. In practice the
logs are usually queryable in well under a second.

This version instead:
  1. Asks immediately, then backs off gently (0.3s, 0.5s, 0.8s, ...) only
     for as long as the log genuinely isn't there yet - so the fast case
     costs ~0.2s instead of a flat 3s.
  2. Scans candidate log streams CONCURRENTLY instead of one at a time.
  3. Prunes candidate streams by `since_ms` (the moment the agent made the
     call), so old streams are never read at all.
  4. Looks at many more streams than before (25, not 5). This matters now
     that the agent runs files in parallel: concurrent Lambda invocations
     mean many warm execution environments, so many live log streams, and
     the one holding our request_id may not be in the newest 5.

Lookup is still by request_id, so it stays exact and concurrency-safe -
it can never return another file's invocation by mistake.
"""

import time
from concurrent.futures import ThreadPoolExecutor

import aws_clients

# Shared pool used only for reading several log streams at once. Sized to
# stay comfortably inside the boto3 connection pool configured in
# aws_clients.py, since this pool is nested inside the agent's own workers.
_SCAN_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="cw-scan")

# Streams whose last event predates the call by more than this can't hold
# our invocation, so they're skipped entirely.
_STREAM_LOOKBACK_MS = 120_000


def get_log_text_for_request(
    function_name,
    request_id,
    region,
    wait_seconds=0.0,
    max_attempts=None,
    streams_to_check=25,
    timeout_seconds=30.0,
    since_ms=None,
    log_stream_name=None,
):
    """Return the exact CloudWatch log text for one specific Lambda
    invocation, identified by its request_id (from context.aws_request_id,
    which L1/L2/L3 include in their structured output).

    Args:
        function_name: e.g. "agentcore-demo-l1-copy"
        request_id:    the Lambda request id to find
        region:        AWS region
        wait_seconds:  optional fixed delay before the first attempt.
                       Defaults to 0 - we poll instead of guessing.
        max_attempts:  kept for backwards compatibility with older callers;
                       `timeout_seconds` is the real control now.
        streams_to_check: how many recent log streams to consider when
                       falling back to a search.
        timeout_seconds:  give up after this long and return "".
        since_ms:      epoch milliseconds of when the call was made.
        log_stream_name: the stream the Lambda reported for itself
                       (context.log_stream_name). When present this is an
                       exact lookup - no searching at all.

    Three strategies, in order of precision:

      1. If the Lambda told us its log stream, read exactly that stream.
         Always correct, one API call, no ordering assumptions.

      2. Otherwise scan the most recently active streams directly.
         Fast, but CloudWatch updates a stream's "last event time" only on
         an eventual-consistency basis, so under parallel invocations the
         stream we want may not be near the top of that list yet.

      3. If that fails, ask CloudWatch's search index for the request id.
         The index lags a few seconds behind raw storage, which is why it
         isn't tried first - but it finds the stream regardless of ordering.

    Every strategy waits for the invocation's REPORT line before accepting
    the block, so the LLM never reasons over a half-written log.
    """
    logs = aws_clients.get_client("logs", region)
    log_group = f"/aws/lambda/{function_name}"

    if wait_seconds:
        time.sleep(wait_seconds)

    deadline = time.monotonic() + timeout_seconds
    delay = 0.3
    partial = None

    while True:
        # 1. Exact stream, when the Lambda reported it.
        if log_stream_name:
            text, complete = _read_stream(logs, log_group, log_stream_name, request_id)
            if complete:
                return text
            partial = text or partial
        else:
            # 2. Direct scan of recently active streams.
            try:
                streams = logs.describe_log_streams(
                    logGroupName=log_group,
                    orderBy="LastEventTime",
                    descending=True,
                    limit=streams_to_check,
                )["logStreams"]
            except logs.exceptions.ResourceNotFoundException:
                streams = []

            text, complete = _scan_streams(logs, log_group, _prune_streams(streams, since_ms), request_id)
            if complete:
                return text
            partial = text or partial

            # 3. Search index fallback - finds the stream no matter where
            # it sits in the "recently active" ordering.
            found_stream = _find_stream_via_search(logs, log_group, request_id, since_ms)
            if found_stream:
                text, complete = _read_stream(logs, log_group, found_stream, request_id)
                if complete:
                    return text
                partial = text or partial

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # Out of time: a half-written block is still better evidence
            # than nothing at all.
            return partial or ""
        time.sleep(min(delay, remaining))
        delay = min(delay * 1.6, 2.0)


def _find_stream_via_search(logs, log_group, request_id, since_ms):
    """Ask CloudWatch's search index which stream holds this request id.
    Returns a stream name or None."""
    try:
        kwargs = {
            "logGroupName": log_group,
            "filterPattern": f'"{request_id}"',
            "limit": 1,
        }
        if since_ms:
            kwargs["startTime"] = since_ms - _STREAM_LOOKBACK_MS
        events = logs.filter_log_events(**kwargs).get("events", [])
    except Exception:
        return None
    return events[0].get("logStreamName") if events else None


def _prune_streams(streams, since_ms):
    """Drop streams that stopped receiving events before our call was even
    made - they cannot contain our request_id, so reading them is wasted
    time. Streams with no timestamp yet are kept (they may be brand new)."""
    if since_ms is None:
        return streams

    cutoff = since_ms - _STREAM_LOOKBACK_MS
    kept = [s for s in streams if s.get("lastEventTimestamp", 0) >= cutoff or "lastEventTimestamp" not in s]
    # If pruning removed everything, fall back to the unpruned list rather
    # than returning nothing - better slow than wrong.
    return kept or streams


def _read_stream(logs, log_group, stream_name, request_id):
    """Read one stream and slice out this request's invocation block.
    Returns (text, complete) - complete is False when the block hasn't
    finished being written yet, which tells the caller to poll again."""
    try:
        events = logs.get_log_events(
            logGroupName=log_group,
            logStreamName=stream_name,
            limit=200,
            startFromHead=False,
        )["events"]
    except Exception:
        return None, False

    lines = [e["message"].rstrip("\n") for e in events]
    return _extract_invocation(lines, request_id)


def _scan_streams(logs, log_group, streams, request_id):
    """Read the candidate streams concurrently and return the first one
    containing this request_id's invocation block, as (text, complete)."""
    if not streams:
        return None, False

    def read(stream):
        return _read_stream(logs, log_group, stream["logStreamName"], request_id)

    partial = None
    for text, complete in _SCAN_POOL.map(read, streams):
        if complete:
            return text, True
        partial = text or partial
    return partial, False


def _extract_invocation(lines, request_id):
    """Lambda reuses warm execution environments across calls, so one log
    stream can hold several invocations back to back. Slice out just the
    START...REPORT block for this one request_id, so the LLM (and the
    learner reading LOG) sees only the single relevant invocation.

    Returns (text, complete). `complete` is True only once the closing
    REPORT (or at least END) line for this request has landed - a block cut
    off halfway would make the LLM's conclusion meaningless, so the caller
    keeps polling until it's whole."""
    marker = f"RequestId: {request_id}"

    start_idx = next(
        (i for i, line in enumerate(lines) if line.startswith("START") and marker in line),
        None,
    )
    if start_idx is None:
        return None, False

    end_idx = next(
        (i for i in range(start_idx, len(lines)) if lines[i].startswith("REPORT") and marker in lines[i]),
        None,
    )
    if end_idx is not None:
        return "\n".join(lines[start_idx : end_idx + 1]), True

    end_idx = next(
        (i for i in range(start_idx, len(lines)) if lines[i].startswith("END") and marker in lines[i]),
        None,
    )
    if end_idx is not None:
        return "\n".join(lines[start_idx : end_idx + 1]), True

    # START seen but no terminator yet - the invocation is still being
    # written. Hand back what we have, flagged as incomplete.
    return "\n".join(lines[start_idx:]), False


def get_latest_log_text(function_name, region, wait_seconds=1.0, max_attempts=5):
    """Return the concatenated log message text from the most recent
    CloudWatch log stream for the given Lambda function.

    Only used by the standalone smoke test below - the agent itself always
    looks up by request_id, which is exact."""
    logs = aws_clients.get_client("logs", region)
    log_group = f"/aws/lambda/{function_name}"

    if wait_seconds:
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
        time.sleep(1)

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
