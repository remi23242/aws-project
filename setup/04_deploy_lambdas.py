"""
Step 10: Deploy Lambda functions L1, L2, L3.

Zips each handler.py and creates (or updates, if it already exists) the
corresponding Lambda function using the shared execution role from
setup/03_iam_roles.py.

Idempotent: safe to re-run - updates the function code if it already exists.
Built incrementally: L1 first, L2/L3 added in later steps.
"""

import io
import zipfile

import boto3
from botocore.exceptions import ClientError


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


def zip_handler(handler_path):
    """Zip a single handler.py file in memory - Lambda requires a zip
    upload even for a single-file function."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(handler_path, arcname="handler.py")
    buf.seek(0)
    return buf.read()


def deploy_function(lambda_client, function_name, handler_path, role_arn):
    zip_bytes = zip_handler(handler_path)

    try:
        lambda_client.get_function(FunctionName=function_name)
        print(f"  {function_name} already exists, updating code...")
        lambda_client.update_function_code(
            FunctionName=function_name, ZipFile=zip_bytes
        )
    except ClientError:
        print(f"  Creating {function_name}...")
        lambda_client.create_function(
            FunctionName=function_name,
            Runtime="python3.12",
            Role=role_arn,
            Handler="handler.lambda_handler",
            Code={"ZipFile": zip_bytes},
            Timeout=30,
            MemorySize=128,
            Tags={"Project": "agentcore-demo"},
        )
    print(f"  {function_name} deployed")


def main():
    config = load_config()
    region = config["AWS_REGION"]
    iam = boto3.client("iam", region_name=region)
    lambda_client = boto3.client("lambda", region_name=region)

    role_arn = iam.get_role(RoleName="agentcore-demo-lambda-exec-role")["Role"]["Arn"]

    deploy_function(
        lambda_client,
        "agentcore-demo-l1-copy",
        "lambdas/l1_copy/handler.py",
        role_arn,
    )
    deploy_function(
        lambda_client,
        "agentcore-demo-l2-verify",
        "lambdas/l2_verify/handler.py",
        role_arn,
    )
    deploy_function(
        lambda_client,
        "agentcore-demo-l3-corrupt",
        "lambdas/l3_corrupt/handler.py",
        role_arn,
    )


if __name__ == "__main__":
    main()