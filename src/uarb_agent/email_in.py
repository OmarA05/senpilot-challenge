"""Reading request emails from the agent's Gmail inbox over IMAP."""

import imaplib
import logging
import re
from contextlib import contextmanager
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parseaddr
from html.parser import HTMLParser
from typing import Iterator

from .config import settings
from .models import IncomingRequest

log = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
NETWORK_TIMEOUT = 120  # seconds; Gmail logins alone can take ~15s, but a hung socket must not freeze the loop
PROCESSED_LABEL = "uarb-agent/processed"
REPLY_HEADER = "X-UARB-Agent-Reply"  # set on every reply we send, so we never answer ourselves
TEST_HEADER = "X-UARB-Agent-Test"  # set by scripts/send_test_email.py on self-sent test requests


class EmailAuthError(Exception):
    """Gmail refused the credentials. Raised without the original error so the password never appears in tracebacks."""


AUTH_HELP = (
    "Gmail rejected the login for {addr}. GMAIL_APP_PASSWORD must be a 16-letter App Password "
    "(Google Account → Security → 2-Step Verification → App passwords), not the account password."
)


@contextmanager
def imap_connection() -> Iterator[imaplib.IMAP4_SSL]:
    conn = imaplib.IMAP4_SSL(IMAP_HOST, timeout=NETWORK_TIMEOUT)
    try:
        try:
            conn.login(settings.gmail_address, settings.gmail_app_password)
        except imaplib.IMAP4.error:
            raise EmailAuthError(AUTH_HELP.format(addr=settings.gmail_address)) from None
        conn.select("INBOX")
        yield conn
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def fetch_unread(limit: int | None = None, subject_contains: str | None = None) -> list[IncomingRequest]:
    """Unread INBOX messages as requests. Fetches with BODY.PEEK so nothing is marked seen here."""
    with imap_connection() as conn:
        criteria = ["UNSEEN"]
        if subject_contains:
            criteria += ["SUBJECT", _quote(subject_contains)]
        _, data = conn.uid("SEARCH", None, *criteria)
        uids = data[0].split()
        if limit:
            uids = uids[:limit]
        requests = []
        for uid in uids:
            _, parts = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
            if raw is None:
                continue
            msg = message_from_bytes(raw, policy=policy.default)
            if reason := skip_reason(msg):
                log.info("Skipping message %s (%s): %s", uid.decode(), reason, msg.get("Subject", ""))
                mark_processed(uid.decode(), conn)
                continue
            requests.append(parse_message(msg, uid.decode()))
        log.log(logging.INFO if requests else logging.DEBUG, "Fetched %d unread request(s)", len(requests))
        return requests


def count_unread() -> int:
    with imap_connection() as conn:
        _, data = conn.uid("SEARCH", None, "UNSEEN")
        return len(data[0].split())


def mark_processed(uid: str, conn: imaplib.IMAP4_SSL | None = None) -> None:
    """Mark seen and add a Gmail label so processed requests are easy to find."""
    if conn is None:
        with imap_connection() as c:
            return mark_processed(uid, c)
    conn.uid("STORE", uid, "+FLAGS", r"(\Seen)")
    try:
        conn.uid("STORE", uid, "+X-GM-LABELS", _quote(PROCESSED_LABEL))
    except imaplib.IMAP4.error as e:
        log.debug("Couldn't label %s: %s", uid, e)


def trash_matching(subject_contains: str) -> int:
    """Move every message (any folder) whose subject contains the text to Trash. Returns how many.

    Used to clean up self-test emails; refuses an empty filter so it can never sweep the whole mailbox.
    """
    if not subject_contains.strip():
        raise ValueError("subject_contains must not be empty")
    with imap_connection() as conn:
        _, boxes = conn.list()
        listing = [b.decode() for b in boxes]
        all_mail = next(_mailbox_name(b) for b in listing if "\\All" in b)
        trash = next(_mailbox_name(b) for b in listing if "\\Trash" in b)
        conn.select(all_mail)
        _, data = conn.uid("SEARCH", None, "SUBJECT", _quote(subject_contains))
        uids = data[0].split()
        if uids:
            conn.uid("MOVE", b",".join(uids).decode(), trash)
        return len(uids)


def _mailbox_name(list_line: str) -> str:
    # e.g. '(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"' → '"[Gmail]/Trash"'
    return list_line.rsplit(' "/" ', 1)[1]


# --- parsing ----------------------------------------------------------------


def parse_message(msg: EmailMessage, uid: str | None = None) -> IncomingRequest:
    name, addr = parseaddr(str(msg.get("From", "")))
    return IncomingRequest(
        sender_email=addr,
        sender_name=_first_name(name) if name else None,
        subject=str(msg.get("Subject", "")).strip(),
        body=strip_quoted(extract_body(msg)),
        message_id=str(msg.get("Message-ID", "")).strip() or None,
        references=str(msg.get("References", "")).strip() or None,
        uid=uid,
    )


def skip_reason(msg: EmailMessage) -> str | None:
    """Why a message shouldn't get a reply: our own mail, auto-replies, bounces, bulk mail."""
    _, addr = parseaddr(str(msg.get("From", "")))
    addr = addr.lower()
    if msg.get(REPLY_HEADER):
        return "agent's own reply"
    # Self-sent mail is ignored, except test requests from scripts/send_test_email.py.
    if addr == settings.gmail_address.lower() and msg.get(TEST_HEADER) is None:
        return "sent by the agent itself"
    if addr.startswith(("mailer-daemon", "postmaster")) or "noreply" in addr.replace("-", ""):
        return "automated sender"
    if str(msg.get("Auto-Submitted", "no")).lower() != "no":
        return "auto-submitted"
    if str(msg.get("Precedence", "")).lower() in {"bulk", "auto_reply", "list", "junk"}:
        return "bulk/auto-reply"
    return None


def extract_body(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain",))
    if part is not None:
        return part.get_content()
    part = msg.get_body(preferencelist=("html",))
    if part is not None:
        return html_to_text(part.get_content())
    return ""


QUOTE_HEADER_RE = re.compile(
    r"^(On .{0,200}wrote:\s*$|-{2,}\s*Original Message\s*-{2,}|-{5,}\s*Forwarded message|From: .+|_{10,})",
    re.IGNORECASE,
)


def strip_quoted(text: str) -> str:
    """Drop quoted reply history and the signature, keeping only what the sender just wrote."""
    lines = text.replace("\r\n", "\n").split("\n")
    kept = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "--" or line == "-- ":
            break  # signature delimiter
        if QUOTE_HEADER_RE.match(stripped):
            break
        # Gmail sometimes wraps "On ... wrote:" over two lines.
        if stripped.startswith("On ") and i + 1 < len(lines) and lines[i + 1].strip().endswith("wrote:"):
            break
        if stripped.startswith(">"):
            continue
        kept.append(line.rstrip())
    return "\n".join(kept).strip()


class _HtmlText(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "blockquote"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0  # depth inside <style>/<script>/quoted blocks

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class") or ""
        if self._skip or tag in {"style", "script", "blockquote"} or "gmail_quote" in cls:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag == "br" and not self._skip:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _HtmlText()
    p.feed(html)
    text = re.sub(r"[ \t\xa0]+", " ", "".join(p.parts))
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _first_name(display_name: str) -> str:
    name = display_name.strip().strip('"')
    if "," in name:  # "Last, First"
        name = name.split(",", 1)[1].strip()
    return name.split()[0] if name.split() else name


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
