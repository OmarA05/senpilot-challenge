import argparse
import logging
import sys
import threading
import zipfile

from .agent import CONNECTION_ERRORS, handle_request, process_inbox_once
from .config import settings
from .email_in import EmailAuthError
from .runner import install_signal_handlers, prune_downloads, run_forever


def main() -> None:
    parser = argparse.ArgumentParser(prog="uarb_agent", description="UARB document agent")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)
    once = sub.add_parser("once", help="handle one request locally (no email) and print the replies")
    once.add_argument("text", help='request text, e.g. "Other Documents for M12205"')
    once.add_argument("--name", help="sender's name to greet (default: \"there\")")
    check = sub.add_parser("check", help="process unread emails in the inbox once and reply")
    check.add_argument("--subject-contains", help="only handle unread emails whose subject contains this text")
    run = sub.add_parser("run", help="poll the inbox and reply by email until stopped (Ctrl+C)")
    run.add_argument("--interval", type=float, help=f"seconds between inbox checks (default {settings.poll_interval_seconds})")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if args.command == "once":
        _print_results(args.text, args.name)
    elif args.command == "check":
        try:
            n = process_inbox_once(args.subject_contains)
        except EmailAuthError as e:
            sys.exit(str(e))
        except CONNECTION_ERRORS as e:
            sys.exit(f"Couldn't reach Gmail: {type(e).__name__}: {e}")
        print(f"Processed {n} email(s).")
    else:
        sys.exit(_run(args.interval))


def _run(interval: float | None) -> int:
    stop = threading.Event()
    install_signal_handlers(stop)
    prune_downloads()
    logging.getLogger("uarb_agent").info(
        "Watching %s every %ss (Ctrl+C to stop)", settings.gmail_address, interval or settings.poll_interval_seconds
    )
    return run_forever(stop=stop, interval=interval)


def _print_results(text: str, sender_name: str | None = None) -> None:
    results = handle_request(text, sender_name)
    for i, r in enumerate(results, 1):
        print(f"\n===== Reply {i}/{len(results)} {r.subject_suffix or '[clarification]'} (written by {r.composed_by}) =====")
        print(r.reply_body)
        if r.zip_path:
            print(f"\nAttachment: {r.zip_path} ({r.zip_path.stat().st_size / 1024:.0f} KB)")
            with zipfile.ZipFile(r.zip_path) as zf:
                for info in zf.infolist():
                    print(f"  {info.filename:<24} {info.file_size / 1024:>7.0f} KB")
        else:
            print("\nAttachment: none")


if __name__ == "__main__":
    main()
