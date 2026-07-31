"""
Single chokepoint for all LLM calls in the project (guide Step 8.3).
Every call to an LLM - Groq, Gemini, OpenAI, or Bedrock - goes through
ask_llm(). Which provider is used is controlled by LLM_PROVIDER in
config.env, so the rest of the agent's logic (agent.py) never needs to
know or care which one is active - it just calls ask_llm(prompt) and gets
text back.

--- Performance notes (why this file changed) ---------------------------
Three fixes, all of which matter now that the agent runs files in parallel:

  1. The provider client is built ONCE and reused. Building an OpenAI/Groq
     client (or a Bedrock boto3 client) per call added hundreds of
     milliseconds to every single reasoning step for no benefit.

  2. Output is capped and temperature pinned to 0. The agent only needs
     "SUCCESS/FAILURE + a short justification"; without a cap the model was
     free to ramble for hundreds of extra tokens, and generation time is
     roughly linear in tokens produced. This is usually a 2-4x cut in LLM
     latency on its own.

  3. Concurrency is bounded by a semaphore and retries use exponential
     backoff. Running 8 files at once means bursts of LLM calls; free tiers
     rate-limit per minute, and a burst that trips a 429 is slower than a
     burst that was paced correctly in the first place.
"""

import random
import re
import threading
import time

_CLIENT_LOCK = threading.Lock()
_CLIENTS = {}

_SEMAPHORE_LOCK = threading.Lock()
_SEMAPHORE = None
_SEMAPHORE_SIZE = None

# Enough room for one word plus a sentence or two of evidence - measured
# against real runs, where conclusions average ~120 tokens and the longest
# seen was ~150.
#
# This number matters more than it looks. Groq's free tier meters a
# tokens-per-minute budget and counts the max_tokens you RESERVE, not the
# tokens you actually use. Reserving 300 for 22 calls books 6,600 tokens of
# headroom the agent never touches, which was enough on its own to push a
# parallel run over the 12,000 TPM limit and trigger 429 backoffs. Keeping
# the reservation tight is a direct throughput win.
MAX_OUTPUT_TOKENS = 200

RATE_LIMIT_MARKERS = ("429", "RESOURCE_EXHAUSTED", "Throttling", "TooManyRequests", "rate_limit")


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


def _get_semaphore(config):
    """Cap how many LLM calls are in flight at once, so parallel file
    processing doesn't turn into a rate-limit storm. LLM_MAX_CONCURRENCY in
    config.env tunes it."""
    global _SEMAPHORE, _SEMAPHORE_SIZE
    size = int(config.get("LLM_MAX_CONCURRENCY", 8) or 8)
    with _SEMAPHORE_LOCK:
        if _SEMAPHORE is None or _SEMAPHORE_SIZE != size:
            _SEMAPHORE = threading.BoundedSemaphore(size)
            _SEMAPHORE_SIZE = size
        return _SEMAPHORE


def _retry_after_seconds(exc):
    """Providers that rate-limit usually say exactly how long to wait, in a
    Retry-After header. Using that number instead of a guess removes most of
    the dead time a fixed backoff wastes."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    for name in ("retry-after", "Retry-After", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        raw = headers.get(name)
        if not raw:
            continue
        try:
            # Plain seconds ("3"), or Groq's "1.5s" / "2m59.56s" style.
            match = re.fullmatch(r"(?:(\d+)m)?([\d.]+)s?", str(raw).strip())
            if not match:
                continue
            minutes = float(match.group(1) or 0)
            seconds = float(match.group(2))
            total = minutes * 60 + seconds
            # A multi-minute reset means the daily budget, not this minute's -
            # waiting that out mid-demo is worse than failing loudly.
            if 0 < total <= 60:
                return total
        except (TypeError, ValueError):
            continue
    return None


def ask_llm(prompt, config=None, max_retries=5, retry_wait_seconds=3):
    """Send a prompt to the configured LLM provider, return its text reply.

    Retries on rate-limit errors, waiting exactly as long as the provider
    asks when it tells us, and backing off exponentially when it doesn't.
    Free-tier LLM quotas are easy to hit during a parallel agent run, and
    this project would rather pause briefly than crash mid-demo."""
    config = config or load_config()
    provider = config.get("LLM_PROVIDER", "bedrock").lower()

    handlers = {
        "gemini": _ask_gemini,
        "openai": _ask_openai,
        "groq": _ask_groq,
        "bedrock": _ask_bedrock,
    }
    handler = handlers.get(provider)
    if handler is None:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider!r} (expected bedrock/openai/gemini/groq)")

    semaphore = _get_semaphore(config)

    for attempt in range(max_retries):
        try:
            with semaphore:
                return handler(prompt, config)
        except Exception as exc:
            message = str(exc)
            is_rate_limit = any(marker in message for marker in RATE_LIMIT_MARKERS)
            if is_rate_limit and attempt < max_retries - 1:
                # Prefer the provider's own Retry-After; otherwise back off
                # exponentially. Jitter either way, so parallel workers that
                # were throttled together don't all retry at the same moment.
                wait = _retry_after_seconds(exc) or retry_wait_seconds * (2**attempt)
                wait += random.uniform(0, 1.0)
                print(f"  LLM rate-limited, waiting {wait:.1f}s before retry {attempt + 2}/{max_retries}...")
                time.sleep(wait)
                continue
            raise


def _cached(key, build):
    """Build a provider client once, then hand the same one to every call."""
    client = _CLIENTS.get(key)
    if client is not None:
        return client
    with _CLIENT_LOCK:
        if key not in _CLIENTS:
            _CLIENTS[key] = build()
        return _CLIENTS[key]


def _ask_gemini(prompt, config):
    from google import genai

    client = _cached(("gemini", config["GEMINI_API_KEY"]), lambda: genai.Client(api_key=config["GEMINI_API_KEY"]))
    response = client.models.generate_content(
        model=config.get("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=prompt,
    )
    return response.text


def _ask_openai(prompt, config):
    from openai import OpenAI

    client = _cached(("openai", config["OPENAI_API_KEY"]), lambda: OpenAI(api_key=config["OPENAI_API_KEY"]))
    response = client.chat.completions.create(
        model=config.get("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[{"role": "user", "content": prompt}],
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=0,
    )
    return response.choices[0].message.content


def _ask_groq(prompt, config):
    """Groq's API is OpenAI-compatible, so this reuses the openai package
    already in requirements.txt - just pointed at Groq's base URL with a
    Groq API key and model. Free tier: 14,400 requests/day per model, far
    more generous than what we hit on Gemini."""
    from openai import OpenAI

    client = _cached(
        ("groq", config["GROQ_API_KEY"]),
        lambda: OpenAI(api_key=config["GROQ_API_KEY"], base_url="https://api.groq.com/openai/v1"),
    )
    response = client.chat.completions.create(
        model=config.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        messages=[{"role": "user", "content": prompt}],
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=0,
    )
    return response.choices[0].message.content


def _ask_bedrock(prompt, config):
    import aws_clients

    client = aws_clients.get_client("bedrock-runtime", config["AWS_REGION"])
    response = client.converse(
        modelId=config["BEDROCK_MODEL_ID"],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": MAX_OUTPUT_TOKENS, "temperature": 0},
    )
    return response["output"]["message"]["content"][0]["text"]


if __name__ == "__main__":
    import sys

    prompt = sys.argv[1] if len(sys.argv) > 1 else "Reply with exactly one word: pong"
    print(ask_llm(prompt))
