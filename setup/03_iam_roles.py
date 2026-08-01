"""
Step 9: Create the IAM execution role that Lambda functions L1/L2/L3 will
run as. This role lets Lambda write its own logs to CloudWatch and read/
write only the specific S3 buckets it needs (least privilege).

Idempotent: safe to re-run - skips creation if the role already exists.

Note: the Gateway execution role and the agent's own IAM role are created
in later steps and will be added to this same file as we reach them.
"""

import json
import time

import boto3
from botocore.exceptions import ClientError

ROLE_NAME = "agentcore-demo-lambda-exec-role"
GATEWAY_ROLE_NAME = "agentcore-demo-gateway-exec-role"
RUNTIME_ROLE_NAME = "agentcore-demo-runtime-exec-role"

TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}


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


def create_lambda_exec_role(iam, config):
    try:
        role = iam.get_role(RoleName=ROLE_NAME)
        print(f"  Role {ROLE_NAME} already exists, skipping create.")
        return role["Role"]["Arn"]
    except ClientError:
        pass

    role = iam.create_role(
        RoleName=ROLE_NAME,
        AssumeRolePolicyDocument=json.dumps(TRUST_POLICY),
        Tags=[{"Key": "Project", "Value": "agentcore-demo"}],
    )
    role_arn = role["Role"]["Arn"]
    print(f"  Created role {ROLE_NAME}")

    # Managed policy: lets Lambda write its own logs to CloudWatch.
    iam.attach_role_policy(
        RoleName=ROLE_NAME,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )
    print("  Attached AWSLambdaBasicExecutionRole (CloudWatch Logs access)")

    # Inline policy: least-privilege S3 access covering exactly what
    # L1 (read B1, write B2), L2 (read B2), and L3 (read/write B2) need.
    source_bucket = config["BUCKET_SOURCE"]
    dest_bucket = config["BUCKET_DEST"]
    s3_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{source_bucket}/*",
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject"],
                "Resource": f"arn:aws:s3:::{dest_bucket}/*",
            },
        ],
    }
    iam.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName="agentcore-demo-lambda-s3-access",
        PolicyDocument=json.dumps(s3_policy),
    )
    print("  Attached inline S3 access policy (B1 read, B2 read/write)")

    # New IAM roles can take a few seconds to propagate. Creating a Lambda
    # against a too-fresh role can fail with InvalidParameterValueException.
    print("  Waiting 10s for IAM role to propagate...")
    time.sleep(10)

    return role_arn


def create_gateway_exec_role(iam, sts, config):
    """Role the AgentCore Gateway assumes to invoke L1/L2/L3 on the agent's
    behalf (outbound auth = GATEWAY_IAM_ROLE, per CLAUDE.md Section 2)."""
    account_id = sts.get_caller_identity()["Account"]
    region = config["AWS_REGION"]

    try:
        role = iam.get_role(RoleName=GATEWAY_ROLE_NAME)
        print(f"  Role {GATEWAY_ROLE_NAME} already exists, skipping create.")
        return role["Role"]["Arn"]
    except ClientError:
        pass

    # Trust policy scoped to this account + gateways in this region, so only
    # AgentCore Gateway resources we own can assume this role (prevents the
    # "confused deputy" problem where another account's gateway could).
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "GatewayAssumeRolePolicy",
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/*"
                    },
                },
            }
        ],
    }

    role = iam.create_role(
        RoleName=GATEWAY_ROLE_NAME,
        AssumeRolePolicyDocument=json.dumps(trust_policy),
        Tags=[{"Key": "Project", "Value": "agentcore-demo"}],
    )
    role_arn = role["Role"]["Arn"]
    print(f"  Created role {GATEWAY_ROLE_NAME}")

    # Lets the Gateway call our three Lambdas (L1/L2/L3) as MCP tools.
    lambda_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AmazonBedrockAgentCoreGatewayLambdaProd",
                "Effect": "Allow",
                "Action": "lambda:InvokeFunction",
                "Resource": [
                    f"arn:aws:lambda:{region}:{account_id}:function:agentcore-demo-l1-copy",
                    f"arn:aws:lambda:{region}:{account_id}:function:agentcore-demo-l2-verify",
                    f"arn:aws:lambda:{region}:{account_id}:function:agentcore-demo-l3-corrupt",
                ],
            }
        ],
    }
    iam.put_role_policy(
        RoleName=GATEWAY_ROLE_NAME,
        PolicyName="agentcore-demo-gateway-lambda-invoke",
        PolicyDocument=json.dumps(lambda_policy),
    )
    print("  Attached inline policy: invoke L1/L2/L3")

    print("  Waiting 10s for IAM role to propagate...")
    time.sleep(10)

    return role_arn


def create_runtime_exec_role(iam, sts, config):
    """Pre-create the AgentCore Runtime execution role ourselves, fully
    permissioned, BEFORE `agentcore deploy` ever runs.
    setup/06_configure_runtime.py writes this role's ARN into
    agentcore/agentcore.json as executionRoleArn, so the deployed container
    has everything it needs on the FIRST deploy - no auto-created
    bare-minimum role, no redeploy-after-fixing-permissions needed. Combines
    AWS's documented baseline Runtime execution permissions (ECR pull,
    CloudWatch, X-Ray, Bedrock invoke, workload tokens) with what THIS agent
    specifically needs (S3 buckets, Gateway, reading L1/L2/L3's CloudWatch
    logs)."""
    account_id = sts.get_caller_identity()["Account"]
    region = config["AWS_REGION"]

    try:
        role = iam.get_role(RoleName=RUNTIME_ROLE_NAME)
        print(f"  Role {RUNTIME_ROLE_NAME} already exists, skipping create.")
        return role["Role"]["Arn"]
    except ClientError:
        pass

    # Required trust policy for any AgentCore Runtime execution role.
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AssumeRolePolicy",
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"},
                },
            }
        ],
    }

    role = iam.create_role(
        RoleName=RUNTIME_ROLE_NAME,
        AssumeRolePolicyDocument=json.dumps(trust_policy),
        Tags=[{"Key": "Project", "Value": "agentcore-demo"}],
    )
    role_arn = role["Role"]["Arn"]
    print(f"  Created role {RUNTIME_ROLE_NAME}")

    policy = {
        "Version": "2012-10-17",
        "Statement": [
            # --- AWS's documented baseline Runtime execution permissions ---
            {
                "Sid": "ECRImageAccess",
                "Effect": "Allow",
                "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
                "Resource": f"arn:aws:ecr:{region}:{account_id}:repository/*",
            },
            {
                "Sid": "ECRTokenAccess",
                "Effect": "Allow",
                "Action": "ecr:GetAuthorizationToken",
                "Resource": "*",
            },
            {
                "Sid": "RuntimeLogGroups",
                "Effect": "Allow",
                "Action": ["logs:DescribeLogStreams", "logs:CreateLogGroup"],
                "Resource": f"arn:aws:logs:{region}:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*",
            },
            {
                "Sid": "RuntimeLogStreams",
                "Effect": "Allow",
                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": f"arn:aws:logs:{region}:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*",
            },
            {
                "Sid": "RuntimeLogResourcePolicy",
                "Effect": "Allow",
                "Action": "logs:PutResourcePolicy",
                "Resource": f"arn:aws:logs:{region}:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*",
            },
            {
                "Sid": "DescribeAllLogGroups",
                "Effect": "Allow",
                "Action": "logs:DescribeLogGroups",
                "Resource": f"arn:aws:logs:{region}:{account_id}:log-group:*",
            },
            {
                "Sid": "TraceAccess",
                "Effect": "Allow",
                "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"],
                "Resource": "*",
            },
            {
                "Sid": "RuntimeMetrics",
                "Effect": "Allow",
                "Action": "cloudwatch:PutMetricData",
                "Resource": "*",
                "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
            },
            {
                "Sid": "GetAgentAccessToken",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                ],
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default",
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default/workload-identity/*",
                ],
            },
            {
                "Sid": "BedrockModelInvocation",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:Converse"],
                "Resource": ["arn:aws:bedrock:*::foundation-model/*", f"arn:aws:bedrock:{region}:{account_id}:*"],
            },
            # --- What THIS agent specifically needs ---
            {
                "Sid": "S3BucketAccess",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
                "Resource": [
                    f"arn:aws:s3:::{config['BUCKET_SOURCE']}",
                    f"arn:aws:s3:::{config['BUCKET_SOURCE']}/*",
                    f"arn:aws:s3:::{config['BUCKET_DEST']}",
                    f"arn:aws:s3:::{config['BUCKET_DEST']}/*",
                    f"arn:aws:s3:::{config['BUCKET_LOG']}",
                    f"arn:aws:s3:::{config['BUCKET_LOG']}/*",
                ],
            },
            {
                "Sid": "GatewayAccess",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:ListGateways",
                    "bedrock-agentcore:GetGateway",
                    "bedrock-agentcore:InvokeGateway",
                ],
                "Resource": f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/*",
            },
            {
                "Sid": "ReadLambdaCloudWatchLogs",
                "Effect": "Allow",
                "Action": ["logs:DescribeLogStreams", "logs:GetLogEvents", "logs:FilterLogEvents"],
                "Resource": [
                    f"arn:aws:logs:{region}:{account_id}:log-group:/aws/lambda/agentcore-demo-l1-copy:*",
                    f"arn:aws:logs:{region}:{account_id}:log-group:/aws/lambda/agentcore-demo-l2-verify:*",
                    f"arn:aws:logs:{region}:{account_id}:log-group:/aws/lambda/agentcore-demo-l3-corrupt:*",
                ],
            },
        ],
    }
    iam.put_role_policy(
        RoleName=RUNTIME_ROLE_NAME,
        PolicyName="agentcore-demo-runtime-agent-access",
        PolicyDocument=json.dumps(policy),
    )
    print("  Attached inline policy: baseline Runtime permissions + S3 + Gateway + Lambda CloudWatch Logs")

    print("  Waiting 10s for IAM role to propagate...")
    time.sleep(10)

    return role_arn


def main():
    config = load_config()
    iam = boto3.client("iam", region_name=config["AWS_REGION"])
    sts = boto3.client("sts", region_name=config["AWS_REGION"])

    lambda_role_arn = create_lambda_exec_role(iam, config)
    print(f"Lambda exec role ARN: {lambda_role_arn}")

    gateway_role_arn = create_gateway_exec_role(iam, sts, config)
    print(f"Gateway exec role ARN: {gateway_role_arn}")

    runtime_role_arn = create_runtime_exec_role(iam, sts, config)
    print(f"Runtime exec role ARN: {runtime_role_arn}")
    print()
    print("Next: setup/06_configure_runtime.py picks this role up automatically")
    print("and writes it into agentcore/agentcore.json, so there is nothing to")
    print("copy by hand.")


if __name__ == "__main__":
    main()