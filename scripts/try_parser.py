"""Try the request parser by hand (no scraping, a few seconds at most).

Usage:
    .venv/bin/python scripts/try_parser.py "Exhibits for M12205 and the key stuff for M12383" [--regex]
"""

import argparse
import logging
import time

from uarb_agent.config import settings
from uarb_agent.parser import parse_request


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", help="the email text to parse")
    ap.add_argument("--regex", action="store_true", help="skip Claude and use the regex parser")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    start = time.monotonic()
    parsed = parse_request(args.text, use_llm=not args.regex)
    elapsed = time.monotonic() - start

    model = f" ({settings.anthropic_model})" if parsed.parsed_by == "llm" else ""
    print(f"Parsed by: {parsed.parsed_by}{model} in {elapsed:.1f}s")
    print(f"Jobs ({len(parsed.jobs)}):")
    for j in parsed.jobs:
        print(f"  - {j.matter_number}  {j.doc_type}")
    if not parsed.jobs:
        print("  (none)")
    print(f"Clarification: {parsed.clarification or '(none)'}")


if __name__ == "__main__":
    main()
