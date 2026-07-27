"""
MCP client connection to the AgentCore Gateway, using IAM (SigV4) inbound
auth (CLAUDE.md Section 2 - no Cognito/OAuth). agent/agent.py imports
get_gateway_mcp_client() to call L1/L2/L3 as Gateway tools.

Run this file directly (`python agent/gateway_client.py`) for a quick smoke
test: lists the tools the Gateway exposes and calls copy_file once.
"""

from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
from strands.tools.mcp import MCPClient

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


def get_gateway_url(region):
    """Look up the deployed gateway's MCP endpoint URL by name."""
    import boto3

    control = boto3.client("bedrock-agentcore-control", region_name=region)
    gateways = control.list_gateways().get("items", [])
    match = next((g for g in gateways if g.get("name") == GATEWAY_NAME), None)
    if not match:
        raise RuntimeError(f"Gateway {GATEWAY_NAME} not found - run setup/05_create_gateway.py first")
    return control.get_gateway(gatewayIdentifier=match["gatewayId"])["gatewayUrl"]


def get_gateway_mcp_client(gateway_url, region):
    """Return a Strands MCPClient wired up to call the Gateway with IAM SigV4."""
    return MCPClient(
        lambda: aws_iam_streamablehttp_client(
            endpoint=gateway_url,
            aws_region=region,
            aws_service="bedrock-agentcore",
        )
    )


if __name__ == "__main__":
    config = load_config()
    region = config["AWS_REGION"]

    gateway_url = get_gateway_url(region)
    print(f"Connecting to gateway: {gateway_url}")

    client = get_gateway_mcp_client(gateway_url, region)

    with client:
        tools = client.list_tools_sync()
        names = [t.tool_name for t in tools]
        print(f"Found {len(tools)} tool(s) on the gateway:")
        for n in names:
            print(f"  - {n}")

        # The Gateway prefixes each tool with its target name (e.g.
        # "CopyFileTarget___copy_file") to avoid name collisions across
        # targets, so look up the real name instead of hardcoding it.
        copy_tool_name = next(n for n in names if n.endswith("copy_file"))

        print(f"Calling {copy_tool_name} on sample_002.txt...")
        result = client.call_tool_sync(
            tool_use_id="smoke-test-1",
            name=copy_tool_name,
            arguments={
                "source_bucket": config["BUCKET_SOURCE"],
                "dest_bucket": config["BUCKET_DEST"],
                "file_name": "sample_002.txt",
            },
        )
        print(f"{copy_tool_name} result: {result}")
