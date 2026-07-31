"""
Check that nothing this project created is still alive in AWS.

Run this after cleanup/teardown.py. It looks for every resource type the
project creates and reports GONE or STILL EXISTS for each, then exits
non-zero if anything survived - so you can be certain the account is clean
and nothing is still costing money.

Usage:
    python cleanup/verify_teardown.py
"""

import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

PROJECT_ROOT = Path(__file__).resolve().parent.parent

GONE = "[ gone   ]"
ALIVE = "[ ALIVE  ]"

LAMBDA_FUNCTIONS = [
    "agentcore-demo-l1-copy",
    "agentcore-demo-l2-verify",
    "agentcore-demo-l3-corrupt",
]
IAM_ROLES = [
    "agentcore-demo-lambda-exec-role",
    "agentcore-demo-gateway-exec-role",
    "agentcore-demo-runtime-exec-role",
]
TOOLKIT_ROLE_PREFIX = "AmazonBedrockAgentCoreSDKCodeBuild-"
AGENT_NAME = "agentcore"
GATEWAY_NAME = "agentcore-demo-gateway"
ECR_REPOSITORY = "bedrock-agentcore-agentcore"
CODEBUILD_PROJECT = "bedrock-agentcore-agentcore-builder"


def load_config(path=None):
    path = path or str(PROJECT_ROOT / "config.env")
    config = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            config[key.strip()] = value.strip()
    return config


def report(survivors, label, still_there):
    """still_there is a list of names that should not exist."""
    if still_there:
        survivors.extend(f"{label}: {name}" for name in still_there)
        print(f"{ALIVE} {label}")
        for name in still_there:
            print(f"          {name}")
    else:
        print(f"{GONE} {label}")


def main():
    config = load_config()
    region = config["AWS_REGION"]
    account_id = boto3.client("sts", region_name=region).get_caller_identity()["Account"]

    print(f"Checking account {account_id} in {region} for leftovers...\n")
    survivors = []

    control = boto3.client("bedrock-agentcore-control", region_name=region)

    runtimes = [
        r["agentRuntimeName"]
        for r in control.list_agent_runtimes().get("agentRuntimes", [])
        if r.get("agentRuntimeName") == AGENT_NAME
    ]
    report(survivors, "AgentCore Runtime agent", runtimes)

    gateways = [
        g["name"] for g in control.list_gateways().get("items", []) if g.get("name") == GATEWAY_NAME
    ]
    report(survivors, "AgentCore Gateway", gateways)

    lambda_client = boto3.client("lambda", region_name=region)
    alive = []
    for name in LAMBDA_FUNCTIONS:
        try:
            lambda_client.get_function(FunctionName=name)
            alive.append(name)
        except ClientError:
            pass
    report(survivors, "Lambda functions", alive)

    ecr = boto3.client("ecr", region_name=region)
    try:
        ecr.describe_repositories(repositoryNames=[ECR_REPOSITORY])
        repos = [ECR_REPOSITORY]
    except ClientError:
        repos = []
    report(survivors, "ECR repository", repos)

    codebuild = boto3.client("codebuild", region_name=region)
    projects = [p for p in codebuild.list_projects().get("projects", []) if p == CODEBUILD_PROJECT]
    report(survivors, "CodeBuild project", projects)

    s3 = boto3.client("s3", region_name=region)
    buckets = [
        config["BUCKET_SOURCE"],
        config["BUCKET_DEST"],
        config["BUCKET_LOG"],
        f"bedrock-agentcore-codebuild-sources-{account_id}-{region}",
    ]
    alive = []
    for bucket in buckets:
        try:
            s3.head_bucket(Bucket=bucket)
            alive.append(bucket)
        except ClientError:
            pass
    report(survivors, "S3 buckets", alive)

    iam = boto3.client("iam", region_name=region)
    alive = []
    for name in IAM_ROLES:
        try:
            iam.get_role(RoleName=name)
            alive.append(name)
        except ClientError:
            pass
    paginator = iam.get_paginator("list_roles")
    for page in paginator.paginate():
        alive.extend(
            r["RoleName"] for r in page["Roles"] if r["RoleName"].startswith(TOOLKIT_ROLE_PREFIX)
        )
    report(survivors, "IAM roles", alive)

    logs_client = boto3.client("logs", region_name=region)
    alive = []
    for prefix in [
        "/aws/lambda/agentcore-demo-",
        "/aws/bedrock-agentcore/runtimes/",
        f"/aws/codebuild/{CODEBUILD_PROJECT}",
    ]:
        for page in logs_client.get_paginator("describe_log_groups").paginate(logGroupNamePrefix=prefix):
            alive.extend(g["logGroupName"] for g in page["logGroups"])
    report(survivors, "CloudWatch log groups", alive)

    print()
    if survivors:
        print(f"{ALIVE} {len(survivors)} resource(s) still exist:")
        for item in survivors:
            print(f"          {item}")
        print("\nRe-run: python cleanup/teardown.py")
        return 1

    print(f"{GONE} Nothing left. The account is clean and this project costs nothing.")
    print("\nNote: AWSServiceRoleForBedrockAgentCoreRuntimeIdentity may remain.")
    print("      That is an AWS-managed service-linked role, it is free, and it is")
    print("      shared with any other AgentCore work in the account - leave it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
