"""
L3 - Create a Copy Error (failure simulator) Lambda.

Input event:  {"dest_bucket": str, "file_name": str}
Output:       {"file": str, "success": bool, "error": str|None}

Deliberately corrupts a copied file in the destination bucket by removing
its last 10 lines, then saves it back. Called randomly by the agent loop
to prove L2's verification step can actually detect real failures.
"""

import boto3

s3 = boto3.client("s3")


def lambda_handler(event, context):
    dest_bucket = event["dest_bucket"]
    file_name = event["file_name"]

    print(f"L3 start: corrupting {file_name} in {dest_bucket} (removing last 10 lines)")

    try:
        obj = s3.get_object(Bucket=dest_bucket, Key=file_name)
        body = obj["Body"].read().decode("utf-8")

        lines = body.splitlines(keepends=True)
        removed = min(10, len(lines))
        corrupted_lines = lines[:-10] if len(lines) > 10 else []
        corrupted_body = "".join(corrupted_lines)

        s3.put_object(
            Bucket=dest_bucket, Key=file_name, Body=corrupted_body.encode("utf-8")
        )

        print(
            f"L3: removed last {removed} lines from {file_name}, "
            f"{len(lines)} -> {len(corrupted_lines)} lines remaining"
        )

        result = {"file": file_name, "success": True, "error": None}
        print(f"L3 result: {result}")
        return result

    except Exception as exc:
        print(f"L3 ERROR corrupting {file_name}: {exc}")
        return {"file": file_name, "success": False, "error": str(exc)}