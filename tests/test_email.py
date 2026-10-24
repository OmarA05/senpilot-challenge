from email import message_from_bytes, policy
from email.message import EmailMessage

import pytest

from uarb_agent import agent, email_in, email_out
from uarb_agent.config import settings
from uarb_agent.models import AgentResult, IncomingRequest


def _msg(body: str = "Exhibits for M12205", subtype: str = "plain", **headers) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = headers.pop("From", '"Jane Doe" <jane@example.com>')
    msg["Subject"] = headers.pop("Subject", "Docs please")
    msg["Message-ID"] = headers.pop("Message_ID", "<abc@example.com>")
    for k, v in headers.items():
        msg[k.replace("_", "-")] = v
    msg.set_content(body, subtype=subtype)
    return msg


def _roundtrip(msg: EmailMessage) -> EmailMessage:
    return message_from_bytes(msg.as_bytes(), policy=policy.default)


# --- body extraction ----------------------------------------------------------


def test_plain_body():
    assert email_in.extract_body(_roundtrip(_msg("Exhibits for M12205"))).strip() == "Exhibits for M12205"


def test_multipart_prefers_plain():
    msg = _msg("plain version M12205")
    msg.add_alternative("<p>html version M12205</p>", subtype="html")
    assert email_in.extract_body(_roundtrip(msg)).strip() == "plain version M12205"


def test_html_only_converted_and_gmail_quote_dropped():
    html = (
        "<div dir='ltr'>Hi Agent,<br>Can I get <b>Key Documents</b> for M12205?</div>"
        "<div class='gmail_quote'><div>On Mon, someone wrote:</div><blockquote>Exhibits for M12383</blockquote></div>"
    )
    text = email_in.extract_body(_roundtrip(_msg(html, subtype="html")))
    assert "Key Documents for M12205" in text
    assert "M12383" not in text


# --- quote stripping ------------------------------------------------------------


@pytest.mark.parametrize("tail", [
    "\n\nOn Mon, Oct 20, 2026 at 9:00 AM UARB Agent <agent@gmail.com> wrote:\n> Exhibits for M12383",
    "\n\nOn Mon, Oct 20, 2026 at 9:00 AM UARB Agent <\nagent@gmail.com> wrote:\n> old",
    "\n\n-----Original Message-----\nFrom: someone\nExhibits for M12383",
    "\n\n> quoted line M12383\n> another",
    "\n\n-- \nJane Doe\nAcme Utility M12383",
])
def test_strip_quoted(tail):
    assert email_in.strip_quoted("Key Documents for M12205 please." + tail) == "Key Documents for M12205 please."


def test_strip_quoted_keeps_multiline_request():
    text = "Hi Agent,\n\nCan you send Exhibits for M12205?\nThanks!\nJane"
    assert email_in.strip_quoted(text) == text


# --- headers / skipping -----------------------------------------------------------


def test_parse_message_fields():
    msg = _msg("Exhibits for M12205\n\nOn Mon, X wrote:\n> old", References="<root@example.com>")
    req = email_in.parse_message(_roundtrip(msg), uid="42")
    assert req.sender_email == "jane@example.com"
    assert req.sender_name == "Jane"
    assert req.subject == "Docs please"
    assert req.body == "Exhibits for M12205"
    assert req.message_id == "<abc@example.com>"
    assert req.references == "<root@example.com>"
    assert req.uid == "42"


@pytest.mark.parametrize("from_, name", [
    ('"Doe, Jane" <j@x.com>', "Jane"),
    ("j@x.com", None),
    ("Omar <o@x.com>", "Omar"),
])
def test_sender_name(from_, name):
    assert email_in.parse_message(_roundtrip(_msg(From=from_))).sender_name == name


@pytest.mark.parametrize("headers, skipped", [
    ({}, False),
    ({"Auto_Submitted": "auto-replied"}, True),
    ({"Auto_Submitted": "no"}, False),
    ({"Precedence": "bulk"}, True),
    ({"From": "MAILER-DAEMON@googlemail.com"}, True),
    ({"X_UARB_Agent_Reply": "1"}, True),
])
def test_skip_reason(headers, skipped):
    assert (email_in.skip_reason(_roundtrip(_msg(**headers))) is not None) == skipped


def test_self_sent_skipped_unless_test(monkeypatch):
    monkeypatch.setattr(settings, "gmail_address", "agent@gmail.com")
    assert email_in.skip_reason(_roundtrip(_msg(From="agent@gmail.com"))) is not None
    assert email_in.skip_reason(_roundtrip(_msg(From="agent@gmail.com", X_UARB_Agent_Test="1"))) is None


# --- reply construction ---------------------------------------------------------


def _req(**kw) -> IncomingRequest:
    return IncomingRequest(**{
        "sender_email": "jane@example.com", "sender_name": "Jane", "subject": "Docs please", "body": "x",
        "message_id": "<abc@example.com>", "references": "<root@example.com>", **kw,
    })


@pytest.mark.parametrize("subject, suffix, expected", [
    ("Docs please", "[M12205 – Exhibits]", "Re: Docs please [M12205 – Exhibits]"),
    ("RE: Docs please", None, "RE: Docs please"),
    ("", None, "Re: Your UARB request"),
])
def test_reply_subject(subject, suffix, expected):
    assert email_out.reply_subject(subject, suffix) == expected


def test_build_reply_threads_and_attaches(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "gmail_address", "agent@gmail.com")
    zip_path = tmp_path / "M12205_exhibits.zip"
    zip_path.write_bytes(b"PK\x03\x04 fake zip")
    msg = email_out.build_reply(_req(), AgentResult(reply_body="Hi Jane", zip_path=zip_path, subject_suffix="[M12205 – Exhibits]"))
    assert msg["To"] == "jane@example.com"
    assert msg["Subject"] == "Re: Docs please [M12205 – Exhibits]"
    assert msg["In-Reply-To"] == "<abc@example.com>"
    assert msg["References"] == "<root@example.com> <abc@example.com>"
    assert msg[email_in.REPLY_HEADER] == "1"
    assert msg.get_body(("plain",)).get_content().strip() == "Hi Jane"
    [att] = list(msg.iter_attachments())
    assert att.get_filename() == "M12205_exhibits.zip"
    assert att.get_content_type() == "application/zip"
    assert att.get_content() == b"PK\x03\x04 fake zip"


def test_build_reply_without_zip_or_message_id():
    msg = email_out.build_reply(_req(message_id=None, references=None), AgentResult(reply_body="Hi"))
    assert msg["In-Reply-To"] is None
    assert list(msg.iter_attachments()) == []


def test_oversized_zip_not_attached(tmp_path, monkeypatch):
    monkeypatch.setattr(email_out, "MAX_SEND_BYTES", 10)
    zip_path = tmp_path / "big.zip"
    zip_path.write_bytes(b"x" * 100)
    msg = email_out.build_reply(_req(), AgentResult(reply_body="Hi", zip_path=zip_path))
    assert list(msg.iter_attachments()) == []
    assert "too large to send" in msg.get_body(("plain",)).get_content()


# --- inbox processing (no network) --------------------------------------------------


def test_request_text_includes_subject_without_our_suffix():
    req = _req(subject="Re: Docs please [M12383 – Exhibits]", body="Key Documents for M12205")
    assert agent.request_text(req) == "Subject: Re: Docs please\n\nKey Documents for M12205"


def test_process_request_sends_each_reply_and_marks_processed(monkeypatch):
    sent, marked = [], []
    results = [AgentResult(reply_body="a", subject_suffix="[M12205 – Exhibits]"), AgentResult(reply_body="b")]
    monkeypatch.setattr(agent, "handle_request", lambda text, name: results)
    monkeypatch.setattr(email_out, "send_reply", lambda req, r: sent.append(r.reply_body))
    monkeypatch.setattr(email_in, "mark_processed", lambda uid: marked.append(uid))
    agent.process_request(_req(uid="7"))
    assert sent == ["a", "b"]
    assert marked == ["7"]


def test_process_request_failure_sends_error_and_still_marks(monkeypatch):
    sent, marked = [], []

    def boom(text, name):
        raise RuntimeError("browser exploded")

    monkeypatch.setattr(agent, "handle_request", boom)
    monkeypatch.setattr(email_out, "send_reply", lambda req, r: sent.append(r.reply_body))
    monkeypatch.setattr(email_in, "mark_processed", lambda uid: marked.append(uid))
    agent.process_request(_req(uid="8"))
    assert len(sent) == 1 and "Something went wrong" in sent[0] and sent[0].startswith("Hi Jane")
    assert marked == ["8"]


def test_process_request_send_failure_does_not_raise(monkeypatch):
    marked = []

    def fail_send(req, r):
        raise OSError("smtp down")

    monkeypatch.setattr(agent, "handle_request", lambda text, name: [AgentResult(reply_body="a")])
    monkeypatch.setattr(email_out, "send_reply", fail_send)
    monkeypatch.setattr(email_in, "mark_processed", lambda uid: marked.append(uid))
    agent.process_request(_req(uid="9"))
    assert marked == ["9"]


def test_trash_matching_refuses_empty_filter():
    with pytest.raises(ValueError):
        email_in.trash_matching("  ")
