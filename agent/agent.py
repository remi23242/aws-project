"""
Strands agent: the deterministic control loop (CLAUDE.md Section 3.3), built
around the flow CLAUDE.md Section 2.1 calls the MOST IMPORTANT part of the
project: call a Lambda through the Gateway, read its exact CloudWatch log,
get the LLM's independent text conclusion, compare it to the Lambda's
structured output, and write all of it to the global LOG in B3.

All deterministic decisions (order, one-file-at-a-time, delete-only-after-
verify) live in this Python loop, never in the LLM - the LLM is only used to
read log TEXT and form a conclusion for comparison (CLAUDE.md Section 2).

Runs two ways:
  - Locally/for tests: `python agent/agent.py` runs the loop once and exits.
  - On AgentCore Runtime: `agentcore configure -e agent/agent.py` finds the
    `app`/`invoke` below and deploys it; `agentcore invoke` then triggers
    one full drain-B1 run per call, since this agent is deterministic and
    autonomous rather than conversational - it doesn't need prompt content,
    just a trigger to start (CLAUDE.md Section 2).
"""

import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

# Always resolve sibling imports (cloudwatch, gateway_client, etc.) relative
# to this file's own directory, regardless of the working directory the
# process was started from - matters once this runs inside an AgentCore
# Runtime container instead of a local terminal.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import boto3
import cloudwatch
import gateway_client
import llm_wrapper
import logbook
from bedrock_agentcore import BedrockAgentCoreApp

# Chance that a given file gets deliberately corrupted via L3 after copying,
# to prove L2's verification actually catches real failures (CLAUDE.md
# Section 3.3 step 6 / Section 3.5).
L3_PROBABILITY = 0.4

# Safety cap so a bug can never spin this loop forever during a live demo -
# far more than the handful of iterations this project should ever need.
MAX_ITERATIONS = 50

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


def load_config(path="config.env"):
    """Tiny .env loader: KEY=VALUE lines. No extra dependency needed for this."""
    config = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            config[key.strip()] = value.strip()
    return config


def resolve_tool_name(available_names, short_name):
    """Gateway prefixes tool names with the target name (e.g.
    'CopyFileTarget___copy_file') - look up the real name instead of
    hardcoding the separator."""
    return next(n for n in available_names if n.endswith(short_name))


def call_lambda_and_reason(mcp_client, available_names, short_tool_name, arguments, config, file_name, iteration):
    """
    The core flow (CLAUDE.md Section 2.1):
      1. Call the Lambda through Gateway -> structured JSON result
      2. Extract the Lambda request ID from that result
      3. Fetch the exact CloudWatch log text for that request ID
      4. Ask the LLM to independently conclude success/failure from the log
      5. Compare the LLM's conclusion to the Lambda's structured output
      6. Write everything to the global LOG in B3

    Returns the Lambda's structured output dict, so the caller (the
    deterministic loop) - NOT this function, and NOT the LLM - decides what
    happens next (call another tool, delete a file, etc).
    """
    region = config["AWS_REGION"]
    tool_name = resolve_tool_name(available_names, short_tool_name)

    # 1. Call the Lambda through Gateway.
    raw_result = mcp_client.call_tool_sync(
        tool_use_id=f"{short_tool_name}-{iteration}",
        name=tool_name,
        arguments=arguments,
    )
    structured = json.loads(raw_result["content"][0]["text"])

    # 2. Extract the request ID.
    request_id = structured.get("request_id", "unknown")

    # 3. Fetch the exact CloudWatch log text for this invocation.
    function_name = FUNCTION_NAMES[short_tool_name]
    log_text = cloudwatch.get_log_text_for_request(function_name, request_id, region)

    # 4. Ask the LLM to independently conclude success/failure from the log
    # text alone - it never sees the Lambda's structured output.
    prompt = (
        "Here is a CloudWatch log from a Lambda operation. Based only on "
        "this log text, did the operation succeed or fail? Start your reply "
        "with exactly one word, SUCCESS or FAILURE, then on the next line "
        "explain your evidence from the log.\n\n"
        f"--- CloudWatch log ---\n{log_text}"
    )
    llm_conclusion = llm_wrapper.ask_llm(prompt, config)
    llm_verdict = llm_conclusion.strip().split()[0].upper().rstrip(".:,") if llm_conclusion.strip() else "UNKNOWN"

    # 5. Compare the LLM's conclusion to the Lambda's structured output.
    # This comparison is a plain deterministic string check - never the LLM
    # deciding pass/fail on its own (CLAUDE.md Section 2's control-flow rule).
    lambda_success = bool(structured.get(SUCCESS_FIELD[short_tool_name]))
    lambda_verdict = "SUCCESS" if lambda_success else "FAILURE"
    comparison = "MATCH" if llm_verdict == lambda_verdict else "DISAGREEMENT"

    # 6. Write the full, learner-readable entry to the global LOG in B3.
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = (
        f"--- File: {file_name} | Iteration: {iteration} | Timestamp: {timestamp} ---\n"
        f"Lambda {short_tool_name} structured output: {structured}\n"
        f"CloudWatch log text:\n{log_text}\n"
        f"Agent LLM conclusion: {llm_conclusion.strip()}\n"
        f"COMPARISON: Lambda says {lambda_verdict}, Agent concludes {llm_verdict} -> {comparison}\n"
    )
    logbook.append_entry(config["BUCKET_LOG"], region, entry)

    return structured, entry


def list_b1_files(s3, bucket):
    """Re-inspect B1 fresh, every call - never a list captured once at the
    start, so files dropped in mid-run are discovered (CLAUDE.md Section
    3.3 step 2)."""
    resp = s3.list_objects_v2(Bucket=bucket)
    return [obj["Key"] for obj in resp.get("Contents", [])]


def process_one_file(mcp_client, available_names, s3, config, file_name, iteration):
    """
    Process exactly one file through the full pipeline (CLAUDE.md Section
    3.3 steps 3-8): select -> copy (L1) -> maybe corrupt (L3) -> verify
    (L2) -> delete-if-verified-else-keep. All decisions here are plain
    deterministic Python - the LLM never decides delete/keep or pass/fail,
    it only produces the comparison text written to LOG (Section 2).
    """
    region = config["AWS_REGION"]
    source_bucket = config["BUCKET_SOURCE"]
    dest_bucket = config["BUCKET_DEST"]

    logbook.append_entry(
        config["BUCKET_LOG"],
        region,
        f"=== Iteration {iteration}: selected {file_name} from B1 ===",
    )

    # Step 4-5: L1 copy, with CloudWatch + LLM reasoning + LOG entry.
    l1_result, _ = call_lambda_and_reason(
        mcp_client,
        available_names,
        "copy_file",
        {"source_bucket": source_bucket, "dest_bucket": dest_bucket, "file_name": file_name},
        config,
        file_name,
        iteration,
    )

    if not l1_result.get("success"):
        logbook.append_entry(
            config["BUCKET_LOG"],
            region,
            f"Final status for {file_name}: COPY FAILED - kept in B1. Error: {l1_result.get('error')}",
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
    )

    # Step 8: delete original only after successful verification; otherwise
    # keep it in B1 and record the failure. This decision is made here, in
    # plain Python, from the Lambda's own structured "match" field - never
    # from the LLM's independent conclusion.
    verified = bool(l2_result.get("match"))
    if verified:
        s3.delete_object(Bucket=source_bucket, Key=file_name)
        status = "VERIFIED - original deleted from B1"
    else:
        status = "VERIFICATION FAILED - original kept in B1"

    logbook.append_entry(
        config["BUCKET_LOG"],
        region,
        f"Final status for {file_name}: {status}. "
        f"L3 called: {'yes' if corrupted else 'no'}. "
        f"Hash comparison: {'MATCH' if verified else 'MISMATCH'}.",
    )

    return "verified" if verified else "verification_failed"


def run_agent(config):
    """The deterministic loop (CLAUDE.md Section 3.3): repeat while B1 is
    not empty, one file at a time, until B1 is empty. Runs with no user
    request once started. Returns a small summary dict."""
    region = config["AWS_REGION"]
    source_bucket = config["BUCKET_SOURCE"]
    s3 = boto3.client("s3", region_name=region)

    gateway_url = gateway_client.get_gateway_url(region)
    mcp_client = gateway_client.get_gateway_mcp_client(gateway_url, region)

    iteration = 0
    outcomes = []
    with mcp_client:
        available_names = [t.tool_name for t in mcp_client.list_tools_sync()]

        while True:
            iteration += 1
            if iteration > MAX_ITERATIONS:
                print(f"Safety cap of {MAX_ITERATIONS} iterations reached - stopping.")
                break

            files = list_b1_files(s3, source_bucket)
            if not files:
                print("B1 is empty. Agent run complete.")
                break

            file_name = random.choice(files)
            print(f"[iteration {iteration}] processing {file_name} ({len(files)} file(s) currently in B1)")

            outcome = process_one_file(mcp_client, available_names, s3, config, file_name, iteration)
            print(f"[iteration {iteration}] {file_name} -> {outcome}")
            outcomes.append({"file": file_name, "outcome": outcome})

    return {"iterations": iteration, "files": outcomes}


# --- AgentCore Runtime entrypoint ---------------------------------------
# `agentcore configure -e agent/agent.py` finds this app object; deployed
# invocations (`agentcore invoke`) call invoke() below. The payload's
# content is ignored - any invocation just triggers one full drain-B1 run,
# since this agent doesn't wait on a user request (CLAUDE.md Section 2).
app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload=None):
    summary = run_agent(load_config())
    return {
        "status": "complete",
        "message": f"Processed {len(summary['files'])} file(s) over {summary['iterations']} iteration(s). B1 is empty.",
        "files": summary["files"],
    }


if __name__ == "__main__":
    # `python agent/agent.py` (no args) starts the AgentCore app server and
    # waits for an invoke - this is what the deployed Runtime container
    # actually runs, so it must NOT eagerly drain B1 at startup.
    # `python agent/agent.py --run-once` keeps the old direct-run behavior
    # for local dev/testing without needing a full agentcore invoke round-trip.
    if "--run-once" in sys.argv:
        run_agent(load_config())
    else:
        app.run()