# CLAUDE.md — AWS AgentCore Educational Project (S3 → S3 File Mover)

This file is the single source of truth for this build. Read it fully at the start
of every session and follow it. It contains (1) how you must work with me, (2) the
locked architecture decisions, (3) the full functional spec, (4) the repo layout,
(5) constraints, and (6) the definition of done.

---

## 0. Who I am and how you must work with me

**Treat me as a complete AWS beginner.** I know Python, but I have never used AWS
AgentCore, Gateway, Cognito, or IAM in depth. I am building this as a paid Upwork
job and I also have to (a) record a video of the whole build and (b) write a DOCX
with a screenshot for every important step. So I need to *see and understand* each
step, not just have you run it silently.

**Golden rule: go ONE step at a time. Never batch multiple steps. After each step,
stop and wait for me to say "done" or "next" before continuing.** I need time to
watch, screenshot, and record.

**For every single step, give me ALL of the following, in this exact structure:**

> ### Step N — <short title>
> **Purpose:** one plain-English sentence on why we do this.
> **What I (Claude Code) will do:** the exact command or code, and I run it only
> after you approve.
> **What YOU do in the AWS Console (GUI) — do this too, so you understand it:**
> click-by-click path, e.g. "Sign in → search bar type `S3` → *Create bucket* →
> Bucket name: `...` → Region: `us-east-1` → leave defaults → *Create bucket*."
> Tell me exactly what to type and click. If a step is CLI-only with no console
> equivalent, say so.
> **Expected result:** what I should see on screen / in the terminal when it worked.
> **📸 Screenshot:** tell me exactly what to capture for my DOCX/video at this point.
> **Common errors & fixes:** the 1–3 things most likely to go wrong here, and how to
> fix each.

Always explain AWS jargon the first time it appears (what an IAM role is, what a
policy is, what an ARN is, what SigV4 means) in one plain sentence. Assume nothing.

When something fails, read the actual error, explain in plain English what it means,
and fix it — don't just retry blindly.

**Timeline: this is a 2-DAY job.** Day 1 = build it and get both scenarios working.
Day 2 = clean rebuild while I record the video and take screenshots. Keep us on that
track. If we're drifting (too much time on one step), say so and suggest the fastest
path that still meets the spec. At the end of each step, give me a rough sense of
where we are ("~step 6 of ~20, on track for Day 1").

**"Safe to stop for the night" checkpoint (IMPORTANT — I don't want overnight
charges).** At the end of EVERY step, tell me in one line whether this is a clean
stopping point. The moment I say anything like "I'm tired", "going to sleep",
"stopping for tonight", or "that's it for today", do NOT start new work. Instead give
me a **SHUTDOWN CHECKLIST**:
> 1. Anything that could still cost money while I sleep + the exact command to stop or
>    delete it (e.g. an AgentCore Runtime endpoint/session left running, any
>    provisioned/always-on resource). If nothing is billable-at-rest, say so plainly.
> 2. What's safe to leave (tiny S3 files, IAM roles, un-invoked Lambdas — all ~$0 at
>    rest).
> 3. Where we are in the plan and the exact first step to resume tomorrow.
Confirm I'm not leaving anything in a running/billable state before I close the laptop.
If tearing something down now would mean rebuilding it tomorrow, tell me the tradeoff
so I can choose (usually: tear down runtime/endpoints, keep buckets + roles + code).

**Cost-first rule.** Always pick the cheapest option that still meets the spec.
Prefer always-free and free-tier usage; only spend on the few things that have no
free tier (AgentCore Runtime, Bedrock tokens), and keep even those minimal — smallest
model that works, tiny sample files, few test files, and don't re-run the full agent
more times than we need. Before any action that isn't free, tell me in one line
roughly what it will cost. Full-project spend should stay in the low single-digit
dollars (and $0 if new-account credits apply). Details are in Section 5.

---

## 1. What we are building (one paragraph)

An educational project where an **AWS AgentCore agent** automatically moves text
files from one S3 bucket to another by calling **AWS Lambda** functions, verifying
each copy with a **SHA-256 hash**, deliberately corrupting some files to prove the
agent can *detect* failures, reading **CloudWatch Logs** to form its own text
conclusion, comparing that conclusion to the Lambda's structured result, and writing
a full audit trail to a **global LOG file** in a third bucket. It must be small,
readable, and reproducible by the client on their own AWS account from a Windows
laptop.

---

## 2. Locked architecture decisions — DO NOT change without asking me

- **Compute for the agent:** AWS **AgentCore Runtime**. Deploy with the
  `bedrock-agentcore-starter-toolkit` (`agentcore configure`, `agentcore launch`).
  Prefer the toolkit's **cloud build (CodeBuild)** so we do NOT need local Docker on
  Windows.
- **Agent framework:** **Strands Agents SDK** (AWS's recommended path) with an MCP
  client to reach the Gateway.
- **Tool access:** the three Lambdas are exposed as **MCP tools through AgentCore
  Gateway** (Lambda targets). The agent does NOT call Lambda with `boto3.invoke`; it
  calls them as Gateway tools.
- **Gateway inbound auth (agent → Gateway):** use **IAM (SigV4)** to keep the
  client's reproduction simple (no Cognito, no OAuth flow). In the DOCX, mention
  Cognito JWT as the "production" alternative but implement IAM SigV4.
- **Gateway outbound auth (Gateway → Lambda):** a Gateway **execution IAM role**
  with `lambda:InvokeFunction` on the three functions.
- **LLM / model:** the project supports multiple LLM providers, switchable via
  `LLM_PROVIDER` in `config.env` (`bedrock`, `openai`, `gemini`, `groq`). Default
  for development: **Groq** (`llama-3.3-70b-versatile`, free tier of 14,400
  requests/day per model — Gemini's free tier turned out to cap at only 20
  requests/day per model on this account, too low for a full agent run).
  Default for client/documentation: **Bedrock**. `agent.py` must use a simple
  LLM wrapper function that picks the right provider based on config. The
  agent logic stays identical regardless of provider — only the API call
  changes.
- **Region:** default **us-east-1** (best feature + docs coverage). If latency to
  Pakistan matters, `ap-south-1` (Mumbai) is the fallback — but confirm AgentCore +
  the chosen Bedrock model are both available there before switching.
- **THE CRITICAL CONTROL-FLOW RULE (read carefully):** all deterministic logic lives
  in **our Python agent loop**, NOT in the LLM's free choice. The loop decides order,
  one-file-at-a-time processing, the random L3 coin flip, and "delete original only
  after verify." The **LLM is used ONLY** to read CloudWatch log *text* and produce a
  text conclusion, then compare it to the Lambda's structured output. Never let the
  model decide whether to delete a file or whether verification passed on its own.

---

## 2.1 CLIENT PRIORITY — read this first before writing any agent code

The client explicitly said the main difficulty and what she will scrutinize most is:
- How the agent reads CloudWatch logs
- How the agent understands them
- How the agent communicates with Lambdas through Gateway
- How the agent makes smart logs

This means `agent.py` must clearly implement this flow after EVERY Lambda call:
1. Call L1 or L2 through Gateway → get structured JSON result (e.g.
   `{success: true, hash: "9f86d08..."}`)
2. Extract the Lambda request ID from the response
3. Use the boto3 CloudWatch Logs client to fetch the raw log entries for that
   request ID from the Lambda's log group
4. Send the raw log text to the LLM with a prompt like: "Here is a CloudWatch
   log from a Lambda operation. Based only on this log text, did the operation
   succeed or fail? State your conclusion and the evidence from the log."
5. Receive the LLM's text conclusion
6. Compare the LLM conclusion to the Lambda structured output — do they agree
   or disagree?
7. Write ALL of the following to the LOG file in B3: the structured output,
   the raw CloudWatch log text, the LLM's conclusion, and the comparison
   result

The LOG entry must be readable by a learner. Example format:

```
--- File: sample_001.txt | Iteration: 3 | Timestamp: 2026-07-27T14:32:00Z ---
Lambda L1 structured output: {success: true, hash: "9f86d08..."}
CloudWatch log text: [the raw log]
Agent LLM conclusion: "Copy succeeded — log shows hash computed and file written"
COMPARISON: Lambda says SUCCESS, Agent concludes SUCCESS → MATCH
```

**This is the MOST IMPORTANT part of the project.** Build and show this flow
first before anything else. Every other piece (buckets, IAM, deployment)
exists to support this.

---

## 3. Full functional spec

### 3.1 S3 buckets (names must be globally unique — suffix with account id or random)
- **B1 — source:** holds original text files. We auto-create many sample `.txt`
  files here. More files may be dropped in *while the agent runs*.
- **B2 — destination:** holds files copied from B1.
- **B3 — log:** holds ONE global log file named `LOG` (append to it every step).

### 3.2 Lambda functions (all Python, all log to CloudWatch)
- **L1 — Copy File.** Input: B1 name, B2 name, file name. Steps: read file from B1 →
  compute SHA-256 of original → copy to B2 → return `{file, success: yes/no,
  original_hash, error?}` → write execution info to CloudWatch.
- **L2 — Verify File.** Input: B2 name, file name, original hash from L1. Steps: read
  copied file from B2 → compute SHA-256 → compare to original hash → return `{file,
  original_hash, copied_hash, match: yes/no, error?}` → write to CloudWatch.
- **L3 — Create a Copy Error (failure simulator).** Steps: read a copied `.txt` from
  B2 → remove the **last 10 lines** → save back to B2 → log the simulated error to
  CloudWatch. Called *randomly* by the loop for some files to prove detection works.

### 3.3 Agent loop (deterministic; runs with no user request)
Repeat WHILE B1 is not empty:
1. List B1; if empty, stop.
2. Pick one currently-available file (re-inspect B1 every iteration — never use a
   fixed list captured at the start; new files added mid-run must be discovered).
3. Append the selected file name to the global `LOG` in B3.
4. Call **L1** via Gateway → get `{success, original_hash}`.
5. Fetch L1's CloudWatch log entry → feed the log *text* to the LLM → get a text
   conclusion (succeeded/failed) → compare to L1's structured `success` → save both
   conclusions + the comparison to `LOG`.
6. Random coin flip → maybe call **L3** to corrupt the B2 copy.
7. Call **L2** via Gateway → get hash match/no-match. Fetch L2's CloudWatch log →
   LLM forms a conclusion → decide if verification passed → record evidence in `LOG`.
8. If verified → delete original from B1. If failed → keep original in B1, record
   the failure.
9. Continue to next file. End only when B1 is empty.

### 3.35 Lambda output field requirements
- **L1 (Copy File)** output must include: file name, yes/no result, original file
  hash, error info when copy fails.
- **L2 (Verify File)** output must include: file name, original hash, copied file
  hash, hashes match yes/no, error info when verification fails.
- **L3 (Create a Copy Error)** has no structured return needed beyond success — it
  just corrupts and logs.

### 3.4 Required test cases (client-specified minimum)
Every one of the following must have a corresponding automated test in `tests/`:
1. A file is copied successfully.
2. Original and destination hashes match.
3. Original file deleted from B1 only after verification succeeds.
4. L3 removes last 10 lines from a destination file.
5. L2 detects the hash mismatch created by L3.
6. Agent keeps original file in B1 after verification fails.
7. Agent reads CloudWatch logs and produces a text conclusion.
8. Agent compares log-based conclusion with Lambda structured output.
9. Multiple files processed one at a time through the while loop.
10. A new file added to B1 during execution is discovered and processed.

Plus any additional tests needed to cover every developed function — the list above
is the client's explicit minimum, not a ceiling.

### 3.5 Required demonstration scenarios (must appear in video + LOG)
- L1 reports successful copy AND CloudWatch logs support it.
- L1 reports a failure AND logs explain the failure.
- L3 corrupts a copied file.
- L2 detects hashes do not match.
- Agent does NOT delete original from B1 after verification failure.
- Agent continues processing other files after a failure.

### 3.6 Global LOG entry — must include for each file
timestamp, file name, loop iteration #, agent action, Lambda called, Lambda input
summary, Lambda structured output, CloudWatch log text, agent's conclusion from the
log, comparison (Lambda output vs agent conclusion), original SHA-256, destination
SHA-256, hash comparison result, whether L3 was called, whether the file was
corrupted, final file status, whether the original was deleted from B1, error info.
Write it so a *learner* can follow what the agent did and why.

---

## 4. Repo layout to scaffold

```
.
├── CLAUDE.md                  # this file
├── README.md                  # quickstart: deploy in 1 command, run in 1 command
├── requirements.txt
├── config.example.env         # region, model id, bucket suffix — NO secrets
├── setup/
│   ├── 01_create_buckets.py
│   ├── 02_seed_files.py       # auto-generates the sample .txt files in B1
│   ├── 03_iam_roles.py        # lambda exec role, gateway exec role, agent role
│   ├── 04_deploy_lambdas.py   # L1, L2, L3
│   ├── 05_create_gateway.py   # gateway + IAM inbound auth + 3 Lambda targets
│   └── 06_deploy_agent.py     # agentcore configure + launch
├── lambdas/
│   ├── l1_copy/handler.py
│   ├── l2_verify/handler.py
│   └── l3_corrupt/handler.py
├── agent/
│   ├── agent.py               # Strands agent: the deterministic loop + LLM reasoning
│   ├── gateway_client.py      # MCP/SigV4 connection to the Gateway
│   ├── cloudwatch.py          # read log entries for a given invocation
│   ├── logbook.py             # append structured entries to B3/LOG
│   └── llm_wrapper.py         # single function: prompt in, calls configured
│                               # LLM provider (Gemini/OpenAI/Bedrock), text out.
│                               # All LLM calls in the project go through this.
├── tests/                     # pytest — one test per acceptance case below
├── cleanup/
│   └── teardown.py            # delete buckets, lambdas, gateway, roles, log groups
└── docs/
    └── STEPS.md               # running step log I can paste into the DOCX
```

### What the student runs end-to-end (answer to the client's question)

The deliverable is a **small package**, NOT one giant script and NOT a notebook.
Reason: the AgentCore agent gets deployed to Runtime (a notebook can't host it), and
the client must reproduce it step by step on their own account. After the lesson the
student can, from a Windows terminal:

- **Deploy everything** in one command (wraps steps 01–06): `python setup/deploy_all.py`
- **Create + run the agent on AgentCore** and watch it process files: `agentcore invoke` (or `python run.py`)
- **Run the tests**: `pytest`
- **Tear it all down**: `python cleanup/teardown.py`

So the student ends up able to *create the agent on AWS AgentCore, run it, and test
it* — each as a single, documented command, with the console/GUI equivalent shown for
every step per Section 0.

---

## 5. Constraints & conventions

- **No hardcoded credentials, keys, secrets, or account IDs.** Use IAM roles + env
  vars (`config.env`, git-ignored). `config.example.env` shows the shape with no real
  values.
- Python 3.12, `boto3`, `strands-agents`, `bedrock-agentcore-starter-toolkit`.
- Google Gemini API key goes in `config.env` as `GEMINI_API_KEY` — git-ignored,
  never committed. `config.example.env` shows the shape only.
- Groq API key goes in `config.env` as `GROQ_API_KEY` — same rule, git-ignored.
- `requirements.txt` must include the `google-genai` package for Gemini
  support, and the `openai` package for OpenAI support (Groq is also called
  through the `openai` package, using Groq's OpenAI-compatible endpoint — no
  extra dependency needed). `boto3` already covers Bedrock.
- Tag every resource with `Project=agentcore-demo` so cleanup and cost tracking are
  easy.
- Idempotent setup scripts where possible (safe to re-run).
- **Cost guardrail:** early on, walk me through creating an **AWS Budgets** alert at
  **$10** so nothing surprises me. Expected real spend for the whole project is only
  a few dollars, covered by new-account credits — but set the alarm anyway. Keep
  spend minimal per the cost-first rule in Section 0: cheapest working model, tiny
  sample files, minimal agent re-runs, and tear down AgentCore Runtime/endpoints when
  not actively using them. Never leave a billable resource running overnight.
- **Git / GitHub:** initialise git from the start and commit as we go. Add a
  `.gitignore` that excludes `config.env`, `*.env` (except the example), `__pycache__`,
  and any build artifacts — **never commit secrets or account IDs.** Use a **PRIVATE**
  GitHub repo for version control and handoff. This is paid client work, so do NOT make
  it public unless the client explicitly agrees they're fine with that (the client
  likely owns the code). If unsure, keep it private and deliver via the repo or a zip.
- Keep each file short and heavily commented; this is a *teaching* project.
- As we complete each step, append a short entry to `docs/STEPS.md` (purpose,
  command, console path, expected result, common errors) so I can lift it straight
  into the DOCX.

---

## 6. Deliverables (what the client is paying for)

1. All source code above, runnable on the client's own AWS account from Windows.
2. A **DOCX** with a screenshot for every important step (purpose, exact command,
   console location, screenshot, expected result, common errors). You draft the text;
   I take the screenshots. Must cover, at minimum, every one of these topics:
   - How to prepare a Windows laptop
   - Install Python and packages
   - Configure AWS access
   - Create the project
   - Create three S3 buckets
   - Develop and deploy L1/L2/L3
   - Configure IAM
   - Configure CloudWatch
   - Develop the AgentCore agent
   - Implement the while loop
   - How the agent reads Lambda outputs
   - How the agent reads CloudWatch logs
   - How the agent writes LOG to B3
   - Run every test
   - Add files dynamically during execution
   - Inspect results
   - Troubleshoot errors
   - Delete all AWS resources
3. An **end-to-end instructional video** showing the build from scratch (not just the
   finished project). You can't record it — but help me by keeping the build linear
   and screenshot-friendly so the recording is clean. Must include, at minimum, these
   scenes:
   - Creating project files
   - Writing Python code
   - Creating S3 buckets
   - Creating sample text files
   - Developing L1/L2/L3
   - Configuring IAM
   - Configuring CloudWatch
   - Developing the AgentCore agent
   - Implementing the while loop
   - Deploying the project
   - Running successful scenario
   - Running corrupted-file scenario
   - Reading CloudWatch logs
   - Reviewing agent decisions
   - Reviewing the global LOG file
   - Adding a new file during execution
   - Running all tests
   - Debugging at least one failure
   - Cleaning up all AWS resources

---

## 7. Definition of done — acceptance checklist

- [ ] Uses AWS AgentCore.
- [ ] Code in Python.
- [ ] Three S3 buckets (B1/B2/B3).
- [ ] Three Lambdas (L1/L2/L3).
- [ ] Agent processes files automatically without user request.
- [ ] Agent continues while files exist in B1.
- [ ] Dynamically added files discovered.
- [ ] L1 copies one file, returns success/failure and original hash.
- [ ] L2 calculates destination hash and compares to original.
- [ ] L3 creates controlled error by removing last 10 lines.
- [ ] Agent reads CloudWatch logs for each operation.
- [ ] Agent creates text-based conclusion from logs.
- [ ] Agent compares conclusion with Lambda structured output.
- [ ] Every step recorded in global LOG in B3.
- [ ] Original deleted from B1 only after successful verification.
- [ ] Failed verification leaves original in B1.
- [ ] All developed agent functions tested.
- [ ] Client can deploy and run from Windows laptop.
- [ ] Complete DOCX with screenshots delivered.
- [ ] Complete end-to-end video delivered.
- [ ] Client can reproduce by following DOCX and video.

---

## 8. First actions for this session

1. Confirm region + model choice with me.
2. Walk me through the AWS Budgets $10 alert (Step 1), then scaffold the repo
   (Step 2). One step at a time, using the Step template in Section 0.
