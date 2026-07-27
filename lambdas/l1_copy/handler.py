"""
L1 - Copy File Lambda.

Input event:  {"source_bucket": str, "dest_bucket": str, "file_name": str}
Output:       {"file": str, "success": bool, "original_hash": str|None, "error": str|None, "request_id": str}

request_id (context.aws_request_id) lets the agent fetch the EXACT CloudWatch
log entry for this specific invocation, rather than guessing from recency.

Reads the file from the source bucket, computes its SHA-256 hash, copies it
to the destination bucket. Everything printed here shows up in CloudWatch
Logs automatically - Lambda ships stdout there for us, no setup needed.
"""

import hashlib

import boto3

s3 = boto3.client("s3")


def lambda_handler(event, context):
    source_bucket = event["source_bucket"]
    dest_bucket = event["dest_bucket"]
    file_name = event["file_name"]

    print(f"L1 start: copying {file_name} from {source_bucket} to {dest_bucket}")

    try:
        obj = s3.get_object(Bucket=source_bucket, Key=file_name)
        body = obj["Body"].read()

        original_hash = hashlib.sha256(body).hexdigest()
        print(f"L1: computed original SHA-256 for {file_name}: {original_hash}")

        s3.put_object(Bucket=dest_bucket, Key=file_name, Body=body)
        print(f"L1: copied {file_name} to {dest_bucket}")

        result = {
            "file": file_name,
            "success": True,
            "original_hash": original_hash,
            "error": None,
            "request_id": context.aws_request_id,
        }
        print(f"L1 result: {result}")
        return result

    except Exception as exc:
        print(f"L1 ERROR copying {file_name}: {exc}")
        return {
            "file": file_name,
            "success": False,
            "original_hash": None,
            "error": str(exc),
            "request_id": context.aws_request_id,
        }