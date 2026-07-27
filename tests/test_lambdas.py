"""
Tests for L1/L2/L3 in isolation, invoked directly via boto3 (bypassing the
Gateway) - the simplest, fastest way to verify each Lambda's own behavior.

Covers CLAUDE.md Section 3.4 test cases 1, 2, 4, 5.
"""

import hashlib

from helpers import invoke_lambda, make_test_file_content

L1 = "agentcore-demo-l1-copy"
L2 = "agentcore-demo-l2-verify"
L3 = "agentcore-demo-l3-corrupt"


def test_file_copied_successfully(config, s3, lambda_client):
    """Test case 1: a file is copied successfully."""
    file_name = "test_copy_ok.txt"
    content = make_test_file_content(tag="copy_ok")
    s3.put_object(Bucket=config["BUCKET_SOURCE"], Key=file_name, Body=content.encode())

    result = invoke_lambda(lambda_client, L1, {
        "source_bucket": config["BUCKET_SOURCE"],
        "dest_bucket": config["BUCKET_DEST"],
        "file_name": file_name,
    })

    assert result["success"] is True
    assert result["error"] is None

    copied = s3.get_object(Bucket=config["BUCKET_DEST"], Key=file_name)["Body"].read()
    assert copied.decode() == content


def test_original_and_destination_hashes_match(config, s3, lambda_client):
    """Test case 2: original and destination hashes match after a clean copy+verify."""
    file_name = "test_hashes_match.txt"
    content = make_test_file_content(tag="hashes_match")
    s3.put_object(Bucket=config["BUCKET_SOURCE"], Key=file_name, Body=content.encode())

    l1_result = invoke_lambda(lambda_client, L1, {
        "source_bucket": config["BUCKET_SOURCE"],
        "dest_bucket": config["BUCKET_DEST"],
        "file_name": file_name,
    })
    expected_hash = hashlib.sha256(content.encode()).hexdigest()
    assert l1_result["original_hash"] == expected_hash

    l2_result = invoke_lambda(lambda_client, L2, {
        "dest_bucket": config["BUCKET_DEST"],
        "file_name": file_name,
        "original_hash": l1_result["original_hash"],
    })
    assert l2_result["match"] is True
    assert l2_result["copied_hash"] == l1_result["original_hash"]


def test_l3_removes_last_10_lines(config, s3, lambda_client):
    """Test case 4: L3 removes the last 10 lines from a destination file."""
    file_name = "test_l3_corrupt.txt"
    content = make_test_file_content(num_lines=21, tag="l3")
    # Put directly into B2 - L3 only ever touches the destination bucket.
    s3.put_object(Bucket=config["BUCKET_DEST"], Key=file_name, Body=content.encode())

    result = invoke_lambda(lambda_client, L3, {
        "dest_bucket": config["BUCKET_DEST"],
        "file_name": file_name,
    })
    assert result["success"] is True

    corrupted = s3.get_object(Bucket=config["BUCKET_DEST"], Key=file_name)["Body"].read().decode()
    original_lines = content.splitlines()
    corrupted_lines = corrupted.splitlines()
    assert corrupted_lines == original_lines[:-10]


def test_l2_detects_hash_mismatch_from_l3(config, s3, lambda_client):
    """Test case 5: L2 detects the hash mismatch L3 creates."""
    file_name = "test_l2_detects_l3.txt"
    content = make_test_file_content(num_lines=21, tag="detect")
    s3.put_object(Bucket=config["BUCKET_SOURCE"], Key=file_name, Body=content.encode())

    l1_result = invoke_lambda(lambda_client, L1, {
        "source_bucket": config["BUCKET_SOURCE"],
        "dest_bucket": config["BUCKET_DEST"],
        "file_name": file_name,
    })

    invoke_lambda(lambda_client, L3, {
        "dest_bucket": config["BUCKET_DEST"],
        "file_name": file_name,
    })

    l2_result = invoke_lambda(lambda_client, L2, {
        "dest_bucket": config["BUCKET_DEST"],
        "file_name": file_name,
        "original_hash": l1_result["original_hash"],
    })
    assert l2_result["match"] is False
    assert l2_result["copied_hash"] != l1_result["original_hash"]