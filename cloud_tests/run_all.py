"""
Run every cloud test in order, against the DEPLOYED agent only.

This is the one command to run on camera after `agentcore deploy`:

    python cloud_tests/run_all.py

It performs:
    1. 01_invoke_cloud_agent.py   - seed B1, invoke the hosted agent, time it
    2. 02_verify_cloud_result.py  - assert the resulting AWS state is correct
    3. 03_show_runtime_logs.py    - show the container's own CloudWatch output
                                    and prove files overlapped in time

Nothing here imports the agent. Every step talks to AWS.

Usage:
    python cloud_tests/run_all.py
    python cloud_tests/run_all.py --files 20
"""

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def run_step(script, extra_args):
    print("\n" + "#" * 74)
    print(f"# python cloud_tests/{script} {' '.join(extra_args)}".rstrip())
    print("#" * 74, flush=True)
    completed = subprocess.run(
        [sys.executable, str(HERE / script), *extra_args],
        cwd=str(ROOT),
    )
    return completed.returncode


def main():
    parser = argparse.ArgumentParser(description="Run the whole cloud test suite against the deployed agent.")
    parser.add_argument("--files", type=int, default=5, help="how many sample files to seed")
    parser.add_argument("--workers", type=int, default=None, help="override worker count for the main run")
    args = parser.parse_args()

    invoke_args = ["--files", str(args.files)]
    if args.workers is not None:
        invoke_args += ["--workers", str(args.workers)]

    steps = [
        ("01_invoke_cloud_agent.py", invoke_args),
        ("02_verify_cloud_result.py", []),
        ("03_show_runtime_logs.py", []),
    ]

    failures = []
    for script, extra in steps:
        code = run_step(script, extra)
        if code != 0:
            failures.append(script)
            # A failed invoke makes every later step meaningless.
            if script.startswith("01_"):
                break

    print("\n" + "=" * 74)
    if failures:
        print(f"[ FAIL ] {len(failures)} step(s) failed: {', '.join(failures)}")
        return 1
    print(f"[ PASS ] All {len(steps)} cloud test steps passed against the deployed agent.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
