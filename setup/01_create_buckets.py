"""
Step 7: Create the three S3 buckets (source B1, destination B2, log B3).

Idempotent: safe to re-run - skips buckets that already exist.
Reads bucket names + region from config.env (see config.example.env for the shape).
"""

import boto3
from botocore.exceptions import ClientError


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


def create_bucket(s3, bucket_name, region):
    """Create bucket_name if it doesn't already exist, then tag it."""
    try:
        s3.head_bucket(Bucket=bucket_name)
        print(f"  {bucket_name} already exists, skipping create.")
    except ClientError:
        if region == "us-east-1":
            # us-east-1 is the one region where you must NOT pass a
            # LocationConstraint - AWS treats it as the implicit default.
            s3.create_bucket(Bucket=bucket_name)
        else:
            s3.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={"LocationConstraint": region},
            )
        print(f"  Created {bucket_name}")

    s3.put_bucket_tagging(
        Bucket=bucket_name,
        Tagging={"TagSet": [{"Key": "Project", "Value": "agentcore-demo"}]},
    )


def main():
    config = load_config()
    region = config["AWS_REGION"]
    s3 = boto3.client("s3", region_name=region)

    buckets = {
        "source (B1)": config["BUCKET_SOURCE"],
        "destination (B2)": config["BUCKET_DEST"],
        "log (B3)": config["BUCKET_LOG"],
    }

    for label, name in buckets.items():
        print(f"Bucket - {label}: {name}")
        create_bucket(s3, name, region)


if __name__ == "__main__":
    main()
