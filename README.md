# AgentCore S3 File Mover (Educational Build)

An AWS AgentCore agent that moves text files from one S3 bucket to another via
Lambda, verifies each copy with SHA-256, deliberately simulates corruption to
prove failure detection, reasons over CloudWatch logs with a Bedrock LLM, and
writes a full audit trail to a log file. See [CLAUDE.md](CLAUDE.md) for the
full spec and build log in [docs/STEPS.md](docs/STEPS.md).

## Quickstart (filled in as the build progresses)

- Deploy everything: `python setup/deploy_all.py`
- Run the agent: `agentcore invoke` (or `python run.py`)
- Run tests: `pytest`
- Tear down: `python cleanup/teardown.py`
