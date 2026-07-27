"""
Step 8: Auto-generate sample .txt files and upload them into the source
bucket (B1).

Idempotent-ish: uses a fixed naming scheme (sample_001.txt, sample_002.txt,
...) and overwrites if rerun, so it's safe to run more than once.
"""

import boto3


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


def make_sample_text(index):
    """Small, readable file content - easy to eyeball in the console."""
    lines = [f"Sample file #{index}"]
    lines += [f"Line {n} of sample_{index:03d}.txt" for n in range(1, 21)]
    return "\n".join(lines) + "\n"


def main():
    config = load_config()
    region = config["AWS_REGION"]
    bucket = config["BUCKET_SOURCE"]
    s3 = boto3.client("s3", region_name=region)

    # Keep this small on purpose - the cost-first rule in CLAUDE.md says use
    # a handful of tiny files, not a large batch.
    num_files = 5

    for i in range(1, num_files + 1):
        key = f"sample_{i:03d}.txt"
        body = make_sample_text(i)
        s3.put_object(Bucket=bucket, Key=key, Body=body.encode("utf-8"))
        print(f"  Uploaded {key} ({len(body)} bytes) to {bucket}")

    print(f"Done: {num_files} sample files in {bucket}")


if __name__ == "__main__":
    main()