# Build Steps Log

Complete, in-depth log of every step in this build — purpose, exact
commands, exact console paths, exact test payloads, expected results, and
common errors. This is the source material for the client DOCX and video.

---

## Step 1 — Scaffold the repo + initialize git

**Purpose:** Create the project folder/file skeleton and put it under
version control from the start.

**Commands:**
```powershell
git init
```
Then create the folder structure (see CLAUDE.md Section 4 for the full
layout): `setup/`, `lambdas/l1_copy/`, `lambdas/l2_verify/`,
`lambdas/l3_corrupt/`, `agent/`, `tests/`, `cleanup/`, `docs/`, plus
`README.md`, `requirements.txt`, `config.example.env`, `.gitignore`.

**`.gitignore` must exclude:** `config.env`, `*.env` (except
`config.example.env`), `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.venv/`.

**Expected result:** `git status` shows a new repo with the full skeleton
untracked.

**Common errors:** None expected — local filesystem only.

---

## Step 2 — Install the AWS CLI

**Purpose:** The AWS CLI is what lets local scripts (boto3, the AgentCore
toolkit) authenticate to and act on your AWS account.

**Download link:** `https://awscli.amazonaws.com/AWSCLIV2.msi` (official
AWS direct download, Windows 64-bit installer).

**Steps:**
1. Download and run `AWSCLIV2.msi` → click through with defaults (Next →
   Next → Install → Finish).
2. **Close and fully reopen your terminal** — the installer updates your
   Windows PATH, which an already-open terminal won't see.
3. Verify:
   ```powershell
   aws --version
   ```

**Expected result:** Prints something like `aws-cli/2.36.8 Python/3.14.6
Windows/11 exe/AMD64`.

**Common errors & fixes:**
- *"aws: command not found"* → terminal wasn't reopened after install;
  close ALL terminal windows and reopen.
- *Windows SmartScreen blocks the installer* → click "More info" → "Run
  anyway" (it's Amazon-signed).

---

## Step 3 — Install Python 3.12 + create the project virtual environment

**Purpose:** The AgentCore toolkit and Strands SDK are newer libraries that
may not yet support a very new system Python — this project is pinned to
an isolated Python 3.12 environment, separate from whatever Python you
already have installed.

**Download link:** `https://www.python.org/downloads/release/python-3120/`
(or search "python 3.12 download" for the latest 3.12.x patch release —
any 3.12.x works).

**Steps:**
1. Download the **Windows installer (64-bit)** from the Files section.
2. Run it → **check "Add python.exe to PATH"** on the first screen (easy
   to miss) → **Install Now** → **Close**.
3. Close and reopen your terminal.
4. Verify both versions are available:
   ```powershell
   py -0
   ```
5. Create the project's virtual environment (run from the project root):
   ```powershell
   py -3.12 -m venv .venv
   .venv\Scripts\python.exe --version
   ```

**Expected result:** `py -0` lists both your existing Python and `3.12`;
`.venv\Scripts\python.exe --version` prints `Python 3.12.x`, and a new
`.venv` folder appears in the project directory.

**Common errors & fixes:**
- *Forgot to check "Add to PATH"* → rerun the installer, choose "Modify,"
  check the box (or just reinstall).
- *`.venv` shows the wrong Python version* → delete the `.venv` folder
  (`rmdir /s /q .venv`) and rerun `py -3.12 -m venv .venv`.

---

## Step 4 — Install project dependencies

**Purpose:** Installs every Python package this project needs into the
isolated `.venv`.

**`requirements.txt` contents:**
```
boto3
strands-agents
bedrock-agentcore-starter-toolkit
mcp-proxy-for-aws
google-genai
openai
pytest
```

**Command:**
```powershell
.venv\Scripts\pip install -r requirements.txt
```

**Expected result:** Ends with `Successfully installed ...` and no red
error text.

**Common errors & fixes:**
- *A package fails to build on Windows* → usually needs "Microsoft C++
  Build Tools" — only chase this if it actually happens.
- *SSL/certificate errors* → often VPN/proxy interference; retry on a
  normal network.

---

## Step 5 — Create an IAM user for CLI access + configure the AWS CLI

**Purpose:** Create a dedicated, non-root AWS identity for all CLI/script
work, with its own revocable access key.

**Console path (sign in as root only for this one setup step):**
1. Search bar → `IAM` → **IAM** → **Users** → **Create user**.
2. User name: `agentcore-cli-user` → **Next**.
3. Permissions options → **Attach policies directly** → search and check
   **AdministratorAccess** (broadest, simplest for this learning project —
   note in the DOCX that production would scope this down) → **Next** →
   **Create user**.
4. Click into `agentcore-cli-user` → **Security credentials** tab → scroll
   to **Access keys** → **Create access key**.
5. Use case: **Command Line Interface (CLI)** → check the confirmation box
   → **Next** → **Create access key**.
6. **Download the .csv file now** — the Secret Access Key is shown only
   once. Store it somewhere safe, never inside the git repo.

**Command (paste from the downloaded CSV when prompted):**
```powershell
aws configure
```
```
AWS Access Key ID: <paste from the CSV>
AWS Secret Access Key: <paste from the CSV>
Default region name: us-east-1
Default output format: json
```

**Verify:**
```powershell
aws sts get-caller-identity
```

**Expected result:**
```json
{
    "UserId": "AIDA...",
    "Account": "<your account id>",
    "Arn": "arn:aws:iam::<your account id>:user/agentcore-cli-user"
}
```

**Common errors & fixes:**
- *`InvalidClientTokenId`* → typo pasting the access key; rerun
  `aws configure`.
- *Lost the CSV before copying the secret key* → go back to Security
  credentials → deactivate that key → create a new one.

---

## Step 6 — Create your local `config.env`

**Purpose:** One local, git-ignored file holding every setting/secret this
project needs — region, bucket names, model IDs, API keys. Never committed.

**Command:**
```powershell
copy config.example.env config.env
```

**Then edit `config.env` to look like this** (replace `BUCKET_SUFFIX` with
your own AWS account ID, from Step 5's `aws sts get-caller-identity`):
```
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0

LLM_PROVIDER=gemini
GEMINI_API_KEY=<get this in Step 17 below>
GEMINI_MODEL=gemini-flash-latest
OPENAI_API_KEY=changeme

BUCKET_SUFFIX=<your AWS account id>
BUCKET_SOURCE=agentcore-demo-source-<your AWS account id>
BUCKET_DEST=agentcore-demo-dest-<your AWS account id>
BUCKET_LOG=agentcore-demo-log-<your AWS account id>
```

**Common errors & fixes:**
- *`FileNotFoundError: config.env` in later steps* → this copy step was
  skipped, or scripts are being run from the wrong folder.

---

## Step 7 — Create the S3 buckets (B1 source, B2 destination, B3 log)

**Purpose:** The three buckets are the whole storage backbone: B1 holds
originals, B2 holds copies, B3 holds the audit LOG.

**Script:** `setup/01_create_buckets.py` — idempotent, tags every bucket
`Project=agentcore-demo`.

**Command:**
```powershell
.venv\Scripts\python.exe setup\01_create_buckets.py
```

**Console check:** S3 → confirm all three buckets exist:
`agentcore-demo-source-<suffix>`, `agentcore-demo-dest-<suffix>`,
`agentcore-demo-log-<suffix>`.

**Expected result:**
```
Bucket - source (B1): agentcore-demo-source-...
  Created agentcore-demo-source-...
Bucket - destination (B2): agentcore-demo-dest-...
  Created agentcore-demo-dest-...
Bucket - log (B3): agentcore-demo-log-...
  Created agentcore-demo-log-...
```

**Common errors & fixes:**
- *`BucketAlreadyExists`* → S3 names are globally unique across ALL AWS
  accounts; change `BUCKET_SUFFIX` in `config.env` to something more
  unique and rerun.

---

## Step 8 — Seed sample `.txt` files into B1

**Purpose:** Give the agent something to process — 5 small, readable
20-line `.txt` files.

**Script:** `setup/02_seed_files.py`.

**Command:**
```powershell
.venv\Scripts\python.exe setup\02_seed_files.py
```

**Console check:** S3 → `agentcore-demo-source-<suffix>` → confirm
`sample_001.txt` through `sample_005.txt` exist.

**Expected result:** `Done: 5 sample files in agentcore-demo-source-...`

---

## Step 9 — Create the Lambda execution IAM role

**Purpose:** One shared IAM role for L1/L2/L3 — lets them write logs to
CloudWatch and read/write only the specific S3 buckets they need (least
privilege): `GetObject` on B1, `GetObject`+`PutObject` on B2.

**Script:** `setup/03_iam_roles.py`.

**Command:**
```powershell
.venv\Scripts\python.exe setup\03_iam_roles.py
```

**Console check:** IAM → Roles → `agentcore-demo-lambda-exec-role` →
confirm it has managed policy `AWSLambdaBasicExecutionRole` and inline
policy `agentcore-demo-lambda-s3-access`.

---

## Step 10 — Deploy and test L1 (Copy File)

**Purpose:** First Lambda — reads a file from B1, computes its SHA-256,
copies it to B2, returns a structured result.

**Handler:** `lambdas/l1_copy/handler.py`. **Deploy script:**
`setup/04_deploy_lambdas.py`.

**Command:**
```powershell
.venv\Scripts\python.exe setup\04_deploy_lambdas.py
```

**Console test:** Lambda → `agentcore-demo-l1-copy` → **Test** tab → new
event, paste this JSON (adjust the bucket names to your own suffix):
```json
{
  "source_bucket": "agentcore-demo-source-<suffix>",
  "dest_bucket": "agentcore-demo-dest-<suffix>",
  "file_name": "sample_001.txt"
}
```
→ **Save** → **Test**.

**Expected result (execution result panel):**
```json
{"file": "sample_001.txt", "success": true, "original_hash": "f67695e4...", "error": null, "request_id": "..."}
```
Then check: S3 → B2 bucket now contains `sample_001.txt`. Lambda →
**Monitor** tab → **View CloudWatch logs** → latest log stream shows
`L1 start: copying...`, `L1: computed original SHA-256...`,
`L1: copied...`, `L1 result: {...}`.

**Common errors & fixes:**
- *`InvalidParameterValueException: role ... is not valid`* → IAM role
  hasn't finished propagating; wait ~30s and rerun the deploy script.
- *`NoSuchKey`* → `file_name` doesn't match an object actually in B1.
- *`AccessDenied`* → the exec role's inline policy doesn't match your
  actual bucket names in `config.env`.

---

## Step 11 — Deploy and test L2 (Verify File)

**Purpose:** Independently re-hashes the B2 copy and compares it to the
hash L1 reported — this is what later catches corruption.

**Handler:** `lambdas/l2_verify/handler.py`.

**Console test:** Lambda → `agentcore-demo-l2-verify` → **Test** tab, using
the real `original_hash` you got from Step 10:
```json
{
  "dest_bucket": "agentcore-demo-dest-<suffix>",
  "file_name": "sample_001.txt",
  "original_hash": "f67695e4d157a7bf6d4d75e9c71e2af745c79882cdd08cf34155475d43e4ceac"
}
```

**Expected result:**
```json
{"file": "sample_001.txt", "original_hash": "f67695e4...", "copied_hash": "f67695e4...", "match": true, "error": null, "request_id": "..."}
```

---

## Step 12 — Deploy and test L3 (Create a Copy Error) + prove detection

**Purpose:** Deliberately corrupts a B2 file (strips the last 10 lines) to
prove L2 can actually detect real failures — this pairing is required
evidence per CLAUDE.md Section 3.5.

**Handler:** `lambdas/l3_corrupt/handler.py`.

**Part A — corrupt it.** Lambda → `agentcore-demo-l3-corrupt` → **Test**:
```json
{
  "dest_bucket": "agentcore-demo-dest-<suffix>",
  "file_name": "sample_001.txt"
}
```
Expected: `{"file": "sample_001.txt", "success": true, "error": null}`, and
CloudWatch shows "removed last 10 lines... 21 -> 11 lines remaining"
(sample files are 21 lines: 1 title + 20 numbered lines).

**Part B — prove L2 catches it.** Rerun L2's Step 11 test event UNCHANGED
(same `original_hash`) — now expect `"match": false"` with a different
`copied_hash`.

**Common errors & fixes:**
- *L2 still shows `match: true`* → L1 was rerun in between, which
  re-copies the clean file over the corrupted one — don't rerun L1 for
  this test.

---

## Step 13 — Create the Gateway execution role + the AgentCore Gateway

**Purpose:** Stand up the AgentCore Gateway with **IAM (SigV4) inbound
auth** (no Cognito, per CLAUDE.md Section 2's locked decision) — the
"front door" that will expose L1/L2/L3 as MCP tools.

**Script additions:** `create_gateway_exec_role()` in
`setup/03_iam_roles.py` (trust policy for `bedrock-agentcore.amazonaws.com`,
permission to invoke L1/L2/L3). New script: `setup/05_create_gateway.py`.

**Commands:**
```powershell
.venv\Scripts\python.exe setup\03_iam_roles.py
.venv\Scripts\python.exe setup\05_create_gateway.py
```

**Console check:** search `AgentCore` → **Amazon Bedrock AgentCore** →
**Gateways** → `agentcore-demo-gateway` → confirm inbound auth type
`AWS_IAM`, status `READY`.

**Expected result:**
```
Created gateway agentcore-demo-gateway
Gateway ID: agentcore-demo-gateway-...
Gateway URL: https://agentcore-demo-gateway-....gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp
Gateway status: CREATING
```
(Status flips to `READY` within a minute or two.)

**Common errors & fixes:** this is a newer AWS service — if a field name
errors out, capture the exact message; the API can shift.

---

## Step 14 — Register L1/L2/L3 as MCP tool targets on the Gateway

**Purpose:** Turns each Lambda into a named MCP tool (`copy_file`,
`verify_file`, `corrupt_file`) the agent can call through the Gateway.

**Script:** `setup/05b_add_gateway_targets.py`.

**Command:**
```powershell
.venv\Scripts\python.exe setup\05b_add_gateway_targets.py
```

**Console check:** Gateway → **Targets** tab → confirm `CopyFileTarget`,
`VerifyFileTarget`, `CorruptFileTarget`, all `READY`.

**Common errors & fixes:**
- *Target status `FAILED`* → click for the reason; usually the gateway
  role's `lambda:InvokeFunction` policy doesn't match the actual ARN.

---

## Step 15 — Smoke-test the Gateway end-to-end

**Purpose:** Prove the full chain — agent process to Gateway to Lambda,
authenticated with plain IAM SigV4 (no Cognito) — actually works.

**Module:** `agent/gateway_client.py` (real, reusable — not throwaway).

**Command:**
```powershell
.venv\Scripts\python.exe agent\gateway_client.py
```

**Gotcha:** the Gateway prefixes every tool name with its target name (e.g.
`CopyFileTarget___copy_file`) to avoid name collisions across targets —
never hardcode a bare tool name; read the real name back from
`list_tools_sync()` and match on suffix.

**Expected result:** Lists 3 tools, then successfully calls `copy_file` on
a sample file, printing a JSON success result.

---

## Step 16 — Build and test the CloudWatch log reader

**Purpose:** Fetch the raw CloudWatch log text for a Lambda's run, so the
LLM can independently read it (CLAUDE.md Section 3.3 / 2.1).

**Module:** `agent/cloudwatch.py`.

**Command:**
```powershell
.venv\Scripts\python.exe agent\cloudwatch.py agentcore-demo-l1-copy
```

**Expected result:** Prints the START/END/REPORT block for the most
recent invocation of that function.

---

## Step 17 — Build and test the LOG writer

**Purpose:** Append-only writer for the single global `LOG` object in B3.
S3 has no native append, so this reads the whole object, adds the new
text, and writes it back (fine at this project's scale).

**Module:** `agent/logbook.py`.

**Command:**
```powershell
.venv\Scripts\python.exe agent\logbook.py
```

**Console check:** S3 → `agentcore-demo-log-<suffix>` → confirm object
`LOG` exists and contains the smoke-test line.

---

## Step 18 — Set up the LLM provider (Gemini for dev, Bedrock for client)

**Purpose:** CLAUDE.md Section 2 requires a provider-switchable LLM wrapper
so a Bedrock quota/access hiccup never blocks development.

**Bedrock model access (the client-facing default):**
1. AWS changed this recently: the old "Model access" console page is
   retired — models now auto-enable on first invoke.
2. **First-time Anthropic models still require a one-time "use case
   details" form:** Bedrock → **Model catalog** → **Claude Haiku 4.5** →
   fill out the short use-case form (company/individual name, intended
   use) → wait ~15 minutes.
3. **New-account quota gotcha:** some brand-new AWS accounts get an
   **applied quota of 0** for Bedrock's on-demand tokens-per-minute/day on
   a given model, even though AWS's own published default is much higher.
   Check: Service Quotas console → search `Bedrock` → find e.g.
   "Cross-region model inference tokens per minute for Anthropic Claude
   Haiku 4.5" → if **Applied account-level quota value** is `0`, click it
   → **Request increase at account level** → enter the AWS default value
   shown → Submit. If it doesn't clear within ~30-60 minutes, open a free
   AWS Support case: **Support Center** → **Create case** → **Service
   limit increase** (available even on the Basic/free support plan).

**Gemini — tried first as the development default, hit real quota walls:**
1. Get a free API key: `https://aistudio.google.com/apikey` → **Create API
   key** → copy it.
2. `GEMINI_MODEL=gemini-flash-latest` resolved to a brand-new model
   (Gemini 3.6 Flash) whose free tier is only **20 requests/day** — nowhere
   near enough for a full agent run. Switching to the more established
   `gemini-2.5-flash` hit the **same 20/day cap** on this account/project,
   confirming it's an account-level free-tier restriction, not a
   model-specific fluke.

**Groq — the working development default (14,400 requests/day/model, free):**
1. Get a free API key: `https://console.groq.com/keys` → **Create API
   Key** → copy it. (Note: a newly-created key can take a short time to
   activate — if it errors immediately, try again in a few minutes, or use
   an older existing key if you have one.)
2. Groq's API is OpenAI-compatible, so it's called through the existing
   `openai` Python package, just pointed at
   `https://api.groq.com/openai/v1` — no extra dependency needed.
3. Add to `config.env`:
   ```
   LLM_PROVIDER=groq
   GROQ_API_KEY=<paste your real key>
   GROQ_MODEL=llama-3.3-70b-versatile
   ```

**Gotcha: duplicate keys in `config.env`.** This `.env` parser is a plain
line-by-line loop with no duplicate-key checking - if `LLM_PROVIDER` (or
any key) appears twice, whichever line comes LAST silently wins, with no
warning. Costed real debugging time when a second, older
`LLM_PROVIDER=gemini` block further down the file kept overriding a
correctly-updated `LLM_PROVIDER=groq` above it. Always search the whole
file for a setting before assuming an edit "didn't take."

**Module:** `agent/llm_wrapper.py` — the single chokepoint every LLM call
in the project goes through; routes to Gemini/OpenAI/Groq/Bedrock based on
`LLM_PROVIDER`, so the rest of the code never changes when you switch
providers. Includes retry-with-backoff on rate-limit errors so a
transient throttle waits instead of crashing a run.

**Command:**
```powershell
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python.exe agent\llm_wrapper.py
```

**Expected result:** Prints `pong`.

---

## Step 19 — Wire request IDs through L1/L2 for precise log lookup

**Purpose:** CLAUDE.md Section 2.1 calls for extracting "the Lambda request
ID from the response" so the agent can fetch the *exact* log for that
specific call (not a "most recent" guess).

**Change:** L1 and L2 now include `"request_id": context.aws_request_id`
in their structured output. Added `cloudwatch.get_log_text_for_request()`.

**Command (redeploy the updated Lambda code):**
```powershell
.venv\Scripts\python.exe setup\04_deploy_lambdas.py
```

**Console check:** rerun either Lambda's Test tab — the result JSON now
includes a `request_id` field.

---

## Step 20 — THE core flow: Gateway call → CloudWatch log → LLM conclusion → LOG entry

**Purpose:** This is the single most important piece of the whole project
per CLAUDE.md Section 2.1 — what the client said she will scrutinize most.

**Module:** `agent/agent.py`, function `call_lambda_and_reason()`. Full
flow per invocation:
1. Call the Lambda through the Gateway → structured JSON result.
2. Extract that Lambda's `request_id` from the result.
3. Fetch the *exact* CloudWatch log text for that one invocation.
4. Ask the LLM (via `llm_wrapper.ask_llm()`) to independently conclude
   SUCCESS/FAILURE from the log text alone — it never sees the Lambda's
   structured output.
5. Deterministically compare the LLM's conclusion to the Lambda's own
   structured field (plain string comparison — the LLM never decides
   pass/fail itself, per CLAUDE.md Section 2's control-flow rule).
6. Write everything to the global `LOG` in B3, in a learner-readable
   format.

**Command:**
```powershell
.venv\Scripts\python.exe agent\agent.py
```

**Gotcha #1:** `filter_log_events` with the request ID as a search filter
returned nothing — CloudWatch's search/filter index lags behind raw log
storage by more than the few seconds a direct
`describe_log_streams`/`get_log_events` read needs. Fixed by checking the
most recent log streams directly instead of searching.

**Gotcha #2:** even after that fix, the log text returned included
**multiple invocations concatenated together** — Lambda reuses warm
execution environments across calls, so one log stream can hold several
runs back to back. Fixed with `_extract_invocation()`, which slices out
just the `START...REPORT` block for the specific `request_id`.

**Expected result (a real, clean example):**
```
--- File: sample_003.txt | Iteration: 1 | Timestamp: 2026-07-27T15:35:49Z ---
Lambda copy_file structured output: {'file': 'sample_003.txt', 'success': True, 'original_hash': '5f2cbdec...', 'error': None, 'request_id': '5cc2fa59-1800-4db0-9a79-4f196da9d1fa'}
CloudWatch log text:
START RequestId: 5cc2fa59-1800-4db0-9a79-4f196da9d1fa Version: $LATEST
L1 start: copying sample_003.txt from agentcore-demo-source-... to agentcore-demo-dest-...
L1: computed original SHA-256 for sample_003.txt: 5f2cbdec...
L1: copied sample_003.txt to agentcore-demo-dest-...
L1 result: {'file': 'sample_003.txt', 'success': True, 'original_hash': '5f2cbdec...', 'error': None, 'request_id': '5cc2fa59-1800-4db0-9a79-4f196da9d1fa'}
END RequestId: 5cc2fa59-1800-4db0-9a79-4f196da9d1fa
REPORT RequestId: 5cc2fa59-1800-4db0-9a79-4f196da9d1fa  Duration: 438.80 ms ...
Agent LLM conclusion: SUCCESS
The log explicitly shows 'success': True and 'error': None in the L1 result line...
COMPARISON: Lambda says SUCCESS, Agent concludes SUCCESS -> MATCH
```

**Console check:** S3 → `agentcore-demo-log-<suffix>` → open `LOG` →
confirm the new entry matches this format.

---

## Step 21 — The full deterministic agent loop, run end-to-end

**Purpose:** Wrap the core flow (Step 20) in the actual while-loop from
CLAUDE.md Section 3.3: process every file currently in B1 one at a time,
re-inspecting B1 fresh each iteration, with a random ~40% chance of calling
L3 to corrupt each file before verification, deleting the original from
B1 only after L2 confirms the hashes match, and otherwise keeping it and
retrying it on a later iteration. Stops only when B1 is empty.

**Module:** `agent/agent.py` - added `list_b1_files()`, `process_one_file()`,
and `run_agent()` (the loop itself), with a 50-iteration safety cap.

**Command:**
```powershell
.venv\Scripts\python.exe agent\agent.py
```

**Gotcha: Gemini free-tier quota exhaustion mid-run.** The
`gemini-flash-latest` alias resolved to **Gemini 3.6 Flash**, a very new
model whose free tier only allows **20 requests/day** - far less than the
~1,500/day older Flash models get, and easily exhausted by a single full
agent run (each file needs 2-3 LLM calls). Fixed two ways:
1. Pinned `GEMINI_MODEL` in `config.env` to the established
   **`gemini-2.5-flash`** instead of the `-latest` alias, which has a much
   larger free-tier quota.
2. Added retry-with-backoff to `llm_wrapper.ask_llm()` for any rate-limit
   error (429/RESOURCE_EXHAUSTED/Throttling), so a transient rate limit
   waits and retries instead of crashing the whole run - important so Day
   2's recording doesn't die mid-demo.

**Note:** editing `config.example.env` does NOT update your already-created
`config.env` - that's your real local file and has to be edited directly
each time a new setting is added or changed.

**Expected result (a real run):**
```
[iteration 1] processing sample_005.txt (2 file(s) currently in B1)
[iteration 1] sample_005.txt -> verification_failed
[iteration 2] processing sample_004.txt (2 file(s) currently in B1)
[iteration 2] sample_004.txt -> verified
[iteration 3] processing sample_005.txt (1 file(s) currently in B1)
[iteration 3] sample_005.txt -> verification_failed
[iteration 4] processing sample_005.txt (1 file(s) currently in B1)
[iteration 4] sample_005.txt -> verified
B1 is empty. Agent run complete.
```
B1 ends up empty; B2 has every file (some corrupted-then-recopied along
the way); B3's `LOG` contains the full narrative for every file, including
at least one verification failure that got retried and eventually
succeeded - this single run demonstrates 5 of CLAUDE.md Section 3.5's 6
required scenarios in one go.

**Console check:** S3 → B1 (empty) → B2 (all 5 files) → B3 → `LOG` (full
narrative, readable start to finish).

---

## Step 22 — Demonstrate dynamic file discovery (files added mid-run)

**Purpose:** Prove a file dropped into B1 *while the agent is already
running* gets discovered and processed - the loop never captures a fixed
file list at the start. Its own acceptance criterion and one of the 6
required demo scenarios.

**Two-terminal exercise:**
1. Terminal 1: reseed B1 (`.venv\Scripts\python.exe setup\02_seed_files.py`),
   then start the agent (`.venv\Scripts\python.exe agent\agent.py`) - takes
   1-3 minutes for 5 files, giving a window to work in.
2. Terminal 2, partway through: create and upload a new file directly via
   the AWS CLI (not the project's own seed script - proves ANY file dropped
   in gets picked up):
   ```powershell
   1..20 | ForEach-Object { "Dynamic line $_" } | Out-File -Encoding utf8 sample_dynamic.txt
   aws s3 cp sample_dynamic.txt s3://agentcore-demo-source-<suffix>/sample_dynamic.txt
   ```

**Expected result:** Terminal 1 eventually shows
`[iteration N] processing sample_dynamic.txt ...` even though it was never
one of the original 5 files, then `B1 is empty. Agent run complete.`

**Video note:** this is the "Adding a new file during execution" scene
required in CLAUDE.md Section 6 - do this live with two terminals side by
side on Day 2.

---

## Step 23 — The pytest test suite (10 required cases + 2 extra)

**Purpose:** Automated proof of every required behavior in CLAUDE.md
Section 3.4, runnable with one command.

**Files:** `tests/conftest.py` / `tests/helpers.py` (fixtures + shared
helpers, plus a pre-run cleanup of leftover `test_*` files),
`tests/test_lambdas.py` (cases 1, 2, 4, 5 - direct boto3 Lambda invokes,
fast, no Gateway/LLM needed), `tests/test_reasoning.py` (cases 7, 8 - the
real `cloudwatch.py`/`llm_wrapper.py` functions), `tests/test_agent_loop.py`
(cases 3, 6, 9, 10 - full integration through the real Gateway, using
`agent.process_one_file()` directly rather than reimplementing its logic),
`tests/test_infra.py` (2 extra: LOG append-order, Gateway exposes all 3
tools). All test data uses `test_*.txt` names, never touching the real
`sample_*.txt` demo files.

**Command:**
```powershell
.venv\Scripts\pytest -v
```

**Expected result:** `12 passed` (took ~9-10 minutes on Groq - several
tests deliberately retry up to 15 times waiting for L3's random corruption
coin-flip to land on the specific outcome being tested).

**Recommended run order after any interrupted previous run:** `pytest`
first (its fixture cleans stray `test_*` files and each test self-cleans
on success), THEN `agent.py` (to process any remaining real `sample_*`
files) - avoids the two test runs' leftover files getting tangled
together in B1.

---

## Step 24 — Deploy the agent to AgentCore Runtime

**Purpose:** Until now, `agent.py` only ran as a local script. This step
hosts it on AWS AgentCore Runtime - a genuine "AWS AgentCore agent"
(client's #1 acceptance criterion), triggered via `agentcore invoke`
instead of a local Python command. Validated as a clean, single
configure-then-deploy pass with no redeploys needed.

**Why `agent.py` is already built the way it is:** `agent/agent.py`'s
`if __name__ == "__main__":` block starts the AgentCore app server
(`app.run()`) by default - this is what the deployed container actually
runs, and it must wait for an invocation rather than eagerly draining B1
at startup. Passing `--run-once` on the command line switches it back to
the direct "drain B1 and exit" behavior for local dev/testing. And
`setup/03_iam_roles.py`'s `create_runtime_exec_role()` pre-creates a fully
permissioned Runtime execution role (S3, Gateway, Lambda CloudWatch Logs,
plus AWS's documented baseline Runtime permissions) BEFORE deployment,
so the very first deploy has everything it needs - no auto-created,
under-permissioned role, no fixing-and-redeploying.

**Commands, in order:**
```powershell
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python.exe setup\03_iam_roles.py
```
The second command prints the exact `agentcore configure` command to run
next, including the pre-created role's ARN - copy and run it:
```powershell
.venv\Scripts\agentcore configure -e agent/agent.py --execution-role <arn from previous command's output>
```
Answer the wizard: agent name `agentcore`, requirements file (Enter to
accept detected `requirements.txt`), deployment type Container (only
option without Docker/Finch/Podman installed), ECR repository (Enter to
auto-create), OAuth authorizer - **no**, request header allowlist - **no**,
memory setup - type **s** to skip. Then deploy:
```powershell
.venv\Scripts\agentcore deploy
```
This uses **CodeBuild** (cloud-based, no local Docker needed) to build an
ARM64 container, push it to a new ECR repository, and stand up the Runtime
endpoint - takes a few minutes.

**Verify it (reseed a couple of files first, then invoke):**
```powershell
.venv\Scripts\python.exe setup\02_seed_files.py
.venv\Scripts\agentcore invoke "{}"
```

**Expected result:**
```json
{"status": "complete", "message": "Processed 6 file(s) over 7 iteration(s). B1 is empty.", "files": [...]}
```

**Notes:**
- The toolkit itself prints a deprecation warning ("The Starter Toolkit
  CLI is no longer supported... use @aws/agentcore") on every command. It
  still works fully as of this build; check for a newer CLI if reproducing
  this later.
- If you ever need to read the Runtime's CloudWatch logs directly from Git
  Bash, prefix the command with `MSYS_NO_PATHCONV=1` - Git Bash otherwise
  mangles the leading `/` in log group names, producing a confusing
  "regex validation" error that has nothing to do with the actual log group.

**Expected result:**
```json
{"status": "complete", "message": "Processed 6 file(s) over 7 iteration(s). B1 is empty.", "files": [...]}
```
Confirmed working end to end - the agent genuinely runs hosted on AgentCore
Runtime now, not as a local script.

**Console check:** CloudWatch → `/aws/bedrock-agentcore/runtimes/<agent-id>-DEFAULT`
log group shows the same `[iteration N] processing ...` output as local
runs, proving it executed inside the Runtime container.

---

## Spec addendum (client-clarified, folded into CLAUDE.md Section 3)

The client sent explicit lists that were folded into CLAUDE.md as new
Sections 3.35 (Lambda output fields), 3.4 (10 required test cases), 3.5
(6 required demonstration scenarios), an expanded Section 6 (DOCX topic
list + video scene list), and a replaced Section 7 (21-item acceptance
checklist), plus a new Section 2.1 (CLIENT PRIORITY - the CloudWatch/LLM
flow above) and a multi-provider LLM section in Section 2/5. CLAUDE.md is
the authoritative source for all of these - use it directly when writing
tests, recording demo scenarios, and drafting the DOCX/video.