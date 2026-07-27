"""
Step 13: Create the AgentCore Gateway with IAM (SigV4) inbound authorization
(CLAUDE.md Section 2 locked decision - no Cognito/OAuth).

Lambda targets (L1/L2/L3 as MCP tools) are added in Step 14, in
05b_add_gateway_targets.py, once the gateway itself exists.

Idempotent: safe to re-run - reuses the gateway if a gateway with this name
already exists.
"""

import boto3
from botocore.exceptions import ClientError

GATEWAY_NAME = "agentcore-demo-gateway"


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


def find_existing_gateway(client, name):
    resp = client.list_gateways()
    for gw in resp.get("items", []):
        if gw.get("name") == name:
            return gw
    return None


def main():
    config = load_config()
    region = config["AWS_REGION"]
    iam = boto3.client("iam", region_name=region)
    client = boto3.client("bedrock-agentcore-control", region_name=region)

    gateway_role_arn = iam.get_role(RoleName="agentcore-demo-gateway-exec-role")[
        "Role"
    ]["Arn"]

    existing = find_existing_gateway(client, GATEWAY_NAME)
    if existing:
        gateway_id = existing["gatewayId"]
        print(f"Gateway {GATEWAY_NAME} already exists, reusing it.")
        gateway = client.get_gateway(gatewayIdentifier=gateway_id)
    else:
        print(f"Creating gateway {GATEWAY_NAME} with AWS_IAM inbound auth...")
        gateway = client.create_gateway(
            name=GATEWAY_NAME,
            roleArn=gateway_role_arn,
            protocolType="MCP",
            authorizerType="AWS_IAM",
            description="AgentCore demo gateway exposing L1/L2/L3 as MCP tools",
        )
        print(f"Created gateway {GATEWAY_NAME}")

    print(f"Gateway ID: {gateway.get('gatewayId')}")
    print(f"Gateway URL: {gateway.get('gatewayUrl')}")
    print(f"Gateway status: {gateway.get('status')}")


if __name__ == "__main__":
    main()
