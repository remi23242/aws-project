"""
Write the single global LOG file in B3 (guide Part 9 / Step 9.2).

S3 has no native "append" operation. The original approach was to read the
whole LOG object, add one line, and write the whole thing back - once per
entry. That is two S3 round-trips per entry, six or more entries per file,
and it is also a lost-update race the moment two files are processed at the
same time (two threads read the same LOG, both write, one entry vanishes).

So this module now offers two things:

  * `append_entry()` - the original read-modify-write helper. Kept because
    it is simple, it is what the guide's Step 8.2 smoke test uses, and the
    test suite asserts on it. Correct for single-threaded use only.

  * `LogBuffer` - what the agent actually uses now. It collects every entry
    in memory, keyed so that each file's block stays contiguous and in
    order no matter which worker thread produced it or in what order, then
    writes the whole LOG in ONE S3 PUT at the end of the run. That turns
    ~40 S3 round-trips per run into 2, and removes the race entirely.

Deciding WHAT to write (the learner-readable entry format) still lives in
agent.py; this module only knows how to store and ship it.
"""

import threading

from botocore.exceptions import ClientError

import aws_clients

LOG_KEY = "LOG"


def append_entry(bucket, region, text):
    """Append a text block to the LOG object in the given bucket.

    Read-modify-write, so it is safe only when one thread is writing. The
    agent's parallel loop uses LogBuffer instead; this stays for the guide's
    standalone smoke test and for the test suite."""
    s3 = aws_clients.get_client("s3", region)

    try:
        existing = s3.get_object(Bucket=bucket, Key=LOG_KEY)["Body"].read().decode("utf-8")
    except ClientError:
        existing = ""

    updated = existing + text.rstrip("\n") + "\n"
    s3.put_object(Bucket=bucket, Key=LOG_KEY, Body=updated.encode("utf-8"))


class FileLog:
    """Every LOG entry belonging to one file's trip through the pipeline.

    Entries are added in the order the agent produces them. An entry whose
    text isn't ready yet (because its CloudWatch read + LLM reasoning was
    handed off to a background thread) is added as a Future and resolved
    when the buffer is flushed - so the finished LOG still reads top to
    bottom in the right order, even though the work happened out of order.
    """

    def __init__(self, order, file_name):
        self.order = order
        self.file_name = file_name
        self._slots = []

    def add(self, text):
        """Add a block of text that is already known."""
        self._slots.append(text)

    def add_deferred(self, future):
        """Reserve this position in the file's block for text that a
        background thread is still producing."""
        self._slots.append(future)

    def render(self):
        """Resolve every slot (waiting on any still-running background
        reasoning) and return this file's complete LOG block."""
        parts = []
        for slot in self._slots:
            if hasattr(slot, "result"):
                try:
                    text = slot.result()
                except Exception as exc:  # never lose the rest of the LOG to one bad entry
                    text = f"[log entry failed to render: {exc!r}]"
            else:
                text = slot
            if text:
                parts.append(text.rstrip("\n"))
        return "\n".join(parts)


class LogBuffer:
    """Collects the whole run's LOG in memory, writes it in one S3 PUT."""

    def __init__(self, bucket, region):
        self.bucket = bucket
        self.region = region
        self._lock = threading.Lock()
        self._notes = []
        self._footers = []
        self._file_logs = []

    def note(self, text):
        """A run-level line printed ABOVE every file block (the run header)."""
        with self._lock:
            self._notes.append(text)

    def footer(self, text):
        """A run-level line printed BELOW every file block (the run summary,
        or the safety-cap message)."""
        with self._lock:
            self._footers.append(text)

    def file_log(self, order, file_name):
        """Create and register a block for one file. `order` is the
        iteration number, which is what the final LOG is sorted by."""
        entry = FileLog(order, file_name)
        with self._lock:
            self._file_logs.append(entry)
        return entry

    def render(self):
        """Assemble the run's text: run-level notes first, then each file's
        block in iteration order."""
        with self._lock:
            notes = list(self._notes)
            footers = list(self._footers)
            file_logs = sorted(self._file_logs, key=lambda f: f.order)

        parts = [n.rstrip("\n") for n in notes if n]
        for file_log in file_logs:
            block = file_log.render()
            if block:
                parts.append(block)
        parts.extend(f.rstrip("\n") for f in footers if f)
        return "\n".join(parts)

    def flush(self):
        """Append this run's text to the LOG object in B3 - one GET, one
        PUT for the entire run. Returns the text that was appended."""
        text = self.render()
        if not text:
            return ""

        s3 = aws_clients.get_client("s3", self.region)
        try:
            existing = s3.get_object(Bucket=self.bucket, Key=LOG_KEY)["Body"].read().decode("utf-8")
        except ClientError:
            existing = ""

        updated = existing + text.rstrip("\n") + "\n"
        s3.put_object(Bucket=self.bucket, Key=LOG_KEY, Body=updated.encode("utf-8"))
        return text


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
