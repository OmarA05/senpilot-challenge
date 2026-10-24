# UARB Document Agent

An email agent that fetches regulatory filings from the [Nova Scotia UARB public documents database](https://uarb.novascotia.ca/fmi/webd/UARB15).

Email it a matter number and a document type:

> Hi Agent, Can you give me Other Documents files from M12205? Thanks!

It looks the matter up, downloads up to 10 documents from the requested tab, ZIPs them, and replies in the same thread:

> Hi there,
>
> M12205 is about the Halifax Regional Water Commission - Windsor Street Exchange Redevelopment Project - $69,275,000. It relates to Capital Expenditure Approvals within the Water category. The matter is Closed, with the outcome "Directions given". It had an initial filing on April 7, 2025 and a final filing on October 23, 2025. I found 13 Exhibits, 6 Key Documents, 43 Other Documents, and no Transcripts or Recordings. I downloaded 10 out of the 43 Other Documents and am attaching them as a ZIP here.
>
> Best,
> UARB Agent
>
> 📎 `M12205_other-documents.zip`

Supported document types: **Exhibits, Key Documents, Other Documents, Transcripts, Recordings**.

---

## How it works

```
 Gmail inbox ──IMAP──▶ email_in ──▶ parser ──▶ for each (matter, type) job:
                                     (Claude)      scraper  ─▶ packager ─▶ composer ─▶ email_out ──SMTP──▶ reply in thread
                                                  (Playwright)   (ZIP)      (Claude)
```

1. **`email_in`** reads unread mail and extracts the sender's name and the plain-text body, with quoted history and signatures stripped. It skips auto-replies, bounces and the agent's own mail.
2. **`parser`** turns free text into a list of jobs, each a pair like `(M12205, Other Documents)`. Claude does the extraction with structured output, and code then validates it.
3. **`scraper`** drives the site with headless Chromium. It searches the matter, reads the header metadata and all five tab counts, opens the requested tab, and downloads documents through the site's "GO GET IT" dialog.
4. **`packager`** ZIPs the files, staying under the email attachment limit.
5. **`composer`** builds the facts in code; Claude then words the reply in the example's style, and code checks the result.
6. **`email_out`** sends a threaded reply (`In-Reply-To`/`References`) with the ZIP attached.

`runner` polls the inbox on an interval, retries network failures with backoff, and shuts down gracefully.

## Design decisions

**Claude handles the language; deterministic code handles the browser.**
The site is a FileMaker WebDirect app: it renders slowly, its element IDs change on every load, and its widgets are unusual. A scripted Playwright flow is fast, cheap and testable there, where an LLM clicking around would be slow and flaky. Claude is used where language is the hard part: understanding the request and writing a natural reply.

**Claude's output is never trusted blindly.**
- **Parser:** a matter number must match `M\d{5}` *and* appear in the original email, so a hallucinated matter is dropped. Document types must be one of the five, duplicates are removed, and requests are capped at 5 jobs per email.
- **Composer:** Claude only words facts computed in code. If the draft is missing any count, mentions a different matter, claims an attachment that doesn't exist, or uses markdown, it is discarded and a template reply is sent instead.
- **Fallbacks:** if the Claude API is unavailable, a regex parser and the template composer take over, so the agent keeps working, just with less flexible understanding.

**One email, several requests.**
"Exhibits for M12205 and M12383" or "transcripts and recordings for M12205" expand into separate jobs. Each job gets its own reply and ZIP in the same thread, which keeps each attachment small and means one failure doesn't block the others. A matter is opened only once per email, even when several types are requested from it.

**Edge cases handled**

| Situation | Behaviour |
|---|---|
| No matter number or document type | Reply asking for clarification |
| Matter doesn't exist | Reply saying it wasn't found (the site shows no error, so the scraper detects that the matter page never loads) |
| Requested tab has 0 documents | Summary reply, no attachment |
| Fewer than 10 documents | Downloads all of them; the reply says "X out of Y" |
| ZIP would exceed ~20 MB (some exhibits are 20–50 MB each) | Large files are left out and listed in the reply by number, title and size |
| A single download fails | Skipped and listed; the rest are still sent |
| Website or Gmail is down | The job gets an error reply; connection errors are retried with exponential backoff |
| Same email seen twice | Emails are marked read and labelled `uarb-agent/processed` once handled |
| Ctrl+C / SIGTERM mid-job | The current email finishes, then the agent exits |

## Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/playwright install chromium
cp .env.example .env    # then fill it in
```

**Gmail:** use a dedicated account for the agent. Turn on 2-Step Verification, then create an [App Password](https://myaccount.google.com/apppasswords) and put it in `GMAIL_APP_PASSWORD`. Gmail rejects the normal account password for IMAP/SMTP.

**Anthropic:** put your API key in `ANTHROPIC_API_KEY`.

All settings are listed in [`.env.example`](.env.example) and [`config.py`](src/uarb_agent/config.py).

## Usage

```bash
# Run the agent: poll the inbox every 30s and reply (Ctrl+C to stop)
.venv/bin/python -m uarb_agent.main run
.venv/bin/python -m uarb_agent.main run --interval 10

# Process the inbox once
.venv/bin/python -m uarb_agent.main check [--subject-contains TEXT]

# Try a request locally without email: prints the replies and builds the ZIPs
.venv/bin/python -m uarb_agent.main once "Hi Agent, Can you give me Other Documents files from M12205? Thanks!"
```

Then email the agent's address from any account.

**Individual pieces**, handy for debugging:

```bash
.venv/bin/python scripts/try_scraper.py M12205 "Other Documents" --limit 3 [--headed]   # --headed shows the browser
.venv/bin/python scripts/try_parser.py "exhibits for M12205, and for M12383 just the key stuff" [--regex]
.venv/bin/python scripts/try_composer.py [--template]
.venv/bin/python scripts/send_test_email.py "Key Documents for M12205"                  # agent emails itself
```

### Docker

```bash
docker build -t uarb-agent .
docker run --rm --env-file .env uarb-agent                    # runs the polling agent
docker run --rm --env-file .env uarb-agent once "Transcripts for M12205"
```

The image is based on Microsoft's Playwright image, which includes Chromium. Secrets are passed at runtime and never built into the image. The agent only makes outbound connections (Gmail, the UARB site, Anthropic), so it can run on any host that supports a long-running container, with no public port.

## Deployment

The agent runs on an **AWS Lightsail** instance (Amazon Linux 2023, 2 GB RAM, Montreal region) as a single Docker container. The container is built from this repo's Dockerfile, gets its secrets from an env file on the instance, and runs with a restart-always policy, so it recovers from crashes and reboots. A swap file gives Chromium some headroom.

The agent only makes outbound connections (Gmail, the UARB site and Anthropic), so no ports, domain or load balancer are exposed. To update it, pull the latest commit on the instance, rebuild the image and restart the container. Any host that can run one long-lived container with about 2 GB of memory would work the same way, such as EC2, ECS Fargate, Railway or Fly.io.

## Tests

```bash
.venv/bin/pytest -q          # ~100 offline unit tests (fake Claude client, no network), under a second
.venv/bin/pytest -m live -q  # against the real UARB site, Claude API and Gmail (a few minutes)
```

The live tests assert on the shape of results, not exact counts, because the site's document counts change over time. The email round-trip test only touches messages carrying its own unique tag and deletes them afterwards.

## Project structure

```
src/uarb_agent/
  main.py       CLI: run / check / once
  runner.py     poll loop, backoff, signal handling, download pruning
  agent.py      orchestration: email → jobs → replies
  email_in.py   IMAP: fetch unread, extract text, skip auto-replies, mark processed
  email_out.py  SMTP: threaded reply with ZIP attachment
  parser.py     request text → jobs (Claude + validation, regex fallback)
  scraper.py    Playwright automation of the UARB FileMaker WebDirect site
  packager.py   size-capped ZIP
  composer.py   facts → reply text (Claude + guardrail, template fallback)
  llm.py        shared Anthropic client
  models.py     DocType, Job, MatterInfo, Document, IncomingRequest, AgentResult
  config.py     settings from environment / .env
scripts/        small runnable tools for trying each piece
tests/          unit tests + @pytest.mark.live integration tests
```

## Limitations and next steps

- **Polling, not push.** Gmail IMAP logins take ~10–15s, so a reply typically arrives within about a minute. IMAP IDLE or the Gmail API with push notifications would be faster.
- **"First 10" means the 10 most recent.** The site lists documents newest first. Users can't yet ask for specific documents or date ranges.
- **Large exhibits are listed rather than attached.** They could be uploaded to cloud storage and sent as links instead.
- **Partial delivery on network loss.** If the connection drops after some of an email's replies are sent, the email is still marked processed. This avoids duplicate replies at the cost of possibly missing one.
- **Tied to the current site layout.** The scraper relies on visible text and layout because the site has no stable IDs; a redesign would need selector updates. The live tests catch this quickly.
- **Toward the searchable database.** Matter metadata and downloaded documents could be stored (for example in SQLite or Postgres, plus a vector index over the document text) to build the research corpus the Regulatory Agent needs.
