# Container image for the AgentCore Runtime agent.
#
# The AgentCore CLI builds this in the cloud (no Docker needed on your
# laptop) and deploys the resulting image. It is based on the CLI's own
# generated template, kept in the repository so the build is explicit and
# reviewable rather than generated behind the scenes.
#
# Note what is NOT here: config.env. The old deployment copied it into the
# image, which put the LLM API key inside a container stored in ECR. The
# settings now arrive as environment variables declared in
# agentcore/agentcore.json, which setup/06_configure_runtime.py generates
# from config.env.

FROM public.ecr.aws/docker/library/python:3.12-slim-trixie

RUN pip install --no-cache-dir uv

WORKDIR /app

ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_PROGRESS=1 \
    PYTHONUNBUFFERED=1 \
    DOCKER_CONTAINER=1

RUN useradd -m -u 1000 bedrock_agentcore

# Dependencies first, so this layer is cached and only re-runs when
# pyproject.toml or uv.lock actually change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=bedrock_agentcore:bedrock_agentcore . .
RUN uv sync --frozen --no-dev

# `uv sync` installs into a project virtualenv at /app/.venv rather than the
# system interpreter, so nothing it installed is on PATH by default. Without
# this the container fails to start with:
#     exec: "opentelemetry-instrument": executable file not found in $PATH
# Putting the venv first also makes `python` below the one that has the
# dependencies.
ENV PATH="/app/.venv/bin:$PATH"

USER bedrock_agentcore

# 8080 is the port BedrockAgentCoreApp listens on for /invocations and /ping.
EXPOSE 8080 8000 9000

# agent.py at the image root, run as a module. With no --run-once argument
# it starts the app server and waits to be invoked, which is exactly what
# the Runtime expects.
CMD ["opentelemetry-instrument", "python", "-m", "agent"]
