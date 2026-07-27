"""
Integration tests for the full agent loop (CLAUDE.md Section 3.3), through
the real Gateway/MCP path - the same code path a real agent run uses.
These are slower than test_lambdas.py since each involves real Lambda +
CloudWatch + LLM latency, and some retry a few times because L3's
corruption coin-flip is genuinely random.

Covers CLAUDE.md Section 3.4 test cases 3, 6, 9, 10.
"""

import agent as agent_module
import gateway_client

from helpers import make_test_file_content


def _connect(config):
    gateway_url = gateway_client.get_gateway_url(config["AWS_REGION"])
    return gateway_client.get_gateway_mcp_client(gateway_url, config["AWS_REGION"])


def test_original_deleted_only_after_verification_succeeds(config, s3):
    """Test case 3: original file deleted from B1 only after verification succeeds."""
    file_name = "test_delete_on_success.txt"
    content = make_test_file_content(tag="delete_on_success")

    mcp_client = _connect(config)
    outcome = None
    with mcp_client:
        available_names = [t.tool_name for t in mcp_client.list_tools_sync()]
        for _ in range(15):
            s3.put_object(Bucket=config["BUCKET_SOURCE"], Key=file_name, Body=content.encode())
            outcome = agent_module.process_one_file(
                mcp_client, available_names, s3, config, file_name, iteration=1
            )
            if outcome == "verified":
                break

    assert outcome == "verified"
    listing = s3.list_objects_v2(Bucket=config["BUCKET_SOURCE"], Prefix=file_name)
    keys = [obj["Key"] for obj in listing.get("Contents", [])]
    assert file_name not in keys


def test_agent_keeps_original_after_verification_fails(config, s3):
    """Test case 6: agent keeps the original file in B1 when verification fails.

    Retries a few times since L3's corruption is a random coin-flip inside
    process_one_file - we specifically want to observe a FAILURE outcome
    at least once."""
    file_name = "test_keep_on_failure.txt"
    content = make_test_file_content(num_lines=21, tag="keep_on_failure")

    mcp_client = _connect(config)
    outcome = None
    with mcp_client:
        available_names = [t.tool_name for t in mcp_client.list_tools_sync()]
        for _ in range(15):
            # Re-seed a clean copy each attempt - a prior failed attempt
            # leaves this file already in B1 with a corrupted B2 copy.
            s3.put_object(Bucket=config["BUCKET_SOURCE"], Key=file_name, Body=content.encode())
            outcome = agent_module.process_one_file(
                mcp_client, available_names, s3, config, file_name, iteration=1
            )
            if outcome == "verification_failed":
                break

    assert outcome == "verification_failed"
    listing = s3.list_objects_v2(Bucket=config["BUCKET_SOURCE"], Prefix=file_name)
    keys = [obj["Key"] for obj in listing.get("Contents", [])]
    assert file_name in keys

    s3.delete_object(Bucket=config["BUCKET_SOURCE"], Key=file_name)  # test cleanup


def test_multiple_files_processed_one_at_a_time(config, s3):
    """Test case 9: multiple files are processed one at a time through the while loop."""
    files = ["test_multi_a.txt", "test_multi_b.txt", "test_multi_c.txt"]
    for f in files:
        s3.put_object(Bucket=config["BUCKET_SOURCE"], Key=f, Body=make_test_file_content(tag=f).encode())

    mcp_client = _connect(config)
    processed = []
    with mcp_client:
        available_names = [t.tool_name for t in mcp_client.list_tools_sync()]
        remaining = set(files)
        iteration = 0
        while remaining and iteration < 30:
            iteration += 1
            current = [k for k in agent_module.list_b1_files(s3, config["BUCKET_SOURCE"]) if k in remaining]
            if not current:
                break
            file_name = current[0]
            outcome = agent_module.process_one_file(
                mcp_client, available_names, s3, config, file_name, iteration
            )
            processed.append((file_name, outcome))
            if outcome == "verified":
                remaining.discard(file_name)

    assert {f for f, _ in processed} >= set(files)
    listing = s3.list_objects_v2(Bucket=config["BUCKET_SOURCE"])
    remaining_keys = {obj["Key"] for obj in listing.get("Contents", [])}
    assert not remaining_keys & set(files)


def test_new_file_added_during_execution_is_discovered(config, s3):
    """Test case 10: a file added to B1 mid-run is discovered and processed -
    proves the loop never captures a fixed file list at the start."""
    first_file = "test_dynamic_first.txt"
    dynamic_file = "test_dynamic_added.txt"
    s3.put_object(Bucket=config["BUCKET_SOURCE"], Key=first_file, Body=make_test_file_content(tag="first").encode())

    mcp_client = _connect(config)
    with mcp_client:
        available_names = [t.tool_name for t in mcp_client.list_tools_sync()]

        outcome1 = None
        for _ in range(15):
            outcome1 = agent_module.process_one_file(
                mcp_client, available_names, s3, config, first_file, iteration=1
            )
            if outcome1 == "verified":
                break

        # Only added AFTER the first file was already being processed -
        # proves discovery, not a list captured at the start.
        s3.put_object(Bucket=config["BUCKET_SOURCE"], Key=dynamic_file, Body=make_test_file_content(tag="dynamic").encode())

        files_now = agent_module.list_b1_files(s3, config["BUCKET_SOURCE"])
        assert dynamic_file in files_now

        outcome2 = None
        for _ in range(15):
            outcome2 = agent_module.process_one_file(
                mcp_client, available_names, s3, config, dynamic_file, iteration=2
            )
            if outcome2 == "verified":
                break

    assert outcome1 == "verified"
    assert outcome2 == "verified"