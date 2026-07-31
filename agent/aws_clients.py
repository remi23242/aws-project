"""
One cached, thread-safe boto3 client per (service, region).

WHY THIS EXISTS (performance fix):
Building a boto3 client is not free - botocore parses that service's JSON
model, builds an endpoint resolver, and re-runs the credential chain. That
costs somewhere between 100ms and 400ms. The original code built a brand
new client on nearly every call: `logbook.append_entry()` built an S3
client each time it was called (4-6 times per file), and cloudwatch.py
built a Logs client on every log read. Across one 5-file run that alone was
tens of seconds of pure client construction with no AWS work happening.

boto3 clients are safe to SHARE across threads once built - it is only
their creation that isn't - so this module builds each one once behind a
lock and hands the same object to everyone afterwards.

The connection pool is also raised well above botocore's default of 10.
The agent now processes several files at once; a pool smaller than the
worker count would quietly serialise those workers again (and print
"Connection pool is full" warnings while doing it).
"""

import threading

import boto3
from botocore.config import Config

_LOCK = threading.Lock()
_CLIENTS = {}
_SESSION = None

_CONFIG = Config(
    max_pool_connections=64,
    retries={"max_attempts": 5, "mode": "standard"},
    # The agent is short-lived and chatty; fail fast rather than hanging a
    # worker for a full minute on one wedged socket.
    connect_timeout=10,
    read_timeout=60,
)


def get_client(service, region):
    """Return the shared boto3 client for this service+region, building it
    on first use."""
    key = (service, region)
    client = _CLIENTS.get(key)
    if client is not None:
        return client

    with _LOCK:
        # Re-check inside the lock: two threads can arrive here at once.
        if key not in _CLIENTS:
            global _SESSION
            if _SESSION is None:
                _SESSION = boto3.session.Session()
            _CLIENTS[key] = _SESSION.client(service, region_name=region, config=_CONFIG)
        return _CLIENTS[key]


def reset():
    """Drop every cached client. Only used by tests that need a clean slate."""
    with _LOCK:
        _CLIENTS.clear()
