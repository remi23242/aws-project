"""Small helpers shared across test files."""

import json


def invoke_lambda(lambda_client, function_name, payload):
    """Directly invoke a Lambda (bypassing the Gateway) - the simplest,
    fastest way to test one Lambda's own behavior in isolation."""
    response = lambda_client.invoke(
        FunctionName=function_name,
        Payload=json.dumps(payload).encode("utf-8"),
    )
    return json.loads(response["Payload"].read())


def make_test_file_content(num_lines=20, tag="test"):
    lines = [f"{tag} line {i}" for i in range(1, num_lines + 1)]
    return "\n".join(lines) + "\n"