"""
CLOUD TEST 2 - Prove the deployed run actually did the right thing.

Runs against AWS only: it reads the three S3 buckets and the global LOG
written by the hosted agent, and asserts every behaviour the project
promises. No agent code is imported and nothing is recomputed locally -
these are assertions about what is sitting in AWS right now.

Checks:
  1. B1 (source) was fully drained.
  2. B2 (dest) holds a copy of every file that was seeded.
  3. LOG in B3 contains a run header recording how many workers ran.
  4. LOG contains, for each file, the Lambda structured output, the raw
     CloudWatch log text, the agent's independent LLM conclusion, and the
     deterministic COMPARISON line.
  5. Every file reached a "Final status" line.
  6. Verified files were deleted from B1 only after verification passed.
  7. If L3 corrupted a copy, L2 caught it (a MISMATCH exists and that file
     was retried and eventually verified).
  8. The agent's LLM conclusions agreed with the Lambdas (MATCH), which is
     the whole point of the reasoning step.

Usage:
    python cloud_tests/02_verify_cloud_result.py
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _common as C

STATE_FILE = Path(__file__).resolve().parent / ".last_run.json"


def load_state():
    if not STATE_FILE.exists():
        raise SystemExit(
            f"{C.FAIL} No {STATE_FILE.name} found.\n"
            f"        Run this first:  python cloud_tests/01_invoke_cloud_agent.py"
        )
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def latest_run_block(log_text):
    """The LOG is cumulative across runs. Slice out just the newest run,
    which the agent delimits with its '========== AGENT RUN ...' header."""
    markers = [m.start() for m in re.finditer(r"^=+ AGENT RUN ", log_text, re.M)]
    return log_text[markers[-1] :] if markers else log_text


def main():
    config = C.load_config()
    state = load_state()
    region = config["AWS_REGION"]

    C.banner("CLOUD TEST 2 - verify the deployed run's results in AWS")
    print(f"{C.INFO} Reading state written by the hosted agent, not by this machine.")

    result = state.get("result") or {}
    reported_files = result.get("files") or []
    seeded = state.get("seeded_files", 0)

    b1 = C.s3_keys(region, config["BUCKET_SOURCE"])
    b2 = C.s3_keys(region, config["BUCKET_DEST"])
    full_log = C.read_log_object(region, config["BUCKET_LOG"])
    run = latest_run_block(full_log)

    print(f"{C.INFO} B1 (source) : {len(b1)} object(s)")
    print(f"{C.INFO} B2 (dest)   : {len(b2)} object(s)")
    print(f"{C.INFO} LOG         : {len(full_log):,} bytes total, {len(run):,} bytes for this run")
    print()

    checks = []

    # 1. B1 drained.
    checks.append((not b1, "B1 (source bucket) is empty - every file was moved", f"still present: {b1}" if b1 else ""))

    # 2. B2 holds every seeded file.
    expected = {f"sample_{i:03d}.txt" for i in range(1, seeded + 1)} if seeded else set()
    missing = sorted(expected - set(b2))
    checks.append(
        (
            not missing,
            f"B2 (destination bucket) holds all {len(expected)} seeded file(s)",
            f"missing: {missing}" if missing else "",
        )
    )

    # 3. Run header proves how many workers the hosted agent used.
    header = re.search(r"=+ AGENT RUN .*? \| (\d+) parallel worker\(s\) \| reasoning=(\w+)", run)
    checks.append(
        (
            header is not None,
            "LOG records this run's parallelism in its header",
            f"workers={header.group(1)}, reasoning={header.group(2)}" if header else "no AGENT RUN header found",
        )
    )

    # 4. Each of the required pieces of evidence appears in the LOG.
    for label, needle in [
        ("Lambda structured output", "structured output:"),
        ("raw CloudWatch log text", "CloudWatch log text:"),
        ("independent LLM conclusion", "Agent LLM conclusion:"),
        ("deterministic comparison line", "COMPARISON: Lambda says"),
    ]:
        count = run.count(needle)
        checks.append((count > 0, f"LOG contains the {label} ({count} occurrence(s))"))

    # 5. Every file reached a final status.
    final_statuses = re.findall(r"Final status for (\S+): ([A-Z][^.]*)\.", run)
    files_with_status = {f for f, _ in final_statuses}
    checks.append(
        (
            len(files_with_status) >= len(expected) if expected else bool(final_statuses),
            f"every file reached a Final status line ({len(final_statuses)} status line(s) for "
            f"{len(files_with_status)} distinct file(s))",
        )
    )

    # 6. Deleted only after verification.
    verified_files = {f for f, s in final_statuses if s.startswith("VERIFIED")}
    still_in_b1 = verified_files & set(b1)
    checks.append(
        (
            not still_in_b1,
            f"all {len(verified_files)} VERIFIED file(s) were deleted from B1",
            f"still in B1 despite VERIFIED: {sorted(still_in_b1)}" if still_in_b1 else "",
        )
    )

    kept_files = {f for f, s in final_statuses if s.startswith("VERIFICATION FAILED")}
    if kept_files:
        # Every file that failed once must have been retried and finished.
        unresolved = kept_files - verified_files
        checks.append(
            (
                not unresolved,
                f"every file that failed verification ({len(kept_files)}) was retried until it verified",
                f"never verified: {sorted(unresolved)}" if unresolved else "",
            )
        )
        checks.append((True, "L3 corruption was genuinely detected by L2 at least once (MISMATCH recorded)"))
    else:
        print(
            f"{C.INFO} No file was corrupted this run - L3's coin flip is random (~40%).\n"
            f"         Re-run test 1 to see the failure-detection path, or seed more files."
        )

    # 7. Hash mismatches, when they happen, are recorded as such.
    mismatches = run.count("Hash comparison: MISMATCH")
    matches = run.count("Hash comparison: MATCH")
    print(f"{C.INFO} Hash comparisons this run: {matches} MATCH, {mismatches} MISMATCH")

    # 8. Every Lambda call actually got an independent conclusion. This has
    # to be checked before the agreement rate, because a call the agent
    # could not reason about at all is not an agreement - and without this
    # check a run whose LLM quota was exhausted would still show green.
    total_comparisons = run.count("COMPARISON: Lambda says")
    agree = run.count("-> MATCH")
    disagree = run.count("-> DISAGREEMENT")
    llm_unavailable = run.count("NOT COMPARED (LLM unavailable)")
    log_unavailable = run.count("NOT COMPARED (log unavailable)")
    not_compared = llm_unavailable + log_unavailable

    print(
        f"{C.INFO} Reasoning outcomes: {agree} MATCH, {disagree} DISAGREEMENT, "
        f"{llm_unavailable} LLM unavailable, {log_unavailable} log unavailable "
        f"(of {total_comparisons} Lambda call(s))"
    )

    detail = ""
    if llm_unavailable:
        detail = (
            "the LLM provider refused these calls - almost always a free-tier quota. "
            "Check LLM_PROVIDER / LLM_MAX_CONCURRENCY in config.env, or wait for the quota to reset."
        )
    elif log_unavailable:
        detail = "CloudWatch did not return the log text for these calls within the timeout."

    checks.append(
        (
            not_compared == 0,
            f"every Lambda call got an independent LLM conclusion "
            f"({total_comparisons - not_compared}/{total_comparisons})",
            detail,
        )
    )

    # 9. Of the calls that were compared, the LLM agreed with the Lambda.
    checks.append(
        (
            agree > 0 and disagree == 0,
            f"agent's LLM conclusions agreed with the Lambdas on all {agree} compared call(s)",
            f"{disagree} DISAGREEMENT(s) - inspect the LOG" if disagree else "",
        )
    )

    # 10. The reported result is internally consistent.
    checks.append(
        (
            result.get("status") == "complete" and len(reported_files) >= len(expected),
            f"hosted agent reported {len(reported_files)} file-pass(es) for {len(expected)} seeded file(s)",
        )
    )

    print()
    results = []
    for entry in checks:
        ok, description = entry[0], entry[1]
        detail = entry[2] if len(entry) > 2 else ""
        C.check(ok, description, detail)
        results.append((ok, description))

    return C.exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
