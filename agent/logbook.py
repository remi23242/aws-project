"""
Append entries to the single global LOG file in B3 (CLAUDE.md Section 3.6).

S3 has no native "append" operation, so this reads the current LOG object,
adds the new text, and writes the whole thing back. Fine at this project's
scale (a handful of files, a log of a few KB) - not meant to scale to a
huge log.

This module only knows how to talk to S3. Deciding WHAT to write (the
learner-readable entry format) lives in agent.py, per the separation of
concerns in CLAUDE.md Section 2 (deterministic control stays in the agent
loop, not scattered across helper modules).
"""

import boto3
from botocore.exceptions import ClientError

LOG_KEY = "LOG"


def append_entry(bucket, region, text):
    """Append a text block to the LOG object in the given bucket."""
    s3 = boto3.client("s3", region_name=region)

    try:
        existing = s3.get_object(Bucket=bucket, Key=LOG_KEY)["Body"].read().decode("utf-8")
    except ClientError:
        existing = ""

    updated = existing + text.rstrip("\n") + "\n"
    s3.put_object(Bucket=bucket, Key=LOG_KEY, Body=updated.encode("utf-8"))


if __name__ == "__main__":
    def load_config(path="config.env"):
        config = {}
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, _, value = line.partition("=")
                config[key.strip()] = value.strip()
        return config

    config = load_config()
    append_entry(
        config["BUCKET_LOG"],
        config["AWS_REGION"],
        "[smoke test] logbook.py wired up correctly.",
    )
    print(f"Appended a test line to LOG in {config['BUCKET_LOG']}")