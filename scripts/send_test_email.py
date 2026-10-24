"""Send a request email from the agent's Gmail account, to trigger the agent by hand.

By default it sends to the agent's own address with a test header, so `main check` will pick it up
(normal self-sent mail is ignored). Prints the subject so you can filter on it.

Usage:
    .venv/bin/python scripts/send_test_email.py "Hi Agent, Can you give me Other Documents files from M12205? Thanks!"
    .venv/bin/python -m uarb_agent.main check --subject-contains "<printed subject>"
"""

import argparse
import uuid
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from uarb_agent.config import settings
from uarb_agent.email_in import TEST_HEADER
from uarb_agent.email_out import send


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", help="the request to send, e.g. \"Exhibits for M12205\"")
    ap.add_argument("--to", default=settings.gmail_address, help="recipient (default: the agent's own address)")
    ap.add_argument("--subject", help="subject (default: 'UARB request <random tag>')")
    ap.add_argument("--name", default="Test User", help="display name on the From header (the agent greets this name)")
    args = ap.parse_args()

    msg = EmailMessage()
    msg["From"] = formataddr((args.name, settings.gmail_address))
    msg["To"] = args.to
    msg["Subject"] = args.subject or f"UARB request {uuid.uuid4().hex[:8]}"
    msg["Message-ID"] = make_msgid(domain=settings.gmail_address.split("@")[-1])
    msg[TEST_HEADER] = "1"
    msg.set_content(args.text)
    send(msg)
    print(f"Sent to {args.to} with subject: {msg['Subject']}")


if __name__ == "__main__":
    main()
