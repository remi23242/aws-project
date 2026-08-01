"""
Delete EVERY AWS resource this project created, so nothing keeps costing
money after you're done with it.

The project now creates resources two different ways, and they have to be
removed differently:

  * The Runtime agent and everything supporting it (its container image,
    ECR repository, the CodeBuild project that builds it, the encryption
    key and several IAM roles) are deployed by the AgentCore CLI through
    CloudFormation, as one stack. Those are removed by deleting the stack -
    NOT by deleting resources individually. Deleting a stack's resources
    behind its back leaves the stack stuck in DELETE_FAILED.

  * Everything else - the S3 buckets, the three Lambda functions, the
    Gateway and its targets, and the IAM roles created by setup/03 - is
    created directly with boto3, so it is removed the same way.

What it removes:
    1.  The CloudFormation stack: Runtime, ECR repo + images, KMS key,
        CodeBuild project, build Lambda, and the IAM roles CDK created
    2.  The AgentCore Gateway and its three targets
    3.  The three Lambda functions
    4.  All three S3 buckets
    5.  The IAM roles from setup/03_iam_roles.py
    6.  The Lambdas' CloudWatch log groups
    7.  Local generated files (agentcore/agentcore.json, CDK build output)

Left alone by default:
    * The CDKToolkit stack and its bucket/repository. That is CDK's shared
      bootstrap, used by any CDK project in the account - deleting it would
      break unrelated work. Pass --include-cdk-bootstrap if this account is
      only ever used for this project.

Usage:
    python cleanup/teardown.py --dry-run    # list what WOULD be deleted
    python cleanup/teardown.py              # delete, after confirming
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

# Written by setup/06_configure_runtime.py and used by the AgentCore CLI.
PROJECT_NAME = "agentcoredemo"
STACK_NAME = f"AgentCore-{PROJECT_NAME}-default"
CDK_BOOTSTRAP_STACK = "CDKToolkit"

# AWS manages this for the AgentCore service. It is free, shared across the
# account, and deleting it can break other work - so it is left alone.
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


# --- 1. The CloudFormation stack -----------------------------------------


def empty_stack_ecr_repositories(cfn, ecr, stack_name):
    """Delete every image in the stack's ECR repositories first.

    A repository that still holds images can refuse to delete, which fails
    the whole stack deletion. Emptying them first avoids a stack stuck in
    DELETE_FAILED that then has to be cleaned up by hand."""
    try:
        resources = cfn.list_stack_resources(StackName=stack_name)["StackResourceSummaries"]
    except ClientError:
        return

    for resource in resources:
        if resource["ResourceType"] != "AWS::ECR::Repository":
            continue
        repo = resource.get("PhysicalResourceId")
        if not repo:
            continue
        try:
            images = ecr.list_images(repositoryName=repo).get("imageIds", [])
        except ClientError:
            continue
        if not images:
            continue
        if act(f"{len(images)} image(s) from ECR repository {repo}"):
            ecr.batch_delete_image(repositoryName=repo, imageIds=images)


def delete_stack(cfn, ecr, stack_name, wait=True):
    """Delete a CloudFormation stack and wait for it to finish."""
    try:
        cfn.describe_stacks(StackName=stack_name)
    except ClientError:
        skip(f"CloudFormation stack {stack_name}")
        return

    empty_stack_ecr_repositories(cfn, ecr, stack_name)

    if not act(f"CloudFormation stack {stack_name} (and everything in it)"):
        return

    cfn.delete_stack(StackName=stack_name)
    if not wait:
        return

    print("    waiting for the stack to finish deleting...")
    waiter = cfn.get_waiter("stack_delete_complete")
    try:
        waiter.wait(StackName=stack_name, WaiterConfig={"Delay": 10, "MaxAttempts": 90})
        print(f"  Deleted stack: {stack_name}")
    except Exception as exc:
        print(f"  Warning: stack did not delete cleanly ({type(exc).__name__}).")
        print(f"           Check CloudFormation -> {stack_name} in the console.")


# --- 1b. Leftovers from an older starter-toolkit deployment ---------------


def delete_legacy_toolkit_leftovers(control, ecr, codebuild, runtime_name=None):
    """Remove what an earlier deployment with the deprecated Python starter
    toolkit left behind.

    That toolkit named things `bedrock-agentcore-<agent>` and
    `bedrock-agentcore-<agent>-builder`. The AgentCore CLI uses different
    names and puts everything in a CloudFormation stack, so anything still
    matching the old convention is an orphan from before the migration -
    it will sit in your account, show up in the console next to the real
    agent, and keep costing a little, until it is removed.

    The Runtime itself is only deleted when you name it with
    --legacy-runtime, because runtime names are freely chosen and this
    should never guess at deleting an agent it does not own."""
    if runtime_name:
        runtimes = control.list_agent_runtimes().get("agentRuntimes", [])
        match = next((r for r in runtimes if r.get("agentRuntimeName") == runtime_name), None)
        if match is None:
            skip(f"legacy Runtime agent '{runtime_name}'")
        elif act(f"legacy Runtime agent '{runtime_name}' ({match['agentRuntimeId']})"):
            control.delete_agent_runtime(agentRuntimeId=match["agentRuntimeId"])
            time.sleep(10)  # let it release its container image before ECR

    found = False
    for repo in ecr.describe_repositories().get("repositories", []):
        name = repo["repositoryName"]
        if not name.startswith("bedrock-agentcore-"):
            continue
        found = True
        if act(f"legacy ECR repository {name} (and its images)"):
            ecr.delete_repository(repositoryName=name, force=True)

    for project in codebuild.list_projects().get("projects", []):
        if not (project.startswith("bedrock-agentcore-") and project.endswith("-builder")):
            continue
        found = True
        if act(f"legacy CodeBuild project {project}"):
            codebuild.delete_project(name=project)

    if not found and not runtime_name:
        skip("legacy starter-toolkit resources")

    # Any runtime that is not the one this project deploys is worth pointing
    # out, without touching it.
    others = [
        r.get("agentRuntimeName")
        for r in control.list_agent_runtimes().get("agentRuntimes", [])
        if not (r.get("agentRuntimeName") or "").startswith(PROJECT_NAME)
        and r.get("agentRuntimeName") != runtime_name
    ]
    if others:
        print(f"  Note: other Runtime agents exist and were NOT touched: {', '.join(others)}")
        print("        If one is an old deployment of this project, remove it with")
        print("        --legacy-runtime <name>.")


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

    # Target deletion is async - poll until the gateway reports zero targets,
    # otherwise AWS rejects the delete with "has targets associated with it".
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


# --- 4. S3 ----------------------------------------------------------------


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


# --- 5. IAM ---------------------------------------------------------------


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


# --- 6. CloudWatch --------------------------------------------------------


def delete_log_groups(logs_client, extra_prefixes):
    groups = [f"/aws/lambda/{name}" for name in LAMBDA_FUNCTIONS]

    for prefix in extra_prefixes:
        paginator = logs_client.get_paginator("describe_log_groups")
        for page in paginator.paginate(logGroupNamePrefix=prefix):
            groups.extend(g["logGroupName"] for g in page["logGroups"])

    for log_group in dict.fromkeys(groups):  # dedupe, keep order
        if act(f"log group {log_group}"):
            try:
                logs_client.delete_log_group(logGroupName=log_group)
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "ResourceNotFoundException":
                    raise
                skip(f"log group {log_group}")


# --- 7. Local generated files --------------------------------------------


def delete_local_generated_files():
    targets = [
        PROJECT_ROOT / "agentcore" / "agentcore.json",
        PROJECT_ROOT / "agentcore" / "cdk" / "cdk.out",
        PROJECT_ROOT / "agentcore" / ".cli" / "deployed-state.json",
    ]
    for target in targets:
        label = str(target.relative_to(PROJECT_ROOT))
        if not target.exists():
            skip(f"local {label}")
            continue
        if act(f"local {label}"):
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink()


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
    parser.add_argument("--keep-local", action="store_true", help="leave generated local files in place")
    parser.add_argument(
        "--include-cdk-bootstrap",
        action="store_true",
        help="also delete the shared CDKToolkit stack (only if this account is used for nothing else)",
    )
    parser.add_argument(
        "--legacy-runtime",
        metavar="NAME",
        help="also delete a Runtime agent left by an older starter-toolkit deployment, by name",
    )
    args = parser.parse_args()

    global DRY_RUN
    DRY_RUN = args.dry_run

    config = load_config()
    region = config["AWS_REGION"]
    account_id = boto3.client("sts", region_name=region).get_caller_identity()["Account"]

    if not DRY_RUN and not args.yes and not confirm(region, account_id):
        return 1

    s3 = boto3.client("s3", region_name=region)
    lambda_client = boto3.client("lambda", region_name=region)
    iam = boto3.client("iam", region_name=region)
    logs_client = boto3.client("logs", region_name=region)
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    cfn = boto3.client("cloudformation", region_name=region)
    ecr = boto3.client("ecr", region_name=region)

    codebuild = boto3.client("codebuild", region_name=region)

    print("\n[1/8] AgentCore CloudFormation stack (Runtime, ECR, CodeBuild, KMS, IAM)...")
    delete_stack(cfn, ecr, STACK_NAME)

    print("\n[2/8] Leftovers from an older starter-toolkit deployment...")
    delete_legacy_toolkit_leftovers(control, ecr, codebuild, args.legacy_runtime)

    print("\n[3/8] Gateway + targets...")
    delete_gateway(control, GATEWAY_NAME)

    print("\n[4/8] Lambda functions...")
    delete_lambda_functions(lambda_client)

    print("\n[5/8] S3 buckets...")
    empty_and_delete_bucket(s3, config["BUCKET_SOURCE"])
    empty_and_delete_bucket(s3, config["BUCKET_DEST"])
    empty_and_delete_bucket(s3, config["BUCKET_LOG"])

    print("\n[6/8] IAM roles...")
    delete_role_completely(iam, LAMBDA_ROLE_NAME)
    delete_role_completely(iam, GATEWAY_ROLE_NAME)
    delete_role_completely(iam, RUNTIME_ROLE_NAME)
    print(f"  Left alone (AWS-managed, free, shared): {SERVICE_LINKED_ROLE}")

    print("\n[7/8] CloudWatch log groups...")
    delete_log_groups(logs_client, extra_prefixes=["/aws/bedrock-agentcore/runtimes/"])

    print("\n[8/8] Local generated files...")
    if args.keep_local:
        print("  Skipped (--keep-local).")
    else:
        delete_local_generated_files()

    if args.include_cdk_bootstrap:
        print("\n[extra] CDK bootstrap stack...")
        delete_stack(cfn, ecr, CDK_BOOTSTRAP_STACK)
    else:
        print(f"\n[note] Left alone: the {CDK_BOOTSTRAP_STACK} stack.")
        print("       It is CDK's shared bootstrap for this account and region, used by any")
        print("       CDK project - not just this one. It costs almost nothing to keep.")
        print("       Pass --include-cdk-bootstrap to remove it as well.")

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
