"""
Delete every AWS resource this project created, in dependency order, so
nothing keeps costing money after you're done with it.

Does NOT handle the AgentCore Runtime agent, its ECR repo/images, or its
CodeBuild project - those are created and best destroyed by the toolkit
itself:
    .venv\\Scripts\\agentcore destroy --agent agentcore --force --delete-ecr-repo

Safe to re-run - every step tolerates the resource already being gone.
"""

import time

import boto3
from botocore.exceptions import ClientError

LAMBDA_FUNCTIONS = [
    "agentcore-demo-l1-copy",
    "agentcore-demo-l2-verify",
    "agentcore-demo-l3-corrupt",
]
LAMBDA_ROLE_NAME = "agentcore-demo-lambda-exec-role"
GATEWAY_ROLE_NAME = "agentcore-demo-gateway-exec-role"
RUNTIME_ROLE_NAME = "agentcore-demo-runtime-exec-role"
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


def delete_gateway(client, name):
    resp = client.list_gateways()
    gateway = next((g for g in resp.get("items", []) if g.get("name") == name), None)
    if not gateway:
        print(f"  Gateway {name} not found, skipping.")
        return

    gateway_id = gateway["gatewayId"]
    targets = client.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
    for target in targets:
        client.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=target["targetId"])
        print(f"  Deleted gateway target: {target['name']}")

    # Target deletion is async - poll until the gateway actually reports
    # zero targets before trying to delete the gateway itself, otherwise
    # AWS rejects it with "has targets associated with it".
    for _ in range(15):
        remaining = client.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
        if not remaining:
            break
        print(f"  Waiting for {len(remaining)} target(s) to finish deleting...")
        time.sleep(3)

    client.delete_gateway(gatewayIdentifier=gateway_id)
    print(f"  Deleted gateway: {name}")


def delete_lambda_functions(lambda_client):
    for function_name in LAMBDA_FUNCTIONS:
        try:
            lambda_client.delete_function(FunctionName=function_name)
            print(f"  Deleted Lambda: {function_name}")
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ResourceNotFoundException":
                print(f"  Lambda {function_name} not found, skipping.")
            else:
                raise


def empty_and_delete_bucket(s3, bucket_name):
    try:
        s3.head_bucket(Bucket=bucket_name)
    except ClientError:
        print(f"  Bucket {bucket_name} not found, skipping.")
        return

    paginator = s3.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket_name):
        objects = [{"Key": v["Key"], "VersionId": v["VersionId"]} for v in page.get("Versions", [])]
        objects += [{"Key": v["Key"], "VersionId": v["VersionId"]} for v in page.get("DeleteMarkers", [])]
        if objects:
            s3.delete_objects(Bucket=bucket_name, Delete={"Objects": objects})

    s3.delete_bucket(Bucket=bucket_name)
    print(f"  Emptied and deleted bucket: {bucket_name}")


def delete_role_completely(iam, role_name):
    try:
        iam.get_role(RoleName=role_name)
    except ClientError:
        print(f"  Role {role_name} not found, skipping.")
        return

    for policy in iam.list_attached_role_policies(RoleName=role_name)["AttachedPolicies"]:
        iam.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])
    for policy_name in iam.list_role_policies(RoleName=role_name)["PolicyNames"]:
        iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)

    iam.delete_role(RoleName=role_name)
    print(f"  Deleted role: {role_name}")


def delete_log_groups(logs_client):
    for function_name in LAMBDA_FUNCTIONS:
        log_group = f"/aws/lambda/{function_name}"
        try:
            logs_client.delete_log_group(logGroupName=log_group)
            print(f"  Deleted log group: {log_group}")
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ResourceNotFoundException":
                print(f"  Log group {log_group} not found, skipping.")
            else:
                raise


def main():
    config = load_config()
    region = config["AWS_REGION"]

    s3 = boto3.client("s3", region_name=region)
    lambda_client = boto3.client("lambda", region_name=region)
    iam = boto3.client("iam", region_name=region)
    logs_client = boto3.client("logs", region_name=region)
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    sts = boto3.client("sts", region_name=region)
    account_id = sts.get_caller_identity()["Account"]

    print("Deleting Gateway + targets...")
    delete_gateway(control, GATEWAY_NAME)

    print("Deleting Lambda functions...")
    delete_lambda_functions(lambda_client)

    print("Deleting S3 buckets...")
    empty_and_delete_bucket(s3, config["BUCKET_SOURCE"])
    empty_and_delete_bucket(s3, config["BUCKET_DEST"])
    empty_and_delete_bucket(s3, config["BUCKET_LOG"])

    # Created by `agentcore deploy` for CodeBuild source uploads - not
    # covered by `agentcore destroy`.
    empty_and_delete_bucket(s3, f"bedrock-agentcore-codebuild-sources-{account_id}-{region}")

    print("Deleting IAM roles...")
    delete_role_completely(iam, LAMBDA_ROLE_NAME)
    delete_role_completely(iam, GATEWAY_ROLE_NAME)
    delete_role_completely(iam, RUNTIME_ROLE_NAME)

    print("Deleting CloudWatch log groups...")
    delete_log_groups(logs_client)

    print()
    print("Done. Remaining manual step (Runtime/ECR/CodeBuild project):")
    print("  .venv\\Scripts\\agentcore destroy --agent agentcore --force --delete-ecr-repo")


if __name__ == "__main__":
    main()