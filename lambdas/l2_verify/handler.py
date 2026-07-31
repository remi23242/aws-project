"""
L2 - Verify File Lambda.

Input event:  {"dest_bucket": str, "file_name": str, "original_hash": str}
Output:       {"file": str, "original_hash": str, "copied_hash": str|None, "match": bool,
               "error": str|None, "request_id": str, "log_stream_name": str}

request_id (context.aws_request_id) lets the agent fetch the EXACT CloudWatch
log entry for this specific invocation, rather than guessing from recency.
log_stream_name (context.log_stream_name) tells it exactly which stream that
entry is in, which is what keeps the lookup reliable when several copies of
this Lambda run at the same time. See l1_copy/handler.py for the full
explanation.

Reads the copied file from the destination bucket, computes its SHA-256,
and compares it against the original hash produced by L1.
"""

import hashlib

import boto3

s3 = boto3.client("s3")


def lambda_handler(event, context):
    dest_bucket = event["dest_bucket"]
    file_name = event["file_name"]
    original_hash = event["original_hash"]

    print(f"L2 start: verifying {file_name} in {dest_bucket} against hash {original_hash}")

    try:
        obj = s3.get_object(Bucket=dest_bucket, Key=file_name)
        body = obj["Body"].read()

        copied_hash = hashlib.sha256(body).hexdigest()
        match = copied_hash == original_hash

        print(f"L2: copied SHA-256 for {file_name}: {copied_hash} (match={match})")

        result = {
            "file": file_name,
            "original_hash": original_hash,
            "copied_hash": copied_hash,
            "match": match,
            "error": None,
            "request_id": context.aws_request_id,
            "log_stream_name": context.log_stream_name,
        }
        print(f"L2 result: {result}")
        return result

    except Exception as exc:
        print(f"L2 ERROR verifying {file_name}: {exc}")
        return {
            "file": file_name,
            "original_hash": original_hash,
            "copied_hash": None,
            "match": False,
            "error": str(exc),
            "request_id": context.aws_request_id,
            "log_stream_name": context.log_stream_name,
        }