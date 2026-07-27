"""
Tests for the agent's CloudWatch-reading + LLM-reasoning flow (CLAUDE.md
Section 2.1). Invokes L1 directly, then exercises the same cloudwatch.py /
llm_wrapper.py functions agent.py's call_lambda_and_reason() uses.

Covers CLAUDE.md Section 3.4 test cases 7, 8.
"""

import cloudwatch
import llm_wrapper

from helpers import invoke_lambda, make_test_file_content

L1 = "agentcore-demo-l1-copy"

LOG_PROMPT = (
    "Here is a CloudWatch log from a Lambda operation. Based only on this "
    "log text, did the operation succeed or fail? Start your reply with "
    "exactly one word, SUCCESS or FAILURE, then explain your evidence.\n\n"
    "--- CloudWatch log ---\n{log_text}"
)


def test_agent_reads_cloudwatch_and_produces_conclusion(config, s3, lambda_client):
    """Test case 7: agent reads CloudWatch logs and produces a text conclusion."""
    file_name = "test_reasoning_success.txt"
    content = make_test_file_content(tag="reasoning")
    s3.put_object(Bucket=config["BUCKET_SOURCE"], Key=file_name, Body=content.encode())

    result = invoke_lambda(lambda_client, L1, {
        "source_bucket": config["BUCKET_SOURCE"],
        "dest_bucket": config["BUCKET_DEST"],
        "file_name": file_name,
    })

    log_text = cloudwatch.get_log_text_for_request(L1, result["request_id"], config["AWS_REGION"])
    assert log_text
    assert result["request_id"] in log_text

    conclusion = llm_wrapper.ask_llm(LOG_PROMPT.format(log_text=log_text), config)
    assert conclusion.strip()


def test_agent_compares_conclusion_with_structured_output(config, s3, lambda_client):
    """Test case 8: agent compares its log-based conclusion with the
    Lambda's structured output - exercises the same logic as
    agent.call_lambda_and_reason(), using a known-successful invocation so
    the comparison should be MATCH."""
    file_name = "test_reasoning_compare.txt"
    content = make_test_file_content(tag="compare")
    s3.put_object(Bucket=config["BUCKET_SOURCE"], Key=file_name, Body=content.encode())

    result = invoke_lambda(lambda_client, L1, {
        "source_bucket": config["BUCKET_SOURCE"],
        "dest_bucket": config["BUCKET_DEST"],
        "file_name": file_name,
    })
    log_text = cloudwatch.get_log_text_for_request(L1, result["request_id"], config["AWS_REGION"])

    conclusion = llm_wrapper.ask_llm(LOG_PROMPT.format(log_text=log_text), config)
    llm_verdict = conclusion.strip().split()[0].upper().rstrip(".:,")

    assert result["success"] is True
    assert llm_verdict == "SUCCESS"