"""Compose replies from canned facts (no scraping) to see what Claude and the template write.

Usage:
    .venv/bin/python scripts/try_composer.py [CASE ...] [--template] [--name NAME]
Cases: success, zero_docs, skipped_for_size, failed_download, not_found, clarification (default: all)
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # for tests.composer_cases

from tests.composer_cases import CASES  # noqa: E402
from uarb_agent import composer  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cases", nargs="*", choices=[[], *CASES], default=list(CASES), metavar="CASE")
    ap.add_argument("--template", action="store_true", help="skip Claude and use the template")
    ap.add_argument("--name", help="sender name to greet")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    for name in args.cases or CASES:
        facts = CASES[name]()
        facts.sender_name = args.name
        start = time.monotonic()
        body, by = composer.compose(facts, use_llm=not args.template)
        print(f"\n===== {name} (written by {by}, {time.monotonic() - start:.1f}s) =====")
        print(body)


if __name__ == "__main__":
    main()
