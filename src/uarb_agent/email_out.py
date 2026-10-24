"""Sending threaded replies (with the ZIP attached) over Gmail SMTP."""

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .config import settings
from .email_in import AUTH_HELP, NETWORK_TIMEOUT, REPLY_HEADER, EmailAuthError
from .models import AgentResult, IncomingRequest

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
MAX_SEND_BYTES = 24 * 1024 * 1024  # Gmail rejects > 25 MB including encoding overhead; last-resort cap
AGENT_NAME = "UARB Agent"


def reply_subject(original_subject: str, suffix: str | None = None) -> str:
    subject = original_subject.strip() or "Your UARB request"
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    return f"{subject} {suffix}" if suffix else subject


def build_reply(original: IncomingRequest, result: AgentResult) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((AGENT_NAME, settings.gmail_address))
    msg["To"] = original.sender_email
    msg["Subject"] = reply_subject(original.subject, result.subject_suffix)
    msg["Message-ID"] = make_msgid(domain=settings.gmail_address.split("@")[-1] or None)
    if original.message_id:
        msg["In-Reply-To"] = original.message_id
        msg["References"] = " ".join(filter(None, [original.references, original.message_id]))
    msg[REPLY_HEADER] = "1"
    msg["Auto-Submitted"] = "auto-replied"

    body = result.reply_body
    zip_path = result.zip_path
    if zip_path and zip_path.exists() and zip_path.stat().st_size > MAX_SEND_BYTES:
        log.warning("%s is %d bytes, over the send limit; sending without it", zip_path, zip_path.stat().st_size)
        body += "\n\n(Note: the ZIP turned out too large to send by email, so it isn't attached. The documents are available on the UARB site.)"
        zip_path = None
    msg.set_content(body)
    if zip_path and zip_path.exists():
        msg.add_attachment(zip_path.read_bytes(), maintype="application", subtype="zip", filename=zip_path.name)
    return msg


def send(msg: EmailMessage) -> str:
    with smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=NETWORK_TIMEOUT) as smtp:
        try:
            smtp.login(settings.gmail_address, settings.gmail_app_password)
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPServerDisconnected):
            raise EmailAuthError(AUTH_HELP.format(addr=settings.gmail_address)) from None
        smtp.send_message(msg)
    log.info("Sent %r to %s", msg["Subject"], msg["To"])
    return msg["Message-ID"]


def send_reply(original: IncomingRequest, result: AgentResult) -> str:
    """Send one reply in the original's thread; returns the sent Message-ID."""
    return send(build_reply(original, result))
