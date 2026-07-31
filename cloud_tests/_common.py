"""
Shared helpers for the cloud-only test scripts in this folder.

IMPORTANT - what "cloud-only" means here:
Nothing in cloud_tests/ imports or runs the agent code. Every script in
this folder drives the DEPLOYED AgentCore Runtime agent over the AWS API
and then inspects the resulting AWS state (S3 buckets, CloudWatch logs).
Your laptop only sends the trigger and reads the results back; all of the
actual work happens inside the container AWS is running for you.

That is the point: if these scripts pass, the thing that passed is the
deployment, not a local copy of the code.
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

import boto3
from botocore.config import Config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENT_NAME = "test"

# Colourless status markers - they survive copy/paste into a document or a
# video caption better than emoji do.
PASS = "[ PASS ]"
FAIL = "[ FAIL ]"
INFO = "[ info ]"


def load_config(path=None):
    """Tiny .env loader: KEY=VALUE lines, same one the rest of the project uses."""
    path = path or str(PROJECT_ROOT / "config.env")
    config = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            config[key.strip()] = value.strip()
    return config


def _dataplane(region):
    """Data-plane client used to invoke the deployed agent.

    read_timeout is generous on purpose: one invocation runs the agent's
    whole drain-B1 pass inside AWS, and a deliberately slowed-down
    single-worker comparison run can take a couple of minutes."""
    return boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(read_timeout=900, connect_timeout=60, retries={"max_attempts": 2}),
    )


def _control(region):
    return boto3.client("bedrock-agentcore-control", region_name=region)


def find_agent_runtime(region, agent_name=AGENT_NAME):
    """Locate the deployed Runtime agent, and fail with a useful message if
    it isn't there. Returns (agent_arn, agent_id).

    Looked up live from AWS rather than read out of .bedrock_agentcore.yaml,
    so these scripts test what is actually deployed right now."""
    control = _control(region)
    runtimes = []
    paginator_token = None
    while True:
        kwargs = {"maxResults": 100}
        if paginator_token:
            kwargs["nextToken"] = paginator_token
        resp = control.list_agent_runtimes(**kwargs)
        runtimes.extend(resp.get("agentRuntimes", []))
        paginator_token = resp.get("nextToken")
        if not paginator_token:
            break

    match = next((r for r in runtimes if r.get("agentRuntimeName") == agent_name), None)
    if match is None:
        names = ", ".join(sorted(r.get("agentRuntimeName", "?") for r in runtimes)) or "(none)"
        raise SystemExit(
            f"{FAIL} No deployed AgentCore Runtime named '{agent_name}' in {region}.\n"
            f"        Runtimes found in this account/region: {names}\n"
            f"        Deploy first:  agentcore configure -e agent/agent.py --execution-role <ARN>\n"
            f"                       agentcore deploy"
        )

    arn = match["agentRuntimeArn"]
    return arn, match.get("agentRuntimeId") or arn.rsplit("/", 1)[-1]


def new_session_id(label="cloudtest"):
    """AgentCore requires a runtime session id of at least 33 characters."""
    return f"{label}-{uuid.uuid4().hex}-{uuid.uuid4().hex}"[:80]


def invoke_agent(region, agent_arn, payload, session_id=None):
    """Invoke the DEPLOYED agent once and return (result_dict, seconds, session_id).

    This is exactly what the `agentcore invoke` CLI does under the hood -
    a signed InvokeAgentRuntime call against the data plane. Using boto3
    directly just means we can time it and parse the JSON reliably."""
    session_id = session_id or new_session_id()
    client = _dataplane(region)

    started = time.time()
    response = client.invoke_agent_runtime(
        agentRuntimeArn=agent_arn,
        qualifier="DEFAULT",
        runtimeSessionId=session_id,
        payload=json.dumps(payload).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
    )
    body = response["response"].read()
    elapsed = round(time.time() - started, 2)

    try:
        result = json.loads(body)
        if isinstance(result, str):
            result = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        result = {"raw": body.decode("utf-8", errors="replace")}

    return result, elapsed, session_id


def s3_keys(region, bucket, prefix=""):
    s3 = boto3.client("s3", region_name=region)
    keys, token = [], None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        keys.extend(obj["Key"] for obj in resp.get("Contents", []))
        token = resp.get("NextContinuationToken")
        if not token:
            break
    return keys


def read_log_object(region, bucket, key="LOG"):
    s3 = boto3.client("s3", region_name=region)
    try:
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    except Exception:
        return ""


def seed_source_bucket(region, bucket, count):
    """Put `count` fresh sample files into B1 using the SAME naming scheme
    setup/02_seed_files.py uses, so a cloud test run looks identical to a
    normal run. This is bucket seeding, not agent code - the agent itself
    still runs only in AWS."""
    s3 = boto3.client("s3", region_name=region)

    # Clear anything left over first, so timings compare like for like.
    for key in s3_keys(region, bucket):
        s3.delete_object(Bucket=bucket, Key=key)

    for i in range(1, count + 1):
        key = f"sample_{i:03d}.txt"
        lines = [f"Sample file #{i}"] + [f"Line {n} of sample_{i:03d}.txt" for n in range(1, 21)]
        s3.put_object(Bucket=bucket, Key=key, Body=("\n".join(lines) + "\n").encode("utf-8"))
    return count


def runtime_log_group(agent_id, endpoint="DEFAULT"):
    """The CloudWatch log group AgentCore Runtime writes the container's
    stdout/stderr to."""
    return f"/aws/bedrock-agentcore/runtimes/{agent_id}-{endpoint}"


def fetch_runtime_logs(region, agent_id, since_ms, contains=None, timeout_seconds=120, endpoint="DEFAULT"):
    """Pull the deployed agent's own console output out of CloudWatch.

    CloudWatch's search index lags a little behind the container writing a
    line, so this polls until it either sees something or gives up."""
    logs = boto3.client("logs", region_name=region)
    group = runtime_log_group(agent_id, endpoint)
    deadline = time.time() + timeout_seconds
    delay = 2.0

    while True:
        events = []
        try:
            token = None
            while True:
                kwargs = {"logGroupName": group, "startTime": since_ms, "limit": 10000}
                if contains:
                    kwargs["filterPattern"] = f'"{contains}"'
                if token:
                    kwargs["nextToken"] = token
                resp = logs.filter_log_events(**kwargs)
                events.extend(resp.get("events", []))
                token = resp.get("nextToken")
                if not token:
                    break
        except logs.exceptions.ResourceNotFoundException:
            events = []

        if events or time.time() >= deadline:
            return sorted(events, key=lambda e: e["timestamp"]), group

        time.sleep(delay)
        delay = min(delay * 1.5, 10.0)


def banner(title):
    line = "=" * 74
    print(f"\n{line}\n{title}\n{line}", flush=True)


def check(condition, description, detail=""):
    """Print a PASS/FAIL line and return the boolean, so a script can total
    them up at the end. Deliberately plain-text for the screen recording."""
    marker = PASS if condition else FAIL
    print(f"{marker} {description}" + (f"\n         {detail}" if detail else ""), flush=True)
    return bool(condition)


def require_deployed(config):
    """Resolve the deployed agent up front so every script fails fast, and
    with the same message, if nothing is deployed yet."""
    region = config["AWS_REGION"]
    arn, agent_id = find_agent_runtime(region)
    print(f"{INFO} Region        : {region}")
    print(f"{INFO} Agent runtime : {arn}")
    print(f"{INFO} Agent id      : {agent_id}")
    print(f"{INFO} Runtime logs  : {runtime_log_group(agent_id)}")
    return region, arn, agent_id


def exit_code(results):
    """0 if every check passed, 1 otherwise - so these scripts can be used
    in a pipeline as well as on camera."""
    failed = [d for ok, d in results if not ok]
    print()
    if failed:
        print(f"{FAIL} {len(failed)} of {len(results)} checks failed:")
        for description in failed:
            print(f"         - {description}")
        return 1
    print(f"{PASS} All {len(results)} checks passed.")
    return 0


if __name__ == "__main__":
    cfg = load_config()
    require_deployed(cfg)
    print(f"{INFO} Buckets: {cfg['BUCKET_SOURCE']} / {cfg['BUCKET_DEST']} / {cfg['BUCKET_LOG']}")
    sys.exit(0)
