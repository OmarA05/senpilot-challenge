"""Try the UARB scraper by hand.

Usage:
    .venv/bin/python scripts/try_scraper.py M12205 "Other Documents" --limit 3 [--headed]
"""

import argparse
import logging
import sys
import time
from pathlib import Path

from uarb_agent.config import settings
from uarb_agent.models import DocType
from uarb_agent.scraper import MatterNotFound, UarbScraper


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("matter", help="e.g. M12205")
    ap.add_argument("doc_type", nargs="?", default="Other Documents", choices=[t.value for t in DocType])
    ap.add_argument("--limit", type=int, default=settings.max_documents)
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")

    doc_type = DocType(args.doc_type)
    dest = settings.download_dir / args.matter / doc_type.slug
    start = time.monotonic()

    with UarbScraper(headless=not args.headed) as scraper:
        try:
            scraper.open_matter(args.matter)
        except MatterNotFound:
            print(f"\nMatter {args.matter} was not found.")
            return 1

        info = scraper.get_metadata()
        print("\n=== Matter metadata ===")
        for key, value in info.model_dump(exclude={"tab_counts"}).items():
            print(f"  {key:24} {value}")
        print("\n=== Document counts ===")
        for t in DocType:
            print(f"  {t.value:24} {info.tab_counts.get(t, '?')}")

        docs = scraper.list_documents(doc_type)
        print(f"\n=== {doc_type.value}: {len(docs)} listed ===")
        for d in docs:
            print(f"  {d.doc_no:>8}  {d.date or '':10}  {d.title}")

        print(f"\n=== Downloading up to {args.limit} to {dest} ===")
        downloaded = scraper.download_documents(doc_type, args.limit, dest)
        for d in downloaded:
            print(f"  {d.path}  ({Path(d.path).stat().st_size / 1024:.0f} KB)")
        print(f"\nDownloaded {len(downloaded)} of {info.tab_counts.get(doc_type, 0)} {doc_type.value} "
              f"in {time.monotonic() - start:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
