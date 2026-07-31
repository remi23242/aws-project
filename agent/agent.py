"""
Strands agent: the deterministic control loop, built around the flow that
is the most important part of the project: call a Lambda through the
Gateway, read its exact CloudWatch log, get the LLM's independent text
conclusion, compare it to the Lambda's structured output, and write all of
it to the global LOG in B3.

All deterministic decisions (which file, one-pipeline-per-file,
delete-only-after-verify) live in this Python loop, never in the LLM - the
LLM is only used to read log TEXT and form a conclusion for comparison.

Runs two ways:
  - Locally/for tests: `python agent/agent.py --run-once` runs the loop
    once and exits.
  - On AgentCore Runtime: `agentcore configure -e agent/agent.py` finds the
    `app`/`invoke` below and deploys it; `agentcore invoke` then triggers
    one full drain-B1 run per call, since this agent is deterministic and
    autonomous rather than conversational - it doesn't need prompt content,
    just a trigger to start.

=========================================================================
PERFORMANCE: why this loop is now parallel
=========================================================================
The original loop processed exactly ONE file at a time, start to finish,
and every step inside it was blocking. Per file that meant:

    Gateway call (L1)  ->  ~0.5s
    CloudWatch read    ->  a hard-coded 3s sleep, then the query
    LLM reasoning      ->  ~2-4s
    write LOG to S3    ->  a full read-modify-write of the LOG object
    ... repeat for L3 (40% of the time) and again for L2

That is ~20-30 seconds per file with almost all of it spent waiting on the
network, and five files took minutes. None of that waiting was necessary:
the files are completely independent of each other.

Three changes fix it, and they compose:

  1. FILE-LEVEL PARALLELISM (this file). A pool of workers each claim a
     different file from B1 and run the whole pipeline for it. Claiming is
     coordinated so two workers can never grab the same file. Tune with
     AGENT_MAX_WORKERS in config.env, or per-invocation with
     {"max_workers": N} in the invoke payload.

  2. REASONING OFF THE CRITICAL PATH (this file). The CloudWatch read + LLM
     conclusion + comparison for a Lambda call is handed to a background
     pool. The deterministic pipeline (copy -> maybe corrupt -> verify ->
     delete) never waits on it. This is safe precisely BECAUSE of the
     project's own control-flow rule: the delete/keep decision comes from
     the Lambda's structured `match` field, and the LLM's conclusion was
     never an input to it. Every Lambda call still gets its log read, its
     independent LLM conclusion, and its comparison written to LOG - the
     run simply doesn't idle while that happens. Set
     AGENT_REASONING_MODE=inline to restore the strictly-sequential
     behaviour for comparison.

  3. NO MORE BLIND SLEEPS AND NO MORE PER-ENTRY S3 WRITES (see
     cloudwatch.py, logbook.py, llm_wrapper.py and aws_clients.py).
"""

import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

# Always resolve sibling imports (cloudwatch, gateway_client, etc.) relative
# to this file's own directory, regardless of the working directory the
# process was started from - matters once this runs inside an AgentCore
# Runtime container instead of a local terminal.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import aws_clients
import cloudwatch
import gateway_client
import llm_wrapper
import logbook
from bedrock_agentcore import BedrockAgentCoreApp

# Chance that a given file gets deliberately corrupted via L3 after copying,
# to prove L2's verification actually catches real failures.
L3_PROBABILITY = 0.4

# Safety cap so a bug can never spin this loop forever during a live demo.
# It has to scale with the batch size: L3 corrupts ~40% of copies on purpose
# and a failed file is retried, so N files routinely costs 2-3N iterations
# (a 4-file run has been seen to take 13). A flat 50 was fine for the
# original 5-file demo but would cut a 20-file run short.
MAX_ITERATIONS = 50
ITERATIONS_PER_FILE = 15


def iteration_cap(file_count):
    """Safety cap for one run: generous per file, never below MAX_ITERATIONS."""
    return max(MAX_ITERATIONS, file_count * ITERATIONS_PER_FILE)

# How many files to process at the same time when nothing overrides it.
DEFAULT_MAX_WORKERS = 8

FUNCTION_NAMES = {
    "copy_file": "agentcore-demo-l1-copy",
    "verify_file": "agentcore-demo-l2-verify",
    "corrupt_file": "agentcore-demo-l3-corrupt",
}

# Which field in each Lambda's structured output represents pass/fail.
SUCCESS_FIELD = {
    "copy_file": "success",
    "verify_file": "match",
    "corrupt_file": "success",
}

# Wall-clock start of the current run, so print() lines can be stamped with
# an elapsed time. That stamp is what makes parallelism visible in the
# CloudWatch runtime log: several files show the same t= second.
_RUN_STARTED = time.time()

# Where the time actually goes, totalled across every thread. Printed at
# the end of a run and returned in the invoke() response, so "it's slow"
# can always be answered with a number instead of a guess. These are sums
# of concurrent work, so they add up to far more than the wall clock - that
# gap IS the parallelism.
_TIMING_LOCK = threading.Lock()
_TIMING = {}


def _reset_timing():
    with _TIMING_LOCK:
        _TIMING.clear()


def _record(stage, seconds):
    with _TIMING_LOCK:
        total, count = _TIMING.get(stage, (0.0, 0))
        _TIMING[stage] = (total + seconds, count + 1)


def _timing_report():
    with _TIMING_LOCK:
        return {
            stage: {"total_seconds": round(total, 2), "calls": count, "avg_seconds": round(total / count, 2)}
            for stage, (total, count) in sorted(_TIMING.items())
        }


def load_config(path="config.env"):
    """Tiny .env loader: KEY=VALUE lines. No extra dependency needed for this.

    Any key can also be overridden by a real environment variable of the
    same name, which is handy for the deployed container."""
    config = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            config[key.strip()] = value.strip()

    # Environment wins over the file, for the file's own keys plus the
    # performance knobs (which don't have to be present in config.env).
    for key in list(config) + ["AGENT_MAX_WORKERS", "AGENT_REASONING_MODE", "LLM_MAX_CONCURRENCY"]:
        if key in os.environ:
            config[key] = os.environ[key]
    return config


def _int_config(config, key, default):
    try:
        return int(str(config.get(key, default)).strip())
    except (TypeError, ValueError):
        return default


_PRINT_LOCK = threading.Lock()


def _log(message):
    """print() with an elapsed-time stamp. Everything printed here lands in
    the AgentCore Runtime CloudWatch log group when running deployed.

    Locked, and written as one call rather than print()'s separate
    text-then-newline writes: with several workers logging at once, two
    lines were occasionally landing in CloudWatch spliced into a single
    unreadable event."""
    line = f"[t={time.time() - _RUN_STARTED:6.2f}s] {message}\n"
    with _PRINT_LOCK:
        sys.stdout.write(line)
        sys.stdout.flush()


def resolve_tool_name(available_names, short_name):
    """Gateway prefixes tool names with the target name (e.g.
    'CopyFileTarget___copy_file') - look up the real name instead of
    hardcoding the separator."""
    return next(n for n in available_names if n.endswith(short_name))


# --- The call -> read log -> reason -> compare -> record flow -------------


def _call_lambda(mcp_client, available_names, short_tool_name, arguments, iteration):
    """Step 1-2: call the Lambda through the Gateway and pull out its
    request ID. Returns (structured_output, request_id, called_at_ms).

    called_at_ms lets the CloudWatch reader skip every log stream that went
    quiet before this call happened."""
    tool_name = resolve_tool_name(available_names, short_tool_name)
    called_at_ms = int(time.time() * 1000)

    started = time.time()
    raw_result = mcp_client.call_tool_sync(
        # tool_use_id has to be unique per call now that calls overlap.
        tool_use_id=f"{short_tool_name}-{iteration}-{threading.get_ident()}-{called_at_ms}",
        name=tool_name,
        arguments=arguments,
    )
    _record("gateway_lambda_call", time.time() - started)
    structured = json.loads(raw_result["content"][0]["text"])
    return structured, structured.get("request_id", "unknown"), called_at_ms


def _reason_about_call(structured, request_id, called_at_ms, short_tool_name, config, file_name, iteration):
    """Steps 3-6: fetch the exact CloudWatch log for this request ID, ask
    the LLM to conclude success/failure from that text alone, compare its
    conclusion against the Lambda's structured output, and return the
    finished LOG entry as text.

    This is the part that can run on a background thread: it reads and
    records, it never decides anything the pipeline depends on."""
    region = config["AWS_REGION"]

    # 3. Fetch the exact CloudWatch log text for this invocation. The
    # Lambda reports which log stream it wrote to, so this is a direct read
    # of one known stream rather than a search over "recently active"
    # streams - the latter is unreliable while many invocations overlap.
    function_name = FUNCTION_NAMES[short_tool_name]
    started = time.time()
    log_text = cloudwatch.get_log_text_for_request(
        function_name,
        request_id,
        region,
        since_ms=called_at_ms,
        log_stream_name=structured.get("log_stream_name"),
    )
    _record("cloudwatch_log_read", time.time() - started)

    # 4. Ask the LLM to independently conclude success/failure from the log
    # text alone - it never sees the Lambda's structured output.
    lambda_success = bool(structured.get(SUCCESS_FIELD[short_tool_name]))
    lambda_verdict = "SUCCESS" if lambda_success else "FAILURE"

    if not log_text.strip():
        # Never ask the LLM to reason over an empty log: it would invent a
        # verdict and that verdict would show up as a false DISAGREEMENT,
        # which is far more misleading than saying so plainly.
        llm_conclusion = (
            "(no CloudWatch log text was retrievable for this request id within the timeout - "
            "no independent conclusion could be formed)"
        )
        llm_verdict = "UNAVAILABLE"
        comparison = "NOT COMPARED (log unavailable)"
    else:
        prompt = (
            "Here is a CloudWatch log from a Lambda operation. Based only on "
            "this log text, did the operation succeed or fail? Start your reply "
            "with exactly one word, SUCCESS or FAILURE, then on the next line "
            "explain your evidence from the log in one or two short sentences, "
            "quoting the specific log line you relied on.\n\n"
            f"--- CloudWatch log ---\n{log_text}"
        )
        started = time.time()
        try:
            llm_conclusion = llm_wrapper.ask_llm(prompt, config)
        except Exception as exc:
            # A free-tier daily quota running out mid-run must not take the
            # file pipeline down with it. Record what happened and carry on:
            # the copy/verify/delete decisions never depended on this call.
            _record("llm_reasoning", time.time() - started)
            return (
                f"--- File: {file_name} | Iteration: {iteration} | "
                f"Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} ---\n"
                f"Lambda {short_tool_name} structured output: {structured}\n"
                f"CloudWatch log text:\n{log_text}\n"
                f"Agent LLM conclusion: (the LLM call failed: {type(exc).__name__}: {exc})\n"
                f"COMPARISON: Lambda says {lambda_verdict}, Agent concludes UNAVAILABLE "
                f"-> NOT COMPARED (LLM unavailable)\n"
            )
        _record("llm_reasoning", time.time() - started)
        llm_verdict = llm_conclusion.strip().split()[0].upper().rstrip(".:,") if llm_conclusion.strip() else "UNKNOWN"

        # 5. Compare the LLM's conclusion to the Lambda's structured output.
        # This comparison is a plain deterministic string check - never the
        # LLM deciding pass/fail on its own.
        comparison = "MATCH" if llm_verdict == lambda_verdict else "DISAGREEMENT"

    # 6. Build the full, learner-readable entry for the global LOG in B3.
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"--- File: {file_name} | Iteration: {iteration} | Timestamp: {timestamp} ---\n"
        f"Lambda {short_tool_name} structured output: {structured}\n"
        f"CloudWatch log text:\n{log_text}\n"
        f"Agent LLM conclusion: {llm_conclusion.strip()}\n"
        f"COMPARISON: Lambda says {lambda_verdict}, Agent concludes {llm_verdict} -> {comparison}\n"
    )


def call_lambda_and_reason(
    mcp_client,
    available_names,
    short_tool_name,
    arguments,
    config,
    file_name,
    iteration,
    file_log=None,
    reasoning_pool=None,
):
    """
    The core flow:
      1. Call the Lambda through Gateway -> structured JSON result
      2. Extract the Lambda request ID from that result
      3. Fetch the exact CloudWatch log text for that request ID
      4. Ask the LLM to independently conclude success/failure from the log
      5. Compare the LLM's conclusion to the Lambda's structured output
      6. Write everything to the global LOG in B3

    Steps 1-2 always happen right here, synchronously - the caller needs
    the structured result to decide what to do next. Steps 3-6 are handed
    to `reasoning_pool` when one is supplied, and the LOG entry they will
    eventually produce is reserved as a slot in `file_log` so the finished
    LOG still reads in the right order.

    Returns (structured_output, entry_text). entry_text is None when the
    reasoning was deferred to the pool.
    """
    structured, request_id, called_at_ms = _call_lambda(
        mcp_client, available_names, short_tool_name, arguments, iteration
    )

    def build_entry():
        return _reason_about_call(
            structured, request_id, called_at_ms, short_tool_name, config, file_name, iteration
        )

    if reasoning_pool is not None and file_log is not None:
        file_log.add_deferred(reasoning_pool.submit(build_entry))
        return structured, None

    entry = build_entry()
    if file_log is not None:
        file_log.add(entry)
    else:
        logbook.append_entry(config["BUCKET_LOG"], config["AWS_REGION"], entry)
    return structured, entry


def list_b1_files(s3, bucket):
    """Re-inspect B1 fresh, every call - never a list captured once at the
    start, so files dropped in mid-run are discovered."""
    resp = s3.list_objects_v2(Bucket=bucket)
    return [obj["Key"] for obj in resp.get("Contents", [])]


def process_one_file(
    mcp_client,
    available_names,
    s3,
    config,
    file_name,
    iteration,
    file_log=None,
    reasoning_pool=None,
):
    """
    Process exactly one file through the full pipeline: select -> copy (L1)
    -> maybe corrupt (L3) -> verify (L2) -> delete-if-verified-else-keep.
    All decisions here are plain deterministic Python - the LLM never
    decides delete/keep or pass/fail, it only produces the comparison text
    written to LOG.

    Every step for one file stays strictly ordered (you cannot verify a
    copy before it has been made). It is different FILES that run in
    parallel, not the steps within a file.

    `file_log`/`reasoning_pool` are supplied by run_agent(). When they are
    omitted - which is how the test suite calls this - the function writes
    its own LOG block to S3 on the way out and does all reasoning inline,
    exactly like the original single-threaded version.
    """
    region = config["AWS_REGION"]
    source_bucket = config["BUCKET_SOURCE"]
    dest_bucket = config["BUCKET_DEST"]

    standalone_buffer = None
    if file_log is None:
        standalone_buffer = logbook.LogBuffer(config["BUCKET_LOG"], region)
        file_log = standalone_buffer.file_log(iteration, file_name)

    try:
        file_log.add(f"=== Iteration {iteration}: selected {file_name} from B1 ===")

        # Step 4-5: L1 copy, with CloudWatch + LLM reasoning + LOG entry.
        l1_result, _ = call_lambda_and_reason(
            mcp_client,
            available_names,
            "copy_file",
            {"source_bucket": source_bucket, "dest_bucket": dest_bucket, "file_name": file_name},
            config,
            file_name,
            iteration,
            file_log=file_log,
            reasoning_pool=reasoning_pool,
        )

        if not l1_result.get("success"):
            file_log.add(
                f"Final status for {file_name}: COPY FAILED - kept in B1. "
                f"Error: {l1_result.get('error')}"
            )
            return "copy_failed"

        original_hash = l1_result["original_hash"]

        # Step 6: random coin flip - maybe deliberately corrupt the B2 copy.
        corrupted = random.random() < L3_PROBABILITY
        if corrupted:
            call_lambda_and_reason(
                mcp_client,
                available_names,
                "corrupt_file",
                {"dest_bucket": dest_bucket, "file_name": file_name},
                config,
                file_name,
                iteration,
                file_log=file_log,
                reasoning_pool=reasoning_pool,
            )

        # Step 7: L2 verify, with the same CloudWatch + LLM reasoning + LOG entry.
        l2_result, _ = call_lambda_and_reason(
            mcp_client,
            available_names,
            "verify_file",
            {"dest_bucket": dest_bucket, "file_name": file_name, "original_hash": original_hash},
            config,
            file_name,
            iteration,
            file_log=file_log,
            reasoning_pool=reasoning_pool,
        )

        # Step 8: delete original only after successful verification; otherwise
        # keep it in B1 and record the failure. This decision is made here, in
        # plain Python, from the Lambda's own structured "match" field - never
        # from the LLM's independent conclusion, and never from a background
        # thread's result.
        verified = bool(l2_result.get("match"))
        if verified:
            s3.delete_object(Bucket=source_bucket, Key=file_name)
            status = "VERIFIED - original deleted from B1"
        else:
            status = "VERIFICATION FAILED - original kept in B1"

        file_log.add(
            f"Final status for {file_name}: {status}. "
            f"L3 called: {'yes' if corrupted else 'no'}. "
            f"Hash comparison: {'MATCH' if verified else 'MISMATCH'}."
        )

        return "verified" if verified else "verification_failed"
    finally:
        if standalone_buffer is not None:
            standalone_buffer.flush()


# --- Claiming files out of B1, safely, from several workers at once ------


class B1Coordinator:
    """Hands out one file at a time to whichever worker asks next.

    Keeps the two rules the single-threaded loop got for free:
      * B1 is re-listed FRESH on every claim, so a file dropped into B1
        mid-run is still discovered (nothing is captured up front).
      * The same file is never handed to two workers at once - a file that
        fails verification stays in B1, and without this it would be picked
        up again while its first attempt was still running.
    """

    def __init__(self, s3, bucket, max_iterations=None):
        max_iterations = max_iterations or MAX_ITERATIONS
        self._s3 = s3
        self._bucket = bucket
        self._max_iterations = max_iterations
        self.max_iterations = max_iterations
        self._lock = threading.Lock()
        self._in_flight = set()
        self._iteration = 0
        self._stopped = False
        self.hit_safety_cap = False

    @property
    def iterations(self):
        return self._iteration

    def claim(self):
        """Block until a file is available, then return
        (file_name, iteration, files_in_b1). Return None when the run is
        finished - B1 is empty and no worker still holds anything."""
        while True:
            with self._lock:
                if self._stopped:
                    return None

                files = list_b1_files(self._s3, self._bucket)
                available = [f for f in files if f not in self._in_flight]

                if available:
                    if self._iteration >= self._max_iterations:
                        self._stopped = True
                        self.hit_safety_cap = True
                        return None
                    self._iteration += 1
                    file_name = random.choice(available)
                    self._in_flight.add(file_name)
                    return file_name, self._iteration, len(files)

                if not self._in_flight:
                    # Nothing left in B1 and nothing being worked on: done.
                    self._stopped = True
                    return None

            # B1 still has files, but every one of them is already being
            # processed. Wait for a worker to finish - it may put a failed
            # file back into play.
            time.sleep(0.2)

    def release(self, file_name):
        with self._lock:
            self._in_flight.discard(file_name)


def _worker(worker_id, coordinator, config, gateway_url, buffer, reasoning_pool, outcomes, outcomes_lock):
    """One worker: its own Gateway/MCP connection, pulling files from the
    coordinator until the run is done.

    Each worker gets its own MCP client on purpose. One shared client would
    work for the common case, but a connection per worker is unambiguously
    safe and the setup cost is paid once, not per file."""
    region = config["AWS_REGION"]
    s3 = aws_clients.get_client("s3", region)
    mcp_client = gateway_client.get_gateway_mcp_client(gateway_url, region)

    with mcp_client:
        available_names = [t.tool_name for t in mcp_client.list_tools_sync()]

        while True:
            claim = coordinator.claim()
            if claim is None:
                return
            file_name, iteration, files_in_b1 = claim

            try:
                _log(
                    f"[iteration {iteration}] worker {worker_id} processing {file_name} "
                    f"({files_in_b1} file(s) currently in B1)"
                )
                file_log = buffer.file_log(iteration, file_name)
                outcome = process_one_file(
                    mcp_client,
                    available_names,
                    s3,
                    config,
                    file_name,
                    iteration,
                    file_log=file_log,
                    reasoning_pool=reasoning_pool,
                )
                _log(f"[iteration {iteration}] worker {worker_id} {file_name} -> {outcome}")
                with outcomes_lock:
                    outcomes.append({"file": file_name, "outcome": outcome, "iteration": iteration})
            finally:
                # Only release AFTER the file has been deleted or kept -
                # otherwise another worker could claim it mid-pipeline.
                coordinator.release(file_name)


def run_agent(config):
    """The deterministic loop: keep processing files while B1 is not empty,
    several files at a time, until B1 is empty. Runs with no user request
    once started. Returns a small summary dict."""
    global _RUN_STARTED
    _RUN_STARTED = time.time()
    _reset_timing()

    region = config["AWS_REGION"]
    source_bucket = config["BUCKET_SOURCE"]
    s3 = aws_clients.get_client("s3", region)

    requested_workers = max(1, _int_config(config, "AGENT_MAX_WORKERS", DEFAULT_MAX_WORKERS))
    reasoning_mode = str(config.get("AGENT_REASONING_MODE", "async")).strip().lower()

    initial_files = list_b1_files(s3, source_bucket)
    if not initial_files:
        _log("B1 is empty. Nothing to do.")
        return {
            "iterations": 0,
            "files": [],
            "elapsed_seconds": 0.0,
            "max_workers": 0,
            "reasoning_mode": reasoning_mode,
            "timing": {},
        }

    # No point starting more workers than there are files to work on.
    max_workers = min(requested_workers, len(initial_files))

    _log(
        f"Starting run: {len(initial_files)} file(s) in B1, {max_workers} parallel worker(s), "
        f"reasoning={reasoning_mode}"
    )

    gateway_url = gateway_client.get_gateway_url(region)

    buffer = logbook.LogBuffer(config["BUCKET_LOG"], region)
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    buffer.note(
        f"\n========== AGENT RUN {started_at} | {len(initial_files)} file(s) in B1 "
        f"| {max_workers} parallel worker(s) | reasoning={reasoning_mode} =========="
    )

    coordinator = B1Coordinator(s3, source_bucket, max_iterations=iteration_cap(len(initial_files)))
    outcomes = []
    outcomes_lock = threading.Lock()

    # The reasoning pool is deliberately larger than the file pool: each
    # file can have up to three reasoning jobs (L1, L3, L2) in flight while
    # its own pipeline has already moved on. LLM_MAX_CONCURRENCY in
    # llm_wrapper.py is what actually protects the provider's rate limit.
    reasoning_pool = None
    if reasoning_mode != "inline":
        reasoning_pool = ThreadPoolExecutor(
            max_workers=min(24, max(8, max_workers * 3)), thread_name_prefix="reason"
        )

    try:
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="file") as pool:
            futures = [
                pool.submit(
                    _worker, i + 1, coordinator, config, gateway_url,
                    buffer, reasoning_pool, outcomes, outcomes_lock,
                )
                for i in range(max_workers)
            ]
            for future in futures:
                future.result()  # surface any worker exception instead of hiding it

        if coordinator.hit_safety_cap:
            message = (
                f"Safety cap of {coordinator.max_iterations} iterations reached - stopping. "
                f"Files left in B1 were not processed."
            )
            _log(message)
            buffer.footer(message)
        else:
            _log("B1 is empty. Agent run complete.")
    finally:
        # Wait for every deferred CloudWatch read + LLM conclusion to land,
        # so the LOG written below is complete.
        if reasoning_pool is not None:
            _log("Waiting for background log-reading and LLM reasoning to finish...")
            reasoning_pool.shutdown(wait=True)

        elapsed = round(time.time() - _RUN_STARTED, 2)
        with outcomes_lock:
            ordered = sorted(outcomes, key=lambda o: o["iteration"])

        timing = _timing_report()
        breakdown = " | ".join(
            f"{stage}: {info['total_seconds']}s over {info['calls']} call(s), avg {info['avg_seconds']}s"
            for stage, info in timing.items()
        )
        buffer.footer(
            f"========== RUN COMPLETE | {coordinator.iterations} iteration(s) "
            f"| {len(ordered)} file-pass(es) | {elapsed}s wall clock "
            f"| {max_workers} parallel worker(s) =========="
        )
        if breakdown:
            buffer.footer(f"Time spent (summed across all threads, so larger than wall clock): {breakdown}")
        # One GET + one PUT for the entire run's LOG.
        buffer.flush()
        for stage, info in timing.items():
            _log(
                f"  {stage:<22} {info['total_seconds']:>8.2f}s summed over {info['calls']:>3} call(s) "
                f"(avg {info['avg_seconds']}s)"
            )
        _log(f"LOG written to s3://{config['BUCKET_LOG']}/LOG ({elapsed}s total)")

    return {
        "iterations": coordinator.iterations,
        "files": ordered,
        "elapsed_seconds": elapsed,
        "max_workers": max_workers,
        "reasoning_mode": reasoning_mode,
        "timing": timing,
    }


# --- AgentCore Runtime entrypoint ---------------------------------------
# `agentcore configure -e agent/agent.py` finds this app object; deployed
# invocations (`agentcore invoke`) call invoke() below. The payload's
# content is optional - any invocation triggers one full drain-B1 run,
# since this agent doesn't wait on a user request. The payload can however
# tune this run, which is what the cloud test scripts use to demonstrate
# the speed difference:
#
#     agentcore invoke '{"max_workers": 1}'      # old, one-file-at-a-time
#     agentcore invoke '{"max_workers": 8}'      # parallel (default)
#     agentcore invoke '{"reasoning": "inline"}' # reasoning back on the
#                                                # critical path
app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload=None):
    config = load_config()

    overrides = payload if isinstance(payload, dict) else {}
    if overrides.get("max_workers") is not None:
        config["AGENT_MAX_WORKERS"] = str(overrides["max_workers"])
    if overrides.get("reasoning") is not None:
        config["AGENT_REASONING_MODE"] = str(overrides["reasoning"])
    if overrides.get("llm_max_concurrency") is not None:
        config["LLM_MAX_CONCURRENCY"] = str(overrides["llm_max_concurrency"])

    summary = run_agent(config)

    return {
        "status": "complete",
        "message": (
            f"Processed {len(summary['files'])} file-pass(es) over {summary['iterations']} "
            f"iteration(s) in {summary['elapsed_seconds']}s using {summary['max_workers']} "
            f"parallel worker(s). B1 is empty."
        ),
        "elapsed_seconds": summary["elapsed_seconds"],
        "max_workers": summary["max_workers"],
        "reasoning_mode": summary["reasoning_mode"],
        "iterations": summary["iterations"],
        "files": summary["files"],
        # Where the time went, so a slow run can be diagnosed straight from
        # the invoke response without opening CloudWatch.
        "timing": summary.get("timing", {}),
        "log_location": f"s3://{config['BUCKET_LOG']}/LOG",
    }


if __name__ == "__main__":
    # `python agent/agent.py` (no args) starts the AgentCore app server and
    # waits for an invoke - this is what the deployed Runtime container
    # actually runs, so it must NOT eagerly drain B1 at startup.
    # `python agent/agent.py --run-once` keeps the direct-run behaviour for
    # local dev/testing without a full agentcore invoke round-trip.
    # `--workers N` / `--reasoning inline` override config.env for one run.
    if "--run-once" in sys.argv:
        cli_config = load_config()
        if "--workers" in sys.argv:
            cli_config["AGENT_MAX_WORKERS"] = sys.argv[sys.argv.index("--workers") + 1]
        if "--reasoning" in sys.argv:
            cli_config["AGENT_REASONING_MODE"] = sys.argv[sys.argv.index("--reasoning") + 1]
        result = run_agent(cli_config)
        print(json.dumps(result, indent=2))
    else:
        app.run()
