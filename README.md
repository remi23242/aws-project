# AgentCore S3 File Mover (Educational Build)

An AWS AgentCore agent that moves text files from one S3 bucket to another via
Lambda, verifies each copy with SHA-256, deliberately simulates corruption to
prove failure detection, reasons over CloudWatch logs with an LLM, and writes a
full audit trail to a log file in S3.

The agent processes files **in parallel**, and the log reading + LLM reasoning
for each Lambda call runs off the critical path.

## Prerequisites

Python 3.12 and the AWS CLI, plus three tools the AgentCore CLI needs:

```
node --version                        # Node.js 20 or newer
npm install -g @aws/agentcore         # the AgentCore CLI
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"   # uv
```

## Quickstart

Python commands run out of the virtual environment, so the right interpreter
and packages are used regardless of what is on PATH. `agentcore` is a global
npm command, so it has no prefix.

```
# One-time setup
.venv\Scripts\python.exe setup\01_create_buckets.py
.venv\Scripts\python.exe setup\02_seed_files.py       # add a number to seed more, e.g. 20
.venv\Scripts\python.exe setup\03_iam_roles.py
.venv\Scripts\python.exe setup\04_deploy_lambdas.py
.venv\Scripts\python.exe setup\05_create_gateway.py
.venv\Scripts\python.exe setup\05b_add_gateway_targets.py

# Deploy to AgentCore Runtime
.venv\Scripts\python.exe setup\06_configure_runtime.py   # writes agentcore/agentcore.json
agentcore deploy                                          # builds + deploys via CDK

# Run it
agentcore invoke "{}"                                 # the deployed agent
.venv\Scripts\python.exe agent\agent.py --run-once    # the same loop, locally

# Test it
.venv\Scripts\pytest -v                               # tests the CODE, from your laptop
.venv\Scripts\python.exe cloud_tests\run_all.py       # tests the DEPLOYED agent, AWS API only

# Tear down - removes the CloudFormation stack and everything else
.venv\Scripts\python.exe cleanup\teardown.py --dry-run
.venv\Scripts\python.exe cleanup\teardown.py
.venv\Scripts\python.exe cleanup\verify_teardown.py
```

## How it is deployed

The agent is deployed with the **AgentCore CLI** (`@aws/agentcore`), which
builds the container and creates the Runtime through a CloudFormation stack
named `AgentCore-agentcoredemo-default`.

| File | Purpose |
| --- | --- |
| `agentcore/agentcore.json` | Runtime config: entrypoint, code location, execution role, environment variables. Generated from `config.env` by `setup/06_configure_runtime.py`, and git-ignored because it carries your LLM API key. |
| `agentcore/cdk/` | The CDK app the CLI deploys. Generated; you don't edit it. |
| `agent/Dockerfile` | How the container is built. |
| `agent/pyproject.toml`, `agent/uv.lock` | The container's pinned dependencies. |

Settings reach the container as **environment variables**, not as a
`config.env` baked into the image — so your API key isn't stored inside a
container in ECR.

## Where things are written

| What | Where |
| --- | --- |
| Deployed agent's console output | CloudWatch `/aws/bedrock-agentcore/runtimes/<agent-id>-DEFAULT` |
| Traces / spans | Same log group, stream `otel-rt-logs` |
| The project's audit trail | `s3://<log bucket>/LOG` |
| Lambda logs the agent reasons over | `/aws/lambda/agentcore-demo-l{1,2,3}-*` |

`agentcore invoke` does **not** run pytest, and does not build or upload anything —
it calls the image that `agentcore deploy` last pushed. See Part 12 of the guide.

## Layout

```
agent/          the agent, its Gateway client, CloudWatch reader, LLM wrapper,
                LOG writer, plus the Dockerfile and pinned deps for the container
agentcore/      AgentCore CLI project: runtime config and the generated CDK app
lambdas/        L1 copy, L2 verify, L3 corrupt
setup/          create the AWS resources, in order
tests/          pytest suite - runs the agent's code locally against real AWS
cloud_tests/    drives the DEPLOYED agent over the AWS API; imports no agent code
cleanup/        tear everything down
```

Full walkthrough: `AgentCore_Build_Guide.docx`.
