"""
Step 14: Register L1, L2, L3 as MCP tool targets on the AgentCore Gateway
created in setup/05_create_gateway.py.

Each Lambda becomes exactly one MCP tool (no multiplexing needed since each
Lambda already does one job). Outbound auth is GATEWAY_IAM_ROLE - meaning
the Gateway uses its own execution role (from setup/03_iam_roles.py) to
call the Lambda, per CLAUDE.md Section 2.

Idempotent: safe to re-run - skips a target if one with that name already
exists on the gateway.
"""

import time

import boto3

GATEWAY_NAME = "agentcore-demo-gateway"

TARGETS = [
    {
        "target_name": "CopyFileTarget",
        "function_name": "agentcore-demo-l1-copy",
        "tool_name": "copy_file",
        "description": (
            "Copy a file from the source S3 bucket to the destination S3 "
            "bucket and return its SHA-256 hash."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source_bucket": {"type": "string", "description": "Source S3 bucket name"},
                "dest_bucket": {"type": "string", "description": "Destination S3 bucket name"},
                "file_name": {"type": "string", "description": "Object key to copy"},
            },
            "required": ["source_bucket", "dest_bucket", "file_name"],
        },
    },
    {
        "target_name": "VerifyFileTarget",
        "function_name": "agentcore-demo-l2-verify",
        "tool_name": "verify_file",
        "description": (
            "Verify that a copied file's SHA-256 hash matches the original "
            "hash reported when it was copied."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dest_bucket": {"type": "string", "description": "Destination S3 bucket name"},
                "file_name": {"type": "string", "description": "Object key to verify"},
                "original_hash": {"type": "string", "description": "SHA-256 hash from the copy step"},
            },
            "required": ["dest_bucket", "file_name", "original_hash"],
        },
    },
    {
        "target_name": "CorruptFileTarget",
        "function_name": "agentcore-demo-l3-corrupt",
        "tool_name": "corrupt_file",
        "description": (
            "Simulate a copy error by removing the last 10 lines of a "
            "copied file in the destination S3 bucket."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dest_bucket": {"type": "string", "description": "Destination S3 bucket name"},
                "file_name": {"type": "string", "description": "Object key to corrupt"},
            },
            "required": ["dest_bucket", "file_name"],
        },
    },
]


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


def find_gateway_id(client, name):
    resp = client.list_gateways()
    for gw in resp.get("items", []):
        if gw.get("name") == name:
            return gw["gatewayId"]
    raise RuntimeError(f"Gateway {name} not found - run setup/05_create_gateway.py first")


def wait_for_gateway_ready(client, gateway_id, timeout=120, poll_interval=5):
    elapsed = 0
    while elapsed < timeout:
        gw = client.get_gateway(gatewayIdentifier=gateway_id)
        status = gw.get("status")
        print(f"  Gateway status: {status}")
        if status == "READY":
            return
        if status == "FAILED":
            raise RuntimeError(f"Gateway is in FAILED state: {gw}")
        time.sleep(poll_interval)
        elapsed += poll_interval
    raise TimeoutError("Gateway did not reach READY within the timeout")


def target_exists(client, gateway_id, target_name):
    resp = client.list_gateway_targets(gatewayIdentifier=gateway_id)
    return any(t.get("name") == target_name for t in resp.get("items", []))


def main():
    config = load_config()
    region = config["AWS_REGION"]
    client = boto3.client("bedrock-agentcore-control", region_name=region)
    lambda_client = boto3.client("lambda", region_name=region)

    gateway_id = find_gateway_id(client, GATEWAY_NAME)
    print(f"Gateway: {gateway_id}")
    wait_for_gateway_ready(client, gateway_id)

    for spec in TARGETS:
        if target_exists(client, gateway_id, spec["target_name"]):
            print(f"  {spec['target_name']} already exists, skipping.")
            continue

        lambda_arn = lambda_client.get_function(FunctionName=spec["function_name"])[
            "Configuration"
        ]["FunctionArn"]

        print(f"  Creating {spec['target_name']} -> {spec['function_name']}...")
        client.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=spec["target_name"],
            targetConfiguration={
                "mcp": {
                    "lambda": {
                        "lambdaArn": lambda_arn,
                        "toolSchema": {
                            "inlinePayload": [
                                {
                                    "name": spec["tool_name"],
                                    "description": spec["description"],
                                    "inputSchema": spec["input_schema"],
                                }
                            ]
                        },
                    }
                }
            },
            credentialProviderConfigurations=[
                {"credentialProviderType": "GATEWAY_IAM_ROLE"}
            ],
        )
        print(f"  Created {spec['target_name']}")

    print("Done registering gateway targets.")


if __name__ == "__main__":
    main()