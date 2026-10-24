import os
import threading

import pytest

from uarb_agent import agent, email_in, email_out, runner
from uarb_agent.email_in import EmailAuthError
from uarb_agent.models import AgentResult, IncomingRequest


class FakeStop(threading.Event):
    """A stop event whose wait() records delays instead of sleeping, and stops after N waits."""

    def __init__(self, stop_after_waits: int = 10):
        super().__init__()
        self.waits: list[float] = []
        self.stop_after = stop_after_waits

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if len(self.waits) >= self.stop_after:
            self.set()
        return self.is_set()


def _process(*outcomes):
    """A fake process_inbox_once that returns/raises each outcome in turn, then returns 0."""
    calls = iter(outcomes)

    def process(should_stop):
        outcome = next(calls, 0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return process


# --- loop -------------------------------------------------------------------------


def test_backs_off_on_connection_errors_then_recovers():
    stop = FakeStop(stop_after_waits=5)
    process = _process(ConnectionResetError("reset"), TimeoutError(), ConnectionRefusedError(), 2)
    code = runner.run_forever(process, stop=stop, interval=10, max_backoff=25)
    assert code == 0
    # 10, 20, then capped at 25; after success, back to the normal 10s interval.
    assert stop.waits == [10, 20, 25, 10, 10]


def test_auth_error_exits_nonzero_without_retrying():
    stop = FakeStop()
    code = runner.run_forever(_process(EmailAuthError("bad password")), stop=stop, interval=10)
    assert code == 1
    assert stop.waits == []


def test_unexpected_error_keeps_polling_at_normal_interval():
    stop = FakeStop(stop_after_waits=2)
    code = runner.run_forever(_process(RuntimeError("bug"), 1), stop=stop, interval=10)
    assert code == 0
    assert stop.waits == [10, 10]


def test_stop_set_during_pass_exits_after_it():
    stop = FakeStop()
    calls = []

    def process(should_stop):
        calls.append(1)
        stop.set()  # e.g. Ctrl+C arrives while an email is being handled
        return 1

    assert runner.run_forever(process, stop=stop, interval=10) == 0
    assert len(calls) == 1


def test_process_inbox_once_stops_between_emails(monkeypatch):
    reqs = [IncomingRequest(sender_email="a@x.com", subject=f"s{i}", body="b", uid=str(i)) for i in range(3)]
    handled = []
    monkeypatch.setattr(email_in, "fetch_unread", lambda subject_contains=None: reqs)
    monkeypatch.setattr(agent, "process_request", lambda req: handled.append(req.uid))
    assert agent.process_inbox_once(should_stop=lambda: len(handled) == 1) == 1
    assert handled == ["0"]


def test_process_inbox_once_raises_connection_errors(monkeypatch):
    def down(subject_contains=None):
        raise ConnectionResetError("imap down")

    monkeypatch.setattr(email_in, "fetch_unread", down)
    with pytest.raises(ConnectionResetError):
        agent.process_inbox_once()


# --- sending + cleanup ------------------------------------------------------------


def _job_files(tmp_path):
    files_dir = tmp_path / "M12205" / "key-documents"
    files_dir.mkdir(parents=True)
    (files_dir / "1.pdf").write_bytes(b"%PDF")
    zip_path = tmp_path / "M12205_key-documents.zip"
    zip_path.write_bytes(b"PK")
    return AgentResult(reply_body="hi", zip_path=zip_path, files_dir=files_dir, subject_suffix="[M12205 – Key Documents]")


def _req():
    return IncomingRequest(sender_email="jane@x.com", subject="docs", body="b", uid="5")


def test_sent_reply_deletes_its_downloads(monkeypatch, tmp_path):
    result = _job_files(tmp_path)
    marked = []
    monkeypatch.setattr(agent, "handle_request", lambda text, name: [result])
    monkeypatch.setattr(email_out, "send_reply", lambda req, r: "<id>")
    monkeypatch.setattr(email_in, "mark_processed", lambda uid: marked.append(uid))
    agent.process_request(_req())
    assert not result.zip_path.exists()
    assert not result.files_dir.exists()
    assert not (tmp_path / "M12205").exists()
    assert marked == ["5"]


def test_failed_send_keeps_downloads(monkeypatch, tmp_path):
    result = _job_files(tmp_path)

    def rejected(req, r):
        raise OSError("message rejected")

    monkeypatch.setattr(agent, "handle_request", lambda text, name: [result])
    monkeypatch.setattr(email_out, "send_reply", rejected)
    monkeypatch.setattr(email_in, "mark_processed", lambda uid: None)
    agent.process_request(_req())
    assert result.zip_path.exists()
    assert (result.files_dir / "1.pdf").exists()


def test_network_down_leaves_email_unread_and_raises(monkeypatch, tmp_path):
    result = _job_files(tmp_path)
    marked = []

    def down(req, r):
        raise ConnectionResetError("smtp down")

    monkeypatch.setattr(agent, "handle_request", lambda text, name: [result])
    monkeypatch.setattr(email_out, "send_reply", down)
    monkeypatch.setattr(email_in, "mark_processed", lambda uid: marked.append(uid))
    with pytest.raises(ConnectionResetError):
        agent.process_request(_req())
    assert marked == []  # retried on a later poll
    assert result.zip_path.exists()


def test_auth_error_while_sending_propagates(monkeypatch, tmp_path):
    def bad_login(req, r):
        raise EmailAuthError("bad password")

    monkeypatch.setattr(agent, "handle_request", lambda text, name: [AgentResult(reply_body="hi")])
    monkeypatch.setattr(email_out, "send_reply", bad_login)
    monkeypatch.setattr(email_in, "mark_processed", lambda uid: None)
    with pytest.raises(EmailAuthError):
        agent.process_request(_req())


# --- pruning ----------------------------------------------------------------------


def _age(path, now, hours):
    t = now - hours * 3600
    os.utime(path, (t, t))


def test_prune_removes_old_downloads_and_old_screenshots(tmp_path):
    now = 1_000_000_000.0
    old_zip = tmp_path / "M1_x.zip"; old_zip.write_bytes(b"PK"); _age(old_zip, now, 30)
    new_zip = tmp_path / "M2_x.zip"; new_zip.write_bytes(b"PK"); _age(new_zip, now, 2)
    old_dir = tmp_path / "M1" / "x"; old_dir.mkdir(parents=True)
    (old_dir / "a.pdf").write_bytes(b"%PDF"); _age(old_dir / "a.pdf", now, 30)
    mixed_dir = tmp_path / "M3" / "x"; mixed_dir.mkdir(parents=True)
    (mixed_dir / "old.pdf").write_bytes(b"%PDF"); _age(mixed_dir / "old.pdf", now, 30)
    (mixed_dir / "new.pdf").write_bytes(b"%PDF"); _age(mixed_dir / "new.pdf", now, 1)
    for p in (old_dir, tmp_path / "M1", mixed_dir, tmp_path / "M3"):
        _age(p, now, 30)
    errors = tmp_path / "errors"; errors.mkdir()
    old_shot = errors / "old.png"; old_shot.write_bytes(b"png"); _age(old_shot, now, 24 * 8)
    new_shot = errors / "new.png"; new_shot.write_bytes(b"png"); _age(new_shot, now, 30)

    removed = runner.prune_downloads(tmp_path, max_age_hours=24, error_max_age_days=7, now=now)

    assert removed == 3
    assert not old_zip.exists() and not (tmp_path / "M1").exists() and not old_shot.exists()
    assert new_zip.exists() and (mixed_dir / "new.pdf").exists() and new_shot.exists()


def test_prune_missing_dir_is_noop(tmp_path):
    assert runner.prune_downloads(tmp_path / "nope") == 0
