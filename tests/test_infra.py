"""
Extra coverage beyond the 10 required cases, per CLAUDE.md Section 3.4's
"plus any additional tests needed to cover every developed function."
"""

import gateway_client
import logbook


def test_logbook_appends_without_overwriting(config, s3):
    marker_a = "[test] logbook marker A"
    marker_b = "[test] logbook marker B"

    logbook.append_entry(config["BUCKET_LOG"], config["AWS_REGION"], marker_a)
    logbook.append_entry(config["BUCKET_LOG"], config["AWS_REGION"], marker_b)

    content = s3.get_object(Bucket=config["BUCKET_LOG"], Key="LOG")["Body"].read().decode()

    assert marker_a in content
    assert marker_b in content
    assert content.index(marker_a) < content.index(marker_b)


def test_gateway_exposes_all_three_tools(config):
    gateway_url = gateway_client.get_gateway_url(config["AWS_REGION"])
    mcp_client = gateway_client.get_gateway_mcp_client(gateway_url, config["AWS_REGION"])

    with mcp_client:
        names = [t.tool_name for t in mcp_client.list_tools_sync()]

    assert any(n.endswith("copy_file") for n in names)
    assert any(n.endswith("verify_file") for n in names)
    assert any(n.endswith("corrupt_file") for n in names)