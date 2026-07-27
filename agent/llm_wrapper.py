"""
Single chokepoint for all LLM calls in the project (CLAUDE.md Section 5).
Every call to an LLM - Gemini, OpenAI, Groq, or Bedrock - goes through
ask_llm(). Which provider is used is controlled by LLM_PROVIDER in
config.env, so the rest of the agent's logic (agent.py) never needs to
know or care which one is active - it just calls ask_llm(prompt) and gets
text back.
"""


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


RATE_LIMIT_MARKERS = ("429", "RESOURCE_EXHAUSTED", "Throttling", "TooManyRequests")


def ask_llm(prompt, config=None, max_retries=3, retry_wait_seconds=15):
    """Send a prompt to the configured LLM provider, return its text reply.

    Retries on rate-limit errors - free-tier LLM quotas are easy to hit
    during a long agent run, and this project would rather wait a few
    seconds than crash mid-demo."""
    import time

    config = config or load_config()
    provider = config.get("LLM_PROVIDER", "bedrock").lower()

    for attempt in range(max_retries):
        try:
            if provider == "gemini":
                return _ask_gemini(prompt, config)
            elif provider == "openai":
                return _ask_openai(prompt, config)
            elif provider == "groq":
                return _ask_groq(prompt, config)
            elif provider == "bedrock":
                return _ask_bedrock(prompt, config)
            else:
                raise ValueError(f"Unknown LLM_PROVIDER: {provider!r} (expected bedrock/openai/gemini/groq)")
        except Exception as exc:
            is_rate_limit = any(marker in str(exc) for marker in RATE_LIMIT_MARKERS)
            if is_rate_limit and attempt < max_retries - 1:
                print(f"  LLM rate-limited, waiting {retry_wait_seconds}s before retry {attempt + 2}/{max_retries}...")
                time.sleep(retry_wait_seconds)
                continue
            raise


def _ask_gemini(prompt, config):
    from google import genai

    client = genai.Client(api_key=config["GEMINI_API_KEY"])
    response = client.models.generate_content(
        model=config.get("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=prompt,
    )
    return response.text


def _ask_openai(prompt, config):
    from openai import OpenAI

    client = OpenAI(api_key=config["OPENAI_API_KEY"])
    response = client.chat.completions.create(
        model=config.get("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def _ask_groq(prompt, config):
    """Groq's API is OpenAI-compatible, so this reuses the openai package
    already in requirements.txt - just pointed at Groq's base URL with a
    Groq API key and model. Free tier: 14,400 requests/day per model, far
    more generous than what we hit on Gemini today."""
    from openai import OpenAI

    client = OpenAI(api_key=config["GROQ_API_KEY"], base_url="https://api.groq.com/openai/v1")
    response = client.chat.completions.create(
        model=config.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def _ask_bedrock(prompt, config):
    import boto3

    client = boto3.client("bedrock-runtime", region_name=config["AWS_REGION"])
    response = client.converse(
        modelId=config["BEDROCK_MODEL_ID"],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
    )
    return response["output"]["message"]["content"][0]["text"]


if __name__ == "__main__":
    import sys

    prompt = sys.argv[1] if len(sys.argv) > 1 else "Reply with exactly one word: pong"
    print(ask_llm(prompt))