"""
Step: write agentcore/agentcore.json, the AgentCore CLI's project config.

The CLI deploys the Runtime from this file. It needs to know three things
that are specific to your account:

  * the execution role ARN (created by setup/03_iam_roles.py)
  * where the agent's code lives and which file is its entrypoint
  * the settings the running container needs - region, buckets, LLM provider
    and key - which are passed as environment variables

Those settings already live in config.env, so this generates the JSON from
it rather than asking you to maintain the same values in two places.

Why environment variables rather than shipping config.env inside the image:
the old deployment copied config.env into the container, which put your LLM
API key inside an image stored in ECR. Passing them as runtime environment
variables keeps the key out of the image. agentcore/agentcore.json is
git-ignored for the same reason config.env is.

Idempotent: safe to re-run - it rewrites the file from config.env each time.

Usage:
    python setup/06_configure_runtime.py
"""

import json
import sys
from pathlib import Path

import boto3

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "agentcore" / "agentcore.json"

PROJECT_NAME = "agentcoredemo"
RUNTIME_NAME = "agentcore"
RUNTIME_ROLE_NAME = "agentcore-demo-runtime-exec-role"

# Settings the container needs. Anything not present in config.env is simply
# left out, so an unused provider's placeholder key never reaches AWS.
RUNTIME_SETTINGS = (
    "AWS_REGION",
    "BEDROCK_MODEL_ID",
    "LLM_PROVIDER",
    "LLM_MAX_CONCURRENCY",
    "GEMINI_MODEL",
    "OPENAI_MODEL",
    "GROQ_MODEL",
    "BUCKET_SOURCE",
    "BUCKET_DEST",
    "BUCKET_LOG",
    "AGENT_MAX_WORKERS",
    "AGENT_REASONING_MODE",
)

# Only the key for the provider actually in use gets sent.
PROVIDER_KEYS = {
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "bedrock": None,  # Bedrock authenticates with the execution role
}


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


def main():
    config = load_config()
    region = config["AWS_REGION"]

    iam = boto3.client("iam", region_name=region)
    try:
        role_arn = iam.get_role(RoleName=RUNTIME_ROLE_NAME)["Role"]["Arn"]
    except Exception:
        raise SystemExit(
            f"Runtime execution role '{RUNTIME_ROLE_NAME}' not found.\n"
            f"Run setup/03_iam_roles.py first - it creates the role fully permissioned."
        )

    env_vars = [
        {"name": key, "value": config[key]}
        for key in RUNTIME_SETTINGS
        if config.get(key)
    ]

    provider = config.get("LLM_PROVIDER", "bedrock").lower()
    key_name = PROVIDER_KEYS.get(provider)
    if key_name:
        value = config.get(key_name)
        if not value or value == "changeme":
            raise SystemExit(
                f"LLM_PROVIDER is '{provider}' but {key_name} is not set in config.env."
            )
        env_vars.append({"name": key_name, "value": value})

    document = {
        "$schema": "https://schema.agentcore.aws.dev/v1/agentcore.json",
        "name": PROJECT_NAME,
        "version": 1,
        "managedBy": "CDK",
        "tags": {"agentcore:project-name": PROJECT_NAME},
        "runtimes": [
            {
                "name": RUNTIME_NAME,
                "build": "Container",
                # agent.py stays exactly where it is - the CLI builds from
                # this directory rather than requiring the code to be moved.
                "entrypoint": "agent.py",
                "codeLocation": "agent",
                "runtimeVersion": "PYTHON_3_12",
                "networkMode": "PUBLIC",
                "protocol": "HTTP",
                "instrumentation": {"enableOtel": True},
                "executionRoleArn": role_arn,
                "envVars": env_vars,
            }
        ],
        "memories": [],
        "knowledgeBases": [],
        "credentials": [],
        "evaluators": [],
        "onlineEvalConfigs": [],
        "agentCoreGateways": [],
        "policyEngines": [],
        "configBundles": [],
        "abTests": [],
        "harnesses": [],
        "datasets": [],
        "payments": [],
    }

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    shown = [v["name"] for v in env_vars]
    print(f"Wrote {CONFIG_PATH.relative_to(PROJECT_ROOT)}")
    print(f"  runtime            : {RUNTIME_NAME}")
    print(f"  code location      : agent/  (entrypoint agent.py)")
    print(f"  execution role     : {role_arn}")
    print(f"  environment vars   : {len(env_vars)} ({', '.join(shown)})")
    print()
    print("Next:")
    print("  agentcore deploy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
