"""
Delete EVERY AWS resource this project created, in dependency order, so
nothing keeps costing money after you're done with it.

This includes the deployed AgentCore Runtime agent, its container image and
ECR repository, and the CodeBuild project that built it - so a single run
of this script takes the account back to how it was before Part 1. You do
not need to run `agentcore destroy` separately any more.

What it removes:
    1.  The AgentCore Runtime agent and its endpoints
    2.  The AgentCore Gateway and its three targets
    3.  The three Lambda functions
    4.  The ECR repository and every image in it
    5.  The CodeBuild project that builds the container
    6.  All four S3 buckets (the three demo buckets + CodeBuild's source bucket)
    7.  Every IAM role this project caused to exist, including the one the
        AgentCore toolkit creates for CodeBuild
    8.  Every CloudWatch log group: the Lambdas', the Runtime's, CodeBuild's
    9.  The local .bedrock_agentcore.yaml and .bedrock_agentcore/ folder

Usage:
    python cleanup/teardown.py --dry-run    # list what WOULD be deleted, change nothing
    python cleanup/teardown.py              # delete, after asking you to confirm
    python cleanup/teardown.py --yes        # delete without asking

Safe to re-run - every step tolerates the resource already being gone.
"""

import argparse
import shutil
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

PROJECT_ROOT = Path(__file__).resolve().parent.parent

LAMBDA_FUNCTIONS = [
    "agentcore-demo-l1-copy",
    "agentcore-demo-l2-verify",
    "agentcore-demo-l3-corrupt",
]
LAMBDA_ROLE_NAME = "agentcore-demo-lambda-exec-role"
GATEWAY_ROLE_NAME = "agentcore-demo-gateway-exec-role"
RUNTIME_ROLE_NAME = "agentcore-demo-runtime-exec-role"
GATEWAY_NAME = "agentcore-demo-gateway"

AGENT_NAME = "agentcore"
ECR_REPOSITORY = "bedrock-agentcore-agentcore"
CODEBUILD_PROJECT = "bedrock-agentcore-agentcore-builder"

# The toolkit creates this itself during `agentcore deploy`, with a random
# suffix, so it has to be found by prefix rather than by exact name.
TOOLKIT_ROLE_PREFIX = "AmazonBedrockAgentCoreSDKCodeBuild-"

# AWS manages this one for the AgentCore service itself. It is free, it is
# shared across every AgentCore agent in the account, and deleting it can
# break other work - so it is deliberately left alone.
SERVICE_LINKED_ROLE = "AWSServiceRoleForBedrockAgentCoreRuntimeIdentity"

DRY_RUN = False


def load_config(path=None):
    """Tiny .env loader: KEY=VALUE lines. No extra dependency needed for this."""
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


def act(description):
    """Print what is about to happen, and report whether to actually do it."""
    print(f"  {'[dry-run] would delete' if DRY_RUN else 'Deleting'}: {description}")
    return not DRY_RUN


def skip(description):
    print(f"  Not found, skipping: {description}")


# --- 1. AgentCore Runtime -------------------------------------------------


def delete_agent_runtime(control, agent_name):
    """Delete the deployed Runtime agent and any endpoints it has.

    This is what `agentcore destroy` does, done directly against the API so
    it works even if .bedrock_agentcore.yaml has been lost."""
    runtimes = []
    token = None
    while True:
        kwargs = {"maxResults": 100}
        if token:
            kwargs["nextToken"] = token
        resp = control.list_agent_runtimes(**kwargs)
        runtimes.extend(resp.get("agentRuntimes", []))
        token = resp.get("nextToken")
        if not token:
            break

    match = next((r for r in runtimes if r.get("agentRuntimeName") == agent_name), None)
    if not match:
        skip(f"AgentCore Runtime agent '{agent_name}'")
        return

    runtime_id = match["agentRuntimeId"]

    # Custom endpoints have to go before the runtime itself. DEFAULT is
    # created implicitly and disappears with the runtime.
    try:
        endpoints = control.list_agent_runtime_endpoints(agentRuntimeId=runtime_id).get(
            "runtimeEndpoints", []
        )
    except ClientError:
        endpoints = []
    for endpoint in endpoints:
        name = endpoint.get("name") or endpoint.get("endpointName")
        if not name or name == "DEFAULT":
            continue
        if act(f"Runtime endpoint {name}"):
            try:
                control.delete_agent_runtime_endpoint(agentRuntimeId=runtime_id, endpointName=name)
            except ClientError as exc:
                print(f"    (endpoint {name}: {exc.response['Error']['Code']})")

    if not act(f"AgentCore Runtime agent '{agent_name}' ({runtime_id})"):
        return

    control.delete_agent_runtime(agentRuntimeId=runtime_id)

    # Deletion is asynchronous, and the ECR repository cannot be removed
    # while the runtime still references its image.
    for _ in range(40):
        try:
            control.get_agent_runtime(agentRuntimeId=runtime_id)
        except ClientError:
            print(f"  Deleted AgentCore Runtime agent: {agent_name}")
            return
        time.sleep(3)
    print("  Warning: runtime still reports as existing after 2 minutes; continuing anyway.")


# --- 2. Gateway -----------------------------------------------------------


def delete_gateway(client, name):
    resp = client.list_gateways()
    gateway = next((g for g in resp.get("items", []) if g.get("name") == name), None)
    if not gateway:
        skip(f"Gateway {name}")
        return

    gateway_id = gateway["gatewayId"]
    targets = client.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
    for target in targets:
        if act(f"gateway target {target['name']}"):
            client.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=target["targetId"])

    if not act(f"Gateway {name}"):
        return

    # Target deletion is async - poll until the gateway actually reports
    # zero targets before trying to delete the gateway itself, otherwise
    # AWS rejects it with "has targets associated with it".
    for _ in range(15):
        remaining = client.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
        if not remaining:
            break
        print(f"    waiting for {len(remaining)} target(s) to finish deleting...")
        time.sleep(3)

    client.delete_gateway(gatewayIdentifier=gateway_id)
    print(f"  Deleted gateway: {name}")


# --- 3. Lambdas -----------------------------------------------------------


def delete_lambda_functions(lambda_client):
    for function_name in LAMBDA_FUNCTIONS:
        try:
            lambda_client.get_function(FunctionName=function_name)
        except ClientError:
            skip(f"Lambda {function_name}")
            continue
        if act(f"Lambda {function_name}"):
            lambda_client.delete_function(FunctionName=function_name)


# --- 4/5. ECR and CodeBuild ----------------------------------------------


def delete_ecr_repository(ecr, repository_name):
    try:
        ecr.describe_repositories(repositoryNames=[repository_name])
    except ClientError:
        skip(f"ECR repository {repository_name}")
        return
    if act(f"ECR repository {repository_name} (and every image in it)"):
        # force=True because a repository holding images can't be deleted.
        ecr.delete_repository(repositoryName=repository_name, force=True)


def delete_codebuild_project(codebuild, project_name):
    if project_name not in codebuild.list_projects().get("projects", []):
        skip(f"CodeBuild project {project_name}")
        return
    if act(f"CodeBuild project {project_name}"):
        codebuild.delete_project(name=project_name)


# --- 6. S3 ----------------------------------------------------------------


def empty_and_delete_bucket(s3, bucket_name):
    try:
        s3.head_bucket(Bucket=bucket_name)
    except ClientError:
        skip(f"bucket {bucket_name}")
        return

    if not act(f"bucket {bucket_name} (and all its contents)"):
        return

    paginator = s3.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket_name):
        objects = [{"Key": v["Key"], "VersionId": v["VersionId"]} for v in page.get("Versions", [])]
        objects += [{"Key": v["Key"], "VersionId": v["VersionId"]} for v in page.get("DeleteMarkers", [])]
        if objects:
            s3.delete_objects(Bucket=bucket_name, Delete={"Objects": objects})

    s3.delete_bucket(Bucket=bucket_name)
    print(f"  Deleted bucket: {bucket_name}")


# --- 7. IAM ---------------------------------------------------------------


def delete_role_completely(iam, role_name):
    try:
        iam.get_role(RoleName=role_name)
    except ClientError:
        skip(f"IAM role {role_name}")
        return

    if not act(f"IAM role {role_name}"):
        return

    for policy in iam.list_attached_role_policies(RoleName=role_name)["AttachedPolicies"]:
        iam.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])
    for policy_name in iam.list_role_policies(RoleName=role_name)["PolicyNames"]:
        iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)

    iam.delete_role(RoleName=role_name)
    print(f"  Deleted role: {role_name}")


def delete_toolkit_roles(iam, prefix):
    """The AgentCore toolkit names its CodeBuild role with a random suffix,
    so match on the prefix instead of an exact name."""
    found = False
    paginator = iam.get_paginator("list_roles")
    for page in paginator.paginate():
        for role in page["Roles"]:
            if role["RoleName"].startswith(prefix):
                found = True
                delete_role_completely(iam, role["RoleName"])
    if not found:
        skip(f"IAM roles starting with {prefix}")


# --- 8. CloudWatch --------------------------------------------------------


def delete_log_groups(logs_client, extra_prefixes):
    groups = []
    for function_name in LAMBDA_FUNCTIONS:
        groups.append(f"/aws/lambda/{function_name}")

    # Runtime and CodeBuild log groups have generated names, so discover them.
    for prefix in extra_prefixes:
        paginator = logs_client.get_paginator("describe_log_groups")
        for page in paginator.paginate(logGroupNamePrefix=prefix):
            groups.extend(g["logGroupName"] for g in page["logGroups"])

    for log_group in dict.fromkeys(groups):  # dedupe, keep order
        try:
            logs_client.describe_log_groups(logGroupNamePrefix=log_group, limit=1)
        except ClientError:
            pass
        if act(f"log group {log_group}"):
            try:
                logs_client.delete_log_group(logGroupName=log_group)
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "ResourceNotFoundException":
                    raise
                skip(f"log group {log_group}")


# --- 9. Local toolkit files ----------------------------------------------


def delete_local_toolkit_files():
    yaml_file = PROJECT_ROOT / ".bedrock_agentcore.yaml"
    build_dir = PROJECT_ROOT / ".bedrock_agentcore"

    if yaml_file.exists():
        if act(f"local file {yaml_file.name}"):
            yaml_file.unlink()
    else:
        skip(f"local file {yaml_file.name}")

    if build_dir.exists():
        if act(f"local folder {build_dir.name}/"):
            shutil.rmtree(build_dir, ignore_errors=True)
    else:
        skip(f"local folder {build_dir.name}/")


def confirm(region, account_id):
    print()
    print("=" * 74)
    print(f"About to DELETE every resource of this project in {region}, account {account_id}.")
    print("This cannot be undone. The S3 buckets and their contents go too.")
    print("=" * 74)
    try:
        answer = input("Type 'delete' to continue, anything else to abort: ").strip().lower()
    except EOFError:
        answer = ""
    if answer != "delete":
        print("Aborted. Nothing was deleted.")
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="Delete every AWS resource this project created.")
    parser.add_argument("--dry-run", action="store_true", help="list what would be deleted, change nothing")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--keep-local", action="store_true", help="leave .bedrock_agentcore.yaml in place")
    args = parser.parse_args()

    global DRY_RUN
    DRY_RUN = args.dry_run

    config = load_config()
    region = config["AWS_REGION"]

    sts = boto3.client("sts", region_name=region)
    account_id = sts.get_caller_identity()["Account"]

    if not DRY_RUN and not args.yes and not confirm(region, account_id):
        return 1

    s3 = boto3.client("s3", region_name=region)
    lambda_client = boto3.client("lambda", region_name=region)
    iam = boto3.client("iam", region_name=region)
    logs_client = boto3.client("logs", region_name=region)
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    ecr = boto3.client("ecr", region_name=region)
    codebuild = boto3.client("codebuild", region_name=region)

    print("\n[1/9] AgentCore Runtime agent...")
    delete_agent_runtime(control, AGENT_NAME)

    print("\n[2/9] Gateway + targets...")
    delete_gateway(control, GATEWAY_NAME)

    print("\n[3/9] Lambda functions...")
    delete_lambda_functions(lambda_client)

    print("\n[4/9] ECR repository + images...")
    delete_ecr_repository(ecr, ECR_REPOSITORY)

    print("\n[5/9] CodeBuild project...")
    delete_codebuild_project(codebuild, CODEBUILD_PROJECT)

    print("\n[6/9] S3 buckets...")
    empty_and_delete_bucket(s3, config["BUCKET_SOURCE"])
    empty_and_delete_bucket(s3, config["BUCKET_DEST"])
    empty_and_delete_bucket(s3, config["BUCKET_LOG"])
    # Created by `agentcore deploy` to hand the source to CodeBuild.
    empty_and_delete_bucket(s3, f"bedrock-agentcore-codebuild-sources-{account_id}-{region}")

    print("\n[7/9] IAM roles...")
    delete_role_completely(iam, LAMBDA_ROLE_NAME)
    delete_role_completely(iam, GATEWAY_ROLE_NAME)
    delete_role_completely(iam, RUNTIME_ROLE_NAME)
    delete_toolkit_roles(iam, TOOLKIT_ROLE_PREFIX)
    print(f"  Left alone (AWS-managed, free, shared): {SERVICE_LINKED_ROLE}")

    print("\n[8/9] CloudWatch log groups...")
    delete_log_groups(
        logs_client,
        extra_prefixes=[
            "/aws/bedrock-agentcore/runtimes/",
            f"/aws/codebuild/{CODEBUILD_PROJECT}",
        ],
    )

    print("\n[9/9] Local toolkit files...")
    if args.keep_local:
        print("  Skipped (--keep-local).")
    else:
        delete_local_toolkit_files()

    print()
    if DRY_RUN:
        print("Dry run complete - nothing was changed.")
        print("Run without --dry-run to actually delete all of the above.")
    else:
        print("Teardown complete. Verify with:")
        print("  python cleanup/verify_teardown.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
