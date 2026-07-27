"""
Generates the complete, beginner-friendly DOCX guide for this project:
docs/AgentCore_Project_Guide.docx

Written for someone who has never used AWS before. Every step is presented
as the clean, correct procedure (not a debugging transcript) - follow it
in order on a fresh AWS account and it works the first time through.

Run: python docs/generate_docx.py
"""

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Pt, RGBColor, Inches

MONO_FONT = "Consolas"


def set_cell_shading(cell, hex_color):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


def add_code_block(doc, text):
    """A shaded single-cell table, styled monospace - reads clearly as code."""
    table = doc.add_table(rows=1, cols=1)
    table.autofit = True
    cell = table.rows[0].cells[0]
    set_cell_shading(cell, "F0F0F0")
    cell.text = ""
    p = cell.paragraphs[0]
    lines = text.strip("\n").split("\n")
    run = p.add_run(lines[0])
    run.font.name = MONO_FONT
    run.font.size = Pt(9.5)
    for line in lines[1:]:
        p2 = cell.add_paragraph()
        r2 = p2.add_run(line)
        r2.font.name = MONO_FONT
        r2.font.size = Pt(9.5)
    doc.add_paragraph()


def add_labeled(doc, label, text):
    p = doc.add_paragraph()
    r = p.add_run(f"{label}: ")
    r.bold = True
    p.add_run(text)


def add_screenshot_placeholder(doc, text):
    p = doc.add_paragraph()
    r = p.add_run(f"\U0001F4F8 SCREENSHOT: {text}")
    r.bold = True
    r.font.color.rgb = RGBColor(0xB0, 0x00, 0x00)
    r.italic = True


def add_step(doc, number, title, purpose, command=None, console=None,
             expected=None, screenshot=None, errors=None, code=None,
             code_label=None, note=None):
    doc.add_heading(f"Step {number} — {title}", level=2)
    add_labeled(doc, "Purpose", purpose)

    if command:
        p = doc.add_paragraph()
        p.add_run("Command:").bold = True
        add_code_block(doc, command)

    if code:
        p = doc.add_paragraph()
        p.add_run(f"{code_label or 'Code'}:").bold = True
        add_code_block(doc, code)

    if console:
        p = doc.add_paragraph()
        p.add_run("AWS Console path: ").bold = True
        p.add_run(console)

    if expected:
        add_labeled(doc, "Expected result", "")
        add_code_block(doc, expected) if "\n" in expected or "{" in expected else doc.add_paragraph(expected)

    if screenshot:
        add_screenshot_placeholder(doc, screenshot)

    if note:
        p = doc.add_paragraph()
        r = p.add_run("Note: ")
        r.italic = True
        r.bold = True
        r2 = p.add_run(note)
        r2.italic = True

    if errors:
        p = doc.add_paragraph()
        p.add_run("Common errors & fixes:").bold = True
        for err, fix in errors:
            bullet = doc.add_paragraph(style="List Bullet")
            r = bullet.add_run(err)
            r.bold = True
            bullet.add_run(" — " + fix)

    doc.add_paragraph()


def main():
    doc = Document()

    # Base style
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    title = doc.add_heading("AWS AgentCore S3 File Mover", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub = doc.add_paragraph("A Complete Beginner's Build Guide")
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub.runs[0].italic = True
    sub.runs[0].font.size = Pt(14)
    doc.add_paragraph()

    # ---------------------------------------------------------------
    doc.add_heading("Introduction", level=1)
    doc.add_paragraph(
        "This guide walks you through building, from scratch, an educational AWS "
        "AgentCore project. An autonomous AI agent moves text files from one Amazon "
        "S3 bucket to another by calling AWS Lambda functions, verifies each copy "
        "with a SHA-256 hash, deliberately corrupts some files to prove it can "
        "detect failures, reads Amazon CloudWatch Logs to form its own plain-English "
        "conclusion about what happened, compares that conclusion to what the Lambda "
        "function actually reported, and writes a complete audit trail to a log file. "
        "It finishes hosted on AWS AgentCore Runtime, so it runs as a real cloud "
        "service, not just a script on your laptop."
    )
    doc.add_paragraph(
        "You do not need any prior AWS experience. Every AWS term is explained the "
        "first time it appears. You do need: a Windows laptop, an AWS account "
        "(free to create, a small amount of real spend is expected - a few dollars "
        "at most), and about half a day."
    )
    doc.add_paragraph(
        "Each step below gives you: why we're doing it, the exact command to run, "
        "where to look in the AWS Console to see the result, what success looks "
        "like, and the most common mistakes at that step. Follow the steps in "
        "order - each one builds on the last."
    )

    # =================================================================
    doc.add_heading("Part 1 — Prepare Your Windows Laptop", level=1)

    add_step(
        doc, "1.1", "Find your AWS account number",
        "Several later steps need your 12-digit AWS account ID to build unique "
        "resource names.",
        console="Sign in to the AWS Console → click your account name in the "
                "top-right corner → your Account ID is shown there (format: "
                "111122223333). Write it down.",
        screenshot="The account menu showing your Account ID.",
    )

    add_step(
        doc, "1.2", "Download and install Python 3.12",
        "This project is written in Python and needs version 3.12 specifically - "
        "some of the AWS libraries used here are new and may not yet support the "
        "very latest Python release.",
        command=None,
        expected="Running `py -0` in a terminal lists Python 3.12 as an installed version.",
        screenshot="The installer's first screen, and the `py -0` output afterward.",
        errors=[
            ("Forgot to check “Add python.exe to PATH” during install",
             "re-run the installer, choose Modify, and check the box (or just reinstall)."),
        ],
        note="Go to https://www.python.org/downloads/release/python-3120/ (or search "
             "\"python 3.12 download\" for the latest 3.12.x patch) → download the "
             "Windows installer (64-bit) → run it → check “Add python.exe to "
             "PATH” on the first screen → Install Now → close and reopen your terminal.",
    )

    add_step(
        doc, "1.3", "Download and install the AWS CLI",
        "The AWS Command Line Interface (CLI) is what lets Python scripts on your "
        "laptop authenticate to and control your AWS account.",
        note="Go to https://awscli.amazonaws.com/AWSCLIV2.msi → run the installer "
             "with all defaults → close and reopen your terminal.",
        command="aws --version",
        expected="aws-cli/2.x.x Python/3.x Windows/...",
        errors=[
            ("“aws: command not found”", "terminal wasn't reopened after install; "
             "close ALL terminal windows and reopen."),
        ],
    )

    # =================================================================
    doc.add_heading("Part 2 — Set Up AWS Access", level=1)

    add_step(
        doc, "2.1", "Create an IAM user and access key",
        "You should never use your AWS root login for day-to-day work. This "
        "creates a separate identity (called an IAM user) with its own key, just "
        "for this project's scripts.",
        console="IAM → Users → Create user → name it agentcore-cli-user → "
                "Next → Attach policies directly → check AdministratorAccess "
                "(simplest for a learning project) → Next → Create user. Then "
                "click into the user → Security credentials tab → Access keys → "
                "Create access key → use case: Command Line Interface (CLI) → "
                "check the confirmation box → Next → Create access key → "
                "download the .csv file (the secret key is shown only this once).",
        screenshot="The new access key's Security credentials page (with the secret "
                   "key value cropped out before sharing this screenshot with anyone).",
        errors=[
            ("Lost the CSV before copying the secret key",
             "go back to Security credentials → deactivate that key → create a new one."),
        ],
    )

    add_step(
        doc, "2.2", "Configure the AWS CLI with your new key",
        "This tells the AWS CLI (and every Python script in this project) which "
        "AWS account and identity to use.",
        command="aws configure",
        note="It will prompt for four things: AWS Access Key ID and AWS Secret "
             "Access Key (paste from the CSV downloaded in Step 2.1), Default "
             "region name: us-east-1, Default output format: json.",
        expected="Then run `aws sts get-caller-identity` - it should print your "
                 "Account ID and the ARN arn:aws:iam::<account-id>:user/agentcore-cli-user",
        errors=[
            ("InvalidClientTokenId", "typo pasting the access key; rerun aws configure."),
        ],
    )

    # =================================================================
    doc.add_heading("Part 3 — Create the Project", level=1)

    add_step(
        doc, "3.1", "Scaffold the project folder",
        "Sets up the folder structure and puts it under version control from the start.",
        command="mkdir \"client project\"\ncd \"client project\"\ngit init",
        note="Create these folders inside it: setup/, lambdas/l1_copy/, "
             "lambdas/l2_verify/, lambdas/l3_corrupt/, agent/, tests/, cleanup/, docs/",
    )

    add_step(
        doc, "3.2", "Create the Python 3.12 virtual environment",
        "Keeps this project's Python packages isolated from anything else on "
        "your machine, and pinned to Python 3.12.",
        command="py -3.12 -m venv .venv\n.venv\\Scripts\\python.exe --version",
        expected="Python 3.12.x",
    )

    add_step(
        doc, "3.3", "Install project dependencies",
        "Installs every Python package this project needs.",
        code_label="requirements.txt",
        code=(
            "boto3\n"
            "strands-agents\n"
            "bedrock-agentcore\n"
            "bedrock-agentcore-starter-toolkit\n"
            "mcp-proxy-for-aws\n"
            "google-genai\n"
            "openai\n"
            "pytest"
        ),
        command=".venv\\Scripts\\pip install -r requirements.txt",
        expected="Ends with “Successfully installed ...” and no red error text.",
    )

    # =================================================================
    doc.add_heading("Part 4 — Configure the Project", level=1)

    add_step(
        doc, "4.1", "Get a free Groq API key (for the agent's LLM reasoning)",
        "The agent uses a large language model to independently read CloudWatch "
        "logs and draw its own conclusion. Groq provides this for free with a "
        "very generous limit (14,400 requests/day per model) - Google's Gemini "
        "free tier, by comparison, capped out at only 20 requests/day on ours, "
        "nowhere near enough for a full run.",
        note="Go to https://console.groq.com/keys → sign in → Create API Key "
             "→ copy it. (If a brand-new key errors immediately, wait a few "
             "minutes and try again, or use an older key if you have one.)",
    )

    add_step(
        doc, "4.2", "Create your config.env file",
        "This one local file holds every setting and secret the project needs. "
        "It is git-ignored and never committed - it must never leave your machine.",
        command="copy config.example.env config.env",
        code_label="config.env (fill in your own account ID and Groq key)",
        code=(
            "AWS_REGION=us-east-1\n"
            "BEDROCK_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0\n"
            "\n"
            "LLM_PROVIDER=groq\n"
            "GROQ_API_KEY=<paste your real Groq key here>\n"
            "GROQ_MODEL=llama-3.3-70b-versatile\n"
            "\n"
            "BUCKET_SUFFIX=<your 12-digit AWS account ID>\n"
            "BUCKET_SOURCE=agentcore-demo-source-<your account ID>\n"
            "BUCKET_DEST=agentcore-demo-dest-<your account ID>\n"
            "BUCKET_LOG=agentcore-demo-log-<your account ID>"
        ),
        note="Make sure each setting appears only ONCE in this file. If you paste "
             "a line twice, whichever copy comes LAST silently wins with no "
             "warning - always search the whole file before assuming a change "
             "“didn't take.”",
    )

    # =================================================================
    doc.add_heading("Part 5 — Build the Storage Layer", level=1)

    add_step(
        doc, "5.1", "Create the three S3 buckets",
        "B1 holds the original text files, B2 holds the copies, B3 holds one "
        "global audit-trail log file. An S3 bucket is just a named container "
        "for files in Amazon's storage service.",
        command=".venv\\Scripts\\python.exe setup\\01_create_buckets.py",
        console="S3 → confirm all three buckets exist: agentcore-demo-source-..., "
                "agentcore-demo-dest-..., agentcore-demo-log-...",
        expected="Script prints “Created ...” for each of the three buckets.",
        screenshot="The S3 console bucket list showing all three.",
        errors=[
            ("BucketAlreadyExists", "S3 names are globally unique across ALL AWS "
             "accounts everywhere; change BUCKET_SUFFIX in config.env to something "
             "more unique and rerun."),
        ],
    )

    add_step(
        doc, "5.2", "Seed sample text files into B1",
        "Gives the agent something to actually process: 5 small, readable "
        "20-line .txt files.",
        command=".venv\\Scripts\\python.exe setup\\02_seed_files.py",
        console="S3 → the source bucket → confirm sample_001.txt through "
                "sample_005.txt exist.",
        expected="Done: 5 sample files in agentcore-demo-source-...",
    )

    # =================================================================
    doc.add_heading("Part 6 — Build the Lambda Functions", level=1)
    doc.add_paragraph(
        "A Lambda function is a small piece of code AWS runs for you on demand - "
        "you supply the code, AWS runs it when invoked, and you never manage a "
        "server. This project uses three: L1 copies a file and hashes it, L2 "
        "verifies a copy's hash, and L3 deliberately corrupts a file to prove "
        "verification actually works."
    )

    add_step(
        doc, "6.1", "Create the IAM roles (Lambda, Gateway, and Runtime)",
        "An IAM role is an identity an AWS service can “become” to perform "
        "actions, with a defined set of permissions. This one script creates "
        "all three roles this project needs: one for the Lambda functions "
        "(reads/writes only the specific buckets they need), one for the "
        "AgentCore Gateway (lets it call the Lambdas), and one for AgentCore "
        "Runtime (lets the deployed agent call everything it needs to).",
        command=".venv\\Scripts\\python.exe setup\\03_iam_roles.py",
        console="IAM → Roles → confirm agentcore-demo-lambda-exec-role, "
                "agentcore-demo-gateway-exec-role, and agentcore-demo-runtime-exec-role "
                "all exist.",
        expected="Prints the ARN of each of the three roles it created, plus the "
                 "exact `agentcore configure` command you'll use in Part 11 - keep "
                 "that command; you'll need it later.",
    )

    add_step(
        doc, "6.2", "Deploy and test L1 (Copy File)",
        "Reads a file from B1, computes its SHA-256 hash, copies it to B2, and "
        "returns a structured result including its own CloudWatch request ID.",
        code_label="lambdas/l1_copy/handler.py",
        code=(
            "import hashlib\n"
            "import boto3\n"
            "\n"
            "s3 = boto3.client(\"s3\")\n"
            "\n"
            "\n"
            "def lambda_handler(event, context):\n"
            "    source_bucket = event[\"source_bucket\"]\n"
            "    dest_bucket = event[\"dest_bucket\"]\n"
            "    file_name = event[\"file_name\"]\n"
            "\n"
            "    print(f\"L1 start: copying {file_name} from {source_bucket} to {dest_bucket}\")\n"
            "\n"
            "    try:\n"
            "        obj = s3.get_object(Bucket=source_bucket, Key=file_name)\n"
            "        body = obj[\"Body\"].read()\n"
            "\n"
            "        original_hash = hashlib.sha256(body).hexdigest()\n"
            "        s3.put_object(Bucket=dest_bucket, Key=file_name, Body=body)\n"
            "\n"
            "        result = {\n"
            "            \"file\": file_name,\n"
            "            \"success\": True,\n"
            "            \"original_hash\": original_hash,\n"
            "            \"error\": None,\n"
            "            \"request_id\": context.aws_request_id,\n"
            "        }\n"
            "        print(f\"L1 result: {result}\")\n"
            "        return result\n"
            "\n"
            "    except Exception as exc:\n"
            "        return {\n"
            "            \"file\": file_name, \"success\": False, \"original_hash\": None,\n"
            "            \"error\": str(exc), \"request_id\": context.aws_request_id,\n"
            "        }"
        ),
        command=".venv\\Scripts\\python.exe setup\\04_deploy_lambdas.py",
        console="Lambda → agentcore-demo-l1-copy → Test tab → create a new "
                "test event with the JSON below → Save → Test.",
        note='Test event JSON:\n{\n  \"source_bucket\": \"agentcore-demo-source-<your account ID>\",\n'
             '  \"dest_bucket\": \"agentcore-demo-dest-<your account ID>\",\n'
             '  \"file_name\": \"sample_001.txt\"\n}',
        expected='{"file": "sample_001.txt", "success": true, "original_hash": "...", '
                 '"error": null, "request_id": "..."}',
        screenshot="The Lambda Test tab showing the green “Succeeded” result.",
        errors=[
            ("InvalidParameterValueException: role ... is not valid",
             "IAM role hasn't finished propagating yet; wait ~30s and rerun the deploy script."),
            ("NoSuchKey", "file_name doesn't match an object actually in B1."),
        ],
    )

    add_step(
        doc, "6.3", "Deploy and test L2 (Verify File)",
        "Independently re-hashes the copy in B2 and compares it to the hash L1 "
        "reported - this is what later catches corruption.",
        code_label="lambdas/l2_verify/handler.py",
        code=(
            "import hashlib\n"
            "import boto3\n"
            "\n"
            "s3 = boto3.client(\"s3\")\n"
            "\n"
            "\n"
            "def lambda_handler(event, context):\n"
            "    dest_bucket = event[\"dest_bucket\"]\n"
            "    file_name = event[\"file_name\"]\n"
            "    original_hash = event[\"original_hash\"]\n"
            "\n"
            "    try:\n"
            "        obj = s3.get_object(Bucket=dest_bucket, Key=file_name)\n"
            "        body = obj[\"Body\"].read()\n"
            "        copied_hash = hashlib.sha256(body).hexdigest()\n"
            "        match = copied_hash == original_hash\n"
            "\n"
            "        result = {\n"
            "            \"file\": file_name, \"original_hash\": original_hash,\n"
            "            \"copied_hash\": copied_hash, \"match\": match, \"error\": None,\n"
            "            \"request_id\": context.aws_request_id,\n"
            "        }\n"
            "        print(f\"L2 result: {result}\")\n"
            "        return result\n"
            "\n"
            "    except Exception as exc:\n"
            "        return {\n"
            "            \"file\": file_name, \"original_hash\": original_hash,\n"
            "            \"copied_hash\": None, \"match\": False,\n"
            "            \"error\": str(exc), \"request_id\": context.aws_request_id,\n"
            "        }"
        ),
        console="Lambda → agentcore-demo-l2-verify → Test tab.",
        note='Test event JSON (use the real original_hash from Step 6.2’s result):\n'
             '{\n  \"dest_bucket\": \"agentcore-demo-dest-<your account ID>\",\n'
             '  \"file_name\": \"sample_001.txt\",\n'
             '  \"original_hash\": \"<hash from Step 6.2>\"\n}',
        expected='{"file": "sample_001.txt", ..., "match": true, "error": null, "request_id": "..."}',
    )

    add_step(
        doc, "6.4", "Deploy and test L3 (Create a Copy Error) + prove detection",
        "Deliberately strips the last 10 lines off a file already copied to B2, "
        "to prove L2 can genuinely detect real corruption.",
        code_label="lambdas/l3_corrupt/handler.py",
        code=(
            "import boto3\n"
            "\n"
            "s3 = boto3.client(\"s3\")\n"
            "\n"
            "\n"
            "def lambda_handler(event, context):\n"
            "    dest_bucket = event[\"dest_bucket\"]\n"
            "    file_name = event[\"file_name\"]\n"
            "\n"
            "    try:\n"
            "        obj = s3.get_object(Bucket=dest_bucket, Key=file_name)\n"
            "        body = obj[\"Body\"].read().decode(\"utf-8\")\n"
            "        lines = body.splitlines(keepends=True)\n"
            "        corrupted_lines = lines[:-10] if len(lines) > 10 else []\n"
            "        corrupted_body = \"\".join(corrupted_lines)\n"
            "\n"
            "        s3.put_object(Bucket=dest_bucket, Key=file_name,\n"
            "                      Body=corrupted_body.encode(\"utf-8\"))\n"
            "\n"
            "        result = {\"file\": file_name, \"success\": True, \"error\": None}\n"
            "        print(f\"L3 result: {result}\")\n"
            "        return result\n"
            "\n"
            "    except Exception as exc:\n"
            "        return {\"file\": file_name, \"success\": False, \"error\": str(exc)}"
        ),
        console="Lambda → agentcore-demo-l3-corrupt → Test tab, then re-test L2.",
        note='Part A - corrupt: {"dest_bucket": "agentcore-demo-dest-<your account ID>", '
             '"file_name": "sample_001.txt"}\n'
             'Part B - rerun L2’s Step 6.3 test event UNCHANGED (same original_hash) - '
             'now expect "match": false with a different copied_hash.',
        expected='L3: {"file": "sample_001.txt", "success": true, "error": null}\n'
                 'L2 rerun: "match": false',
        screenshot="Both results side by side — L3's success, then L2's match:false.",
        errors=[
            ("L2 still shows match: true", "L1 was rerun in between, which re-copies "
             "the clean file over the corrupted one - don't rerun L1 for this test."),
        ],
    )

    # =================================================================
    doc.add_heading("Part 7 — Build the Gateway", level=1)
    doc.add_paragraph(
        "The AgentCore Gateway is AWS's “front door” that turns backend things "
        "(here, our three Lambdas) into standardized tools an AI agent can call "
        "via MCP (Model Context Protocol - a standard way for AI models to "
        "discover and call tools). The agent authenticates to the Gateway with "
        "plain AWS credentials (called IAM/SigV4 authentication) rather than a "
        "separate login system, to keep this reproducible."
    )

    add_step(
        doc, "7.1", "Create the AgentCore Gateway",
        "Stands up the Gateway itself with IAM inbound authentication.",
        command=".venv\\Scripts\\python.exe setup\\05_create_gateway.py",
        console="Search “AgentCore” → Amazon Bedrock AgentCore → Gateways → "
                "confirm agentcore-demo-gateway shows inbound auth type AWS_IAM and "
                "status READY.",
        expected="Prints the Gateway ID and its MCP endpoint URL.",
    )

    add_step(
        doc, "7.2", "Register L1/L2/L3 as Gateway tools",
        "Turns each Lambda into a named tool (copy_file, verify_file, "
        "corrupt_file) the agent can call by name.",
        command=".venv\\Scripts\\python.exe setup\\05b_add_gateway_targets.py",
        console="Gateway → Targets tab → confirm 3 targets, all READY.",
    )

    add_step(
        doc, "7.3", "Test the Gateway end-to-end",
        "Proves the whole chain works: connecting with plain AWS credentials, "
        "listing the 3 tools, and calling one for real.",
        command=".venv\\Scripts\\python.exe agent\\gateway_client.py",
        expected="Lists 3 tools (each prefixed with its target name, e.g. "
                 "CopyFileTarget___copy_file - the Gateway does this automatically "
                 "to avoid name collisions), then successfully calls copy_file.",
    )

    # =================================================================
    doc.add_heading("Part 8 — Build the Agent's Reasoning Pieces", level=1)

    add_step(
        doc, "8.1", "Build the CloudWatch log reader",
        "Fetches the exact raw log text for one specific Lambda invocation, "
        "identified by its request ID - not just “the most recent” run, since "
        "Lambda can reuse one execution environment (and therefore one log "
        "stream) across several separate invocations.",
        command=".venv\\Scripts\\python.exe agent\\cloudwatch.py agentcore-demo-l1-copy",
        expected="Prints the START/END/REPORT block for the most recent invocation.",
    )

    add_step(
        doc, "8.2", "Build the LOG writer",
        "Appends entries to the single global LOG file in B3. S3 has no native "
        "append operation, so this reads the whole object, adds the new text, "
        "and writes it back - fine at this project's small scale.",
        command=".venv\\Scripts\\python.exe agent\\logbook.py",
        console="S3 → the log bucket → confirm object LOG exists.",
    )

    add_step(
        doc, "8.3", "Build the multi-provider LLM wrapper",
        "One single function, ask_llm(prompt), that every part of the agent "
        "calls - it routes to Groq, Gemini, OpenAI, or Bedrock based on one "
        "setting (LLM_PROVIDER) in config.env, so switching providers is a "
        "one-line config change, never a code change.",
        command=".venv\\Scripts\\python.exe agent\\llm_wrapper.py",
        expected="pong",
    )

    # =================================================================
    doc.add_heading("Part 9 — Build and Run the Agent", level=1)
    doc.add_paragraph(
        "This is the heart of the project. After every Lambda call, the agent: "
        "(1) gets the Lambda's structured JSON result, (2) extracts its request "
        "ID, (3) fetches the exact CloudWatch log text for that one call, (4) "
        "asks the LLM to independently read that log text and conclude SUCCESS "
        "or FAILURE, (5) deterministically compares the LLM's conclusion "
        "against the Lambda's own reported result, and (6) writes all of it - "
        "structured output, raw log text, LLM conclusion, and the comparison - "
        "to the global LOG. The LLM never decides whether to delete a file or "
        "whether verification passed; that decision always comes from the "
        "Lambda's own structured result, in plain Python code."
    )

    add_step(
        doc, "9.1", "Run the core flow on one file",
        "Demonstrates the full call → log → reasoning → compare → write flow "
        "on a single real file, so you can inspect one complete example before "
        "running the whole loop.",
        command=".venv\\Scripts\\python.exe agent\\agent.py --run-once",
        expected="Prints the Lambda's structured result and a complete LOG entry "
                 "ending in a line like: COMPARISON: Lambda says SUCCESS, Agent "
                 "concludes SUCCESS -> MATCH",
        screenshot="The terminal output, and the same entry open in the LOG file in B3.",
    )

    add_step(
        doc, "9.2", "Run the full agent loop",
        "The actual autonomous while-loop: process every file currently in B1 "
        "one at a time, re-checking B1 fresh every iteration, with roughly a "
        "40% random chance of deliberately corrupting each file before "
        "verification, deleting the original from B1 only if verification "
        "passes, and otherwise keeping it and retrying later. Stops only when "
        "B1 is empty.",
        command=".venv\\Scripts\\python.exe agent\\agent.py --run-once",
        expected="A series of [iteration N] processing ... -> outcome lines, ending "
                 "with “B1 is empty. Agent run complete.”",
        console="S3 → B1 (now empty), B2 (all files), B3 → LOG (full narrative).",
        screenshot="The full terminal run, and the LOG file showing at least one "
                   "verification failure that got retried and eventually succeeded.",
    )

    add_step(
        doc, "9.3", "Demonstrate dynamic file discovery",
        "Proves a file dropped into B1 while the agent is already running gets "
        "discovered and processed, since the loop never captures a fixed file "
        "list at the start.",
        note="Open a second terminal window while the agent from Step 9.2 is "
             "still running, and upload a brand-new file directly:\n"
             "1..20 | ForEach-Object { \"Dynamic line $_\" } | Out-File -Encoding utf8 sample_dynamic.txt\n"
             "aws s3 cp sample_dynamic.txt s3://agentcore-demo-source-<your account ID>/sample_dynamic.txt",
        expected="The first terminal eventually shows it processing sample_dynamic.txt "
                 "even though it was never one of the originally seeded files.",
    )

    # =================================================================
    doc.add_heading("Part 10 — Test Everything", level=1)

    add_step(
        doc, "10.1", "Run the automated test suite",
        "12 automated tests covering every required behavior: files copy "
        "successfully, hashes match, originals are deleted only after "
        "verification, L3's corruption is detected by L2, the agent keeps "
        "failed files in B1, multiple files are processed one at a time, and "
        "files added mid-run are discovered.",
        command=".venv\\Scripts\\pytest -v",
        expected="12 passed (takes several minutes - some tests deliberately retry "
                 "up to 15 times waiting for L3's random corruption coin-flip to "
                 "land on the specific outcome being tested).",
        screenshot="The full pytest output showing all 12 tests passing.",
    )

    # =================================================================
    doc.add_heading("Part 11 — Deploy to AWS AgentCore Runtime", level=1)
    doc.add_paragraph(
        "Until now the agent has only run as a local script. This step hosts it "
        "on AWS AgentCore Runtime - a real, managed cloud service - so it's "
        "invoked with agentcore invoke instead of a local Python command. This "
        "is what makes it a genuine AgentCore agent."
    )

    add_step(
        doc, "11.1", "Configure the Runtime deployment",
        "Points the AgentCore toolkit at agent.py and the fully-permissioned "
        "execution role already created in Step 6.1.",
        command="agentcore configure -e agent/agent.py --execution-role <ARN printed by Step 6.1>",
        note="Answer the interactive wizard: agent name → agentcore; requirements "
             "file → press Enter to accept requirements.txt; deployment type → "
             "Container (the only option without Docker installed); ECR repository "
             "→ press Enter to auto-create; OAuth authorizer → no; request header "
             "allowlist → no; memory setup → type ‘s’ to skip (this agent doesn't "
             "need conversational memory).",
        expected="“Configuration Success” panel showing your execution role ARN "
                 "and “authorization: IAM.”",
    )

    add_step(
        doc, "11.2", "Deploy",
        "Builds an ARM64 container in the cloud via AWS CodeBuild (no Docker "
        "needed on your laptop) and stands up the Runtime endpoint.",
        command="agentcore deploy",
        expected="Takes a few minutes; ends with a “Deployment Success” panel "
                 "showing the Agent ARN.",
        note="The toolkit prints a warning that it's deprecated in favor of a "
             "newer npm-based CLI (@aws/agentcore). It still works fully as of "
             "this build; check whether a newer CLI is expected by the time you "
             "read this.",
    )

    add_step(
        doc, "11.3", "Invoke the hosted agent",
        "Proves the agent genuinely runs hosted on AWS, not on your laptop.",
        command="python setup\\02_seed_files.py\nagentcore invoke \"{}\"",
        expected='{"status": "complete", "message": "Processed 5 file(s) over N '
                 'iteration(s). B1 is empty.", "files": [...]}',
        console="CloudWatch → Log groups → /aws/bedrock-agentcore/runtimes/"
                "<agent-id>-DEFAULT → confirm you see the same [iteration N] "
                "processing ... output, proving it ran inside the hosted container.",
        screenshot="The invoke response, and the matching CloudWatch log stream.",
    )

    # =================================================================
    doc.add_heading("Part 12 — Clean Up", level=1)

    add_step(
        doc, "12.1", "Tear down every AWS resource",
        "Deletes everything this project created, so nothing keeps costing "
        "money. Safe to re-run - every step tolerates the resource already "
        "being gone.",
        command="agentcore destroy --agent agentcore --force --delete-ecr-repo\n"
                "python cleanup\\teardown.py",
        expected="The first command removes the Runtime agent, its ECR images and "
                 "repository, the CodeBuild project, and its own execution role. "
                 "The second removes the Gateway and its targets, the three "
                 "Lambda functions, all three S3 buckets, the two remaining IAM "
                 "roles, and the Lambda CloudWatch log groups.",
    )

    doc.add_heading("You're done", level=1)
    doc.add_paragraph(
        "You've built, tested, deployed, and torn down a complete AWS AgentCore "
        "project: an autonomous agent that moves files between S3 buckets via "
        "Lambda, verifies them with SHA-256 hashing, proves it can detect "
        "corruption, reasons over its own CloudWatch logs with an LLM, and "
        "writes a full audit trail - all reproducible from scratch on any AWS "
        "account."
    )

    doc.save("docs/AgentCore_Project_Guide.docx")
    print("Saved docs/AgentCore_Project_Guide.docx")


if __name__ == "__main__":
    main()