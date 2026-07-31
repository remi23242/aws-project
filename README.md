# AgentCore S3 File Mover (Educational Build)

An AWS AgentCore agent that moves text files from one S3 bucket to another via
Lambda, verifies each copy with SHA-256, deliberately simulates corruption to
prove failure detection, reasons over CloudWatch logs with an LLM, and writes a
full audit trail to a log file in S3.

The agent processes files **in parallel**, and the log reading + LLM reasoning
for each Lambda call runs off the critical path.

## Quickstart

Every command runs out of the virtual environment, so the right interpreter
and packages are used regardless of what is on PATH.

```
# One-time setup
.venv\Scripts\python.exe setup\01_create_buckets.py
.venv\Scripts\python.exe setup\02_seed_files.py       # add a number to seed more, e.g. 20
.venv\Scripts\python.exe setup\03_iam_roles.py
.venv\Scripts\python.exe setup\04_deploy_lambdas.py
.venv\Scripts\python.exe setup\05_create_gateway.py
.venv\Scripts\python.exe setup\05b_add_gateway_targets.py

# Deploy to AgentCore Runtime (03_iam_roles.py prints the execution role ARN)
.venv\Scripts\agentcore configure -e agent/agent.py --execution-role <ARN>
.venv\Scripts\agentcore deploy

# Run it
.venv\Scripts\agentcore invoke "{}"                   # the deployed agent
.venv\Scripts\python.exe agent\agent.py --run-once    # the same loop, locally

# Test it
.venv\Scripts\pytest -v                               # tests the CODE, from your laptop
.venv\Scripts\python.exe cloud_tests\run_all.py       # tests the DEPLOYED agent, AWS API only

# Tear down - removes the Runtime agent, ECR, CodeBuild and everything else
.venv\Scripts\python.exe cleanup\teardown.py --dry-run
.venv\Scripts\python.exe cleanup\teardown.py
.venv\Scripts\python.exe cleanup\verify_teardown.py
```

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
agent/          the agent, its Gateway client, CloudWatch reader, LLM wrapper, LOG writer
lambdas/        L1 copy, L2 verify, L3 corrupt
setup/          create the AWS resources, in order
tests/          pytest suite - runs the agent's code locally against real AWS
cloud_tests/    drives the DEPLOYED agent over the AWS API; imports no agent code
cleanup/        tear everything down
```

Full walkthrough: `AgentCore_Project_Guide.docx`.
