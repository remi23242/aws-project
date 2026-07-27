"""
Shared pytest fixtures: config loading, boto3 clients, and pre-test cleanup
of any leftover test_*.txt files from a previous (possibly interrupted) run.

Also adds agent/ to sys.path so tests can import agent.py, cloudwatch.py,
gateway_client.py, llm_wrapper.py, and logbook.py the same way agent.py
imports its own siblings.
"""

import sys
from pathlib import Path

import boto3
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "agent"))


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


@pytest.fixture(scope="session")
def config():
    return load_config()


@pytest.fixture(scope="session")
def s3(config):
    return boto3.client("s3", region_name=config["AWS_REGION"])


@pytest.fixture(scope="session")
def lambda_client(config):
    return boto3.client("lambda", region_name=config["AWS_REGION"])


@pytest.fixture(scope="session", autouse=True)
def _clean_leftover_test_files(config, s3):
    """Remove any test_*.txt objects left behind by a previous, possibly
    interrupted, test run - keeps every run starting from a clean slate.
    Only ever touches test_* keys, never the real sample_* demo files."""
    for bucket_key in ("BUCKET_SOURCE", "BUCKET_DEST"):
        bucket = config[bucket_key]
        listing = s3.list_objects_v2(Bucket=bucket, Prefix="test_")
        for obj in listing.get("Contents", []):
            s3.delete_object(Bucket=bucket, Key=obj["Key"])
    yield