"""
Load tickets into ChromaDB.

    python -m similarity.ingest                       # default file (see below)
    python -m similarity.ingest path/to/tickets.csv   # explicit file (CSV or TSV)
    python -m similarity.ingest --reset               # wipe the collection first

Default file: ``$SIMILARITY_TICKETS_FILE`` or ``data/similarity_tickets.csv`` /
``data/similarity_tickets.tsv``. After ingest the per-year/priority counts are
compared against ``data/similarity_expected_counts.json`` (the reference table).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .analytics import year_priority_counts
from .parser import Ticket, load_tickets_file, parse_tickets
from .store import BACKEND_DIR, get_store

logger = logging.getLogger(__name__)

DATA_DIR = BACKEND_DIR / "data"
EXPECTED_COUNTS_FILE = DATA_DIR / "similarity_expected_counts.json"


def default_tickets_file() -> Optional[Path]:
    env = os.environ.get("SIMILARITY_TICKETS_FILE")
    candidates = [Path(env)] if env else []
    candidates += [DATA_DIR / "similarity_tickets.csv", DATA_DIR / "similarity_tickets.tsv"]
    return next((p for p in candidates if p.exists()), None)


def validate_counts() -> Dict[str, Any]:
    """Compare stored counts with the reference table (if present)."""
    actual = year_priority_counts(get_store().all_metadata())
    expected: Dict[str, Dict[str, int]] = {}
    if EXPECTED_COUNTS_FILE.exists():
        expected = json.loads(EXPECTED_COUNTS_FILE.read_text())
    mismatches = []
    for year, counts in expected.items():
        for priority, exp in counts.items():
            got = actual.get(year, {}).get(priority, 0)
            if got != exp:
                mismatches.append({"year": year, "priority": priority, "expected": exp, "actual": got})
    return {"actual": actual, "expected": expected,
            "matches_expected": bool(expected) and not mismatches, "mismatches": mismatches}


def _load(tickets: list[Ticket], reset: bool) -> Dict[str, Any]:
    store = get_store()
    if reset:
        store.reset()
    written = store.upsert(tickets)
    return {"ingested": written, "total_in_store": store.count(), "validation": validate_counts()}


def ingest_file(path: Path, reset: bool = False) -> Dict[str, Any]:
    return _load(load_tickets_file(path), reset)


def ingest_text(text: str, reset: bool = False) -> Dict[str, Any]:
    return _load(parse_tickets(text), reset)


def ensure_seeded() -> Optional[Dict[str, Any]]:
    """Startup hook: if the collection is empty and a ticket file exists, load it."""
    try:
        if get_store().count() > 0:
            return None
        path = default_tickets_file()
        if path is None:
            logger.info("Similarity store is empty and no ticket file found; skipping seed.")
            return None
        result = ingest_file(path)
        logger.info("Seeded similarity store from %s: %s", path, result["ingested"])
        return result
    except Exception:
        logger.exception("Similarity store seeding failed")
        return None


def main(argv=None) -> int:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Load tickets into ChromaDB")
    parser.add_argument("file", nargs="?", help="CSV/TSV ticket export")
    parser.add_argument("--reset", action="store_true", help="wipe the collection first")
    args = parser.parse_args(argv)

    path = Path(args.file) if args.file else default_tickets_file()
    if path is None or not path.exists():
        print("No ticket file found. Pass a path or place it at data/similarity_tickets.csv", file=sys.stderr)
        return 1
    result = ingest_file(path, reset=args.reset)
    print(f"Ingested {result['ingested']} tickets from {path} (store now holds {result['total_in_store']}).")
    v = result["validation"]
    for year, counts in sorted(v["actual"].items()):
        print(f"  {year}: " + ", ".join(f"{p}={counts[p]}" for p in sorted(counts)))
    if v["expected"]:
        print("Matches reference table." if v["matches_expected"]
              else f"WARNING: differs from reference table: {v['mismatches']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
