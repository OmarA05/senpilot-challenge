import argparse
import logging
import zipfile

from .agent import handle_request, process_inbox_once


def main() -> None:
    parser = argparse.ArgumentParser(prog="uarb_agent", description="UARB document agent")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)
    once = sub.add_parser("once", help="handle one request locally (no email) and print the replies")
    once.add_argument("text", help='request text, e.g. "Other Documents for M12205"')
    once.add_argument("--name", help="sender's name to greet (default: \"there\")")
    check = sub.add_parser("check", help="process unread emails in the inbox once and reply")
    check.add_argument("--subject-contains", help="only handle unread emails whose subject contains this text")
    sub.add_parser("run", help="poll the inbox and reply by email")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if args.command == "once":
        _print_results(args.text, args.name)
    elif args.command == "check":
        n = process_inbox_once(args.subject_contains)
        print(f"Processed {n} email(s).")
    else:
        print("run: not implemented yet (Chunk 9)")


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
