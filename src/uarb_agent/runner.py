"""The long-running inbox loop: poll, back off on network trouble, stop cleanly, prune old downloads."""

import logging
import shutil
import signal
import threading
import time
from pathlib import Path
from typing import Callable

from .agent import CONNECTION_ERRORS, process_inbox_once
from .config import settings
from .email_in import EmailAuthError

log = logging.getLogger(__name__)


class Backoff:
    """Exponential delay after consecutive failures: base, 2x, 4x, ... capped."""

    def __init__(self, base: float, cap: float):
        self.base, self.cap, self.failures = base, cap, 0

    def next_delay(self) -> float:
        delay = min(self.base * 2 ** self.failures, self.cap)
        self.failures += 1
        return delay

    def reset(self) -> None:
        self.failures = 0


def run_forever(
    process: Callable[..., int] = process_inbox_once,
    stop: threading.Event | None = None,
    interval: float | None = None,
    max_backoff: float | None = None,
) -> int:
    """Poll until stop is set. Returns an exit code: 0 after a clean stop, 1 if Gmail rejects the login.

    Waiting uses stop.wait(), so a stop request interrupts the sleep immediately.
    """
    stop = stop or threading.Event()
    interval = settings.poll_interval_seconds if interval is None else interval
    backoff = Backoff(base=interval, cap=settings.max_backoff_seconds if max_backoff is None else max_backoff)
    while not stop.is_set():
        try:
            handled = process(should_stop=stop.is_set)
        except EmailAuthError as e:
            log.error("%s Stopping.", e)
            return 1
        except CONNECTION_ERRORS as e:
            delay = backoff.next_delay()
            log.warning("Network problem (%s: %s); retrying in %.0fs", type(e).__name__, e, delay)
            stop.wait(delay)
            continue
        except Exception:
            # Per-email failures are handled inside process(); this is a bug or something unexpected. Keep going.
            log.exception("Inbox pass failed")
            handled = 0
        if backoff.failures:
            log.info("Connection restored")
        backoff.reset()
        if handled:
            log.info("Handled %d email(s)", handled)
        stop.wait(interval)
    log.info("Stopped")
    return 0


def install_signal_handlers(stop: threading.Event) -> None:
    """First Ctrl+C / SIGTERM: finish the current email, then exit. A second Ctrl+C exits immediately."""

    def handle(signum, frame):
        if stop.is_set():
            raise KeyboardInterrupt
        log.info("Got %s; finishing the current email, then stopping (Ctrl+C again to quit now)", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)


def prune_downloads(root: Path | None = None, max_age_hours: float | None = None,
                    error_max_age_days: float | None = None, now: float | None = None) -> int:
    """Delete downloads left behind (replies that failed to send) older than max_age_hours,
    and error screenshots older than error_max_age_days. Returns how many items were removed."""
    root = root or settings.download_dir
    max_age = (settings.cleanup_after_hours if max_age_hours is None else max_age_hours) * 3600
    error_max_age = (settings.error_screenshot_days if error_max_age_days is None else error_max_age_days) * 86400
    now = time.time() if now is None else now
    if not root.is_dir():
        return 0
    removed = 0
    for item in root.iterdir():
        if item.name == "errors":
            for shot in item.iterdir():
                if now - shot.stat().st_mtime > error_max_age:
                    _remove(shot)
                    removed += 1
        elif now - _newest_mtime(item) > max_age:
            _remove(item)
            removed += 1
    if removed:
        log.info("Pruned %d old item(s) from %s", removed, root)
    return removed


def _newest_mtime(path: Path) -> float:
    """A folder counts as old only when nothing inside it is recent."""
    if path.is_dir():
        return max((p.stat().st_mtime for p in path.rglob("*")), default=path.stat().st_mtime)
    return path.stat().st_mtime


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
