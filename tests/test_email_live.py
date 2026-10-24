"""Real Gmail round trip: the agent account emails itself a tagged request and replies to it.

Only messages carrying the unique test tag are read, changed or deleted.
"""

import time
import uuid
import zipfile
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import pytest

from uarb_agent import email_in, email_out
from uarb_agent.config import settings
from uarb_agent.models import AgentResult

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not (settings.gmail_address and settings.gmail_app_password), reason="Gmail credentials not set"),
]

ALL_MAIL = '"[Gmail]/All Mail"'


def _wait(fn, timeout=90, interval=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result := fn():
            return result
        time.sleep(interval)
    return None


def _find_reply(tag: str, in_reply_to: str) -> EmailMessage | None:
    with email_in.imap_connection() as conn:
        conn.select(ALL_MAIL, readonly=True)
        _, data = conn.uid("SEARCH", None, "SUBJECT", f'"Re: {tag}"')
        for uid in data[0].split():
            _, parts = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            msg = message_from_bytes(next(p[1] for p in parts if isinstance(p, tuple)), policy=policy.default)
            if msg["In-Reply-To"] == in_reply_to:
                return msg
    return None


def _delete_tagged(tag: str) -> None:
    with email_in.imap_connection() as conn:
        conn.select(ALL_MAIL)
        _, data = conn.uid("SEARCH", None, "SUBJECT", f'"{tag}"')
        for uid in data[0].split():
            conn.uid("STORE", uid, "+X-GM-LABELS", "\\Trash")


def test_email_round_trip(tmp_path):
    tag = f"uarb-agent selftest {uuid.uuid4().hex[:10]}"
    request = EmailMessage()
    request["From"] = formataddr(("Selftest Sender", settings.gmail_address))
    request["To"] = settings.gmail_address
    request["Subject"] = tag
    request["Message-ID"] = make_msgid(domain="selftest.local")
    request[email_in.TEST_HEADER] = "1"
    request.set_content("Hi Agent, Can you give me Key Documents files from M12205?\n\nOn Mon, someone wrote:\n> Exhibits for M12383")
    try:
        email_out.send(request)

        found = _wait(lambda: email_in.fetch_unread(subject_contains=tag))
        assert found, "test request never arrived in INBOX as unread"
        [req] = found
        assert req.subject == tag
        assert req.sender_email.lower() == settings.gmail_address.lower()
        assert req.sender_name == "Selftest"
        assert req.body == "Hi Agent, Can you give me Key Documents files from M12205?"
        assert req.message_id == request["Message-ID"]

        zip_path = tmp_path / "selftest.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("hello.txt", "hello")
        sent_id = email_out.send_reply(req, AgentResult(reply_body="Hi Selftest,\n\nHere you go.", zip_path=zip_path, subject_suffix="[M12205 – Key Documents]"))
        email_in.mark_processed(req.uid)
        assert email_in.fetch_unread(subject_contains=tag) == [] or all(r.uid != req.uid for r in email_in.fetch_unread(subject_contains=tag))

        reply = _wait(lambda: _find_reply(tag, request["Message-ID"]))
        assert reply, "reply never showed up"
        assert reply["Message-ID"] == sent_id
        assert reply["Subject"] == f"Re: {tag} [M12205 – Key Documents]"
        assert request["Message-ID"] in reply["References"]
        [att] = list(reply.iter_attachments())
        assert att.get_filename() == "selftest.zip"
        assert att.get_content_type() == "application/zip"
        # Our own reply must never be treated as a new request.
        assert email_in.skip_reason(reply) == "agent's own reply"
    finally:
        _delete_tagged(tag)
