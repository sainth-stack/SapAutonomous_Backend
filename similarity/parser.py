"""
Ticket file parsing.

Input is a CSV / TSV export with the columns:

    Ticket Creation Date | Time | Ticket ID | Priority | Ticket Description

The "Ticket Description" column is a semi-structured string:

    <headline>. System: <s>. Location/Channel: <l>. Business team: <t>.
    Category: <c>. Impact: <i>. Additional details: <d>. Latest action: <a>.

We keep the full text as the embedded document and additionally split it into
structured fields, which are stored as Chroma metadata (used for filtering,
analytics and for composing the KEDB-style answer).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, List, Optional, Union

PRIORITIES = ["Critical", "High", "Medium", "Low"]

_COLUMN_ALIASES = {
    "date": {"ticket creation date", "creation date", "created", "created date", "date"},
    "time": {"time", "creation time", "ticket creation time"},
    "ticket_id": {"ticket id", "ticketid", "id", "ticket", "incident id", "ticket number"},
    "priority": {"priority", "severity"},
    "description": {"ticket description", "description", "short description", "details"},
}

# Order matters: each label terminates the previous value.
_FIELD_LABELS = [
    ("system", "System"),
    ("location", "Location/Channel"),
    ("business_team", "Business team"),
    ("category", "Category"),
    ("impact", "Impact"),
    ("additional_details", "Additional details"),
    ("latest_action", "Latest action"),
]

_FIELD_RE = re.compile(
    r"(?P<label>" + "|".join(re.escape(lbl) for _, lbl in _FIELD_LABELS) + r")\s*:\s*",
    re.IGNORECASE,
)
_LABEL_TO_KEY = {lbl.lower(): key for key, lbl in _FIELD_LABELS}


@dataclass
class Ticket:
    ticket_id: str
    date: str
    time: str
    priority: str
    description: str
    headline: str = ""
    system: str = ""
    location: str = ""
    business_team: str = ""
    category: str = ""
    impact: str = ""
    additional_details: str = ""
    latest_action: str = ""
    year: int = 0
    month: int = 0

    @property
    def embed_text(self) -> str:
        """
        Text that gets embedded. The description tail (System/Location/Team/...) is
        near-identical boilerplate across tickets and would dilute similarity, so we
        embed the problem statement only; the full text is still stored for display.
        """
        return self.headline or self.description

    def metadata(self) -> dict:
        """Flat, Chroma-safe (str/int only) metadata."""
        data = asdict(self)
        data.pop("description")  # stored as the document itself
        return data


def _clean(value: Optional[str]) -> str:
    return (value or "").strip().strip('"').strip()


def _strip_trailing_period(value: str) -> str:
    return value.strip().rstrip(".").strip()


def normalize_priority(value: str) -> str:
    v = _clean(value).lower()
    for p in PRIORITIES:
        if p.lower() == v:
            return p
    return _clean(value).title() or "Unknown"


def parse_description(description: str) -> dict:
    """Split a ticket description into headline + labelled fields."""
    matches = list(_FIELD_RE.finditer(description))
    fields = {key: "" for key, _ in _FIELD_LABELS}
    if not matches:
        return {"headline": _strip_trailing_period(description), **fields}

    headline = _strip_trailing_period(description[: matches[0].start()])
    for idx, m in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(description)
        key = _LABEL_TO_KEY[m.group("label").lower()]
        fields[key] = _strip_trailing_period(description[m.end():end])
    return {"headline": headline, **fields}


def build_ticket(date: str, time: str, ticket_id: str, priority: str, description: str) -> Ticket:
    description = _clean(description)
    parts = parse_description(description)
    year = month = 0
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", date)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
    return Ticket(
        ticket_id=_clean(ticket_id).upper(),
        date=_clean(date),
        time=_clean(time),
        priority=normalize_priority(priority),
        description=description,
        year=year,
        month=month,
        **parts,
    )


def _map_header(header: List[str]) -> dict:
    mapping = {}
    for idx, name in enumerate(header):
        n = name.strip().lower()
        for key, aliases in _COLUMN_ALIASES.items():
            if n in aliases and key not in mapping:
                mapping[key] = idx
    missing = {"date", "ticket_id", "priority", "description"} - set(mapping)
    if missing:
        raise ValueError(
            "Ticket file is missing required column(s): "
            + ", ".join(sorted(missing))
            + ". Expected: Ticket Creation Date, Time, Ticket ID, Priority, Ticket Description."
        )
    return mapping


def parse_tickets(text: str) -> List[Ticket]:
    """Parse CSV / TSV text into tickets. Delimiter is auto-detected."""
    text = text.lstrip("\ufeff")
    first_line = text.splitlines()[0] if text.strip() else ""
    delimiter = "\t" if first_line.count("\t") >= 3 else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return []

    mapping = _map_header(rows[0])
    tickets: dict[str, Ticket] = {}
    for row in rows[1:]:
        def col(key: str) -> str:
            idx = mapping.get(key)
            return row[idx] if idx is not None and idx < len(row) else ""

        ticket_id = _clean(col("ticket_id"))
        description = _clean(col("description"))
        if not ticket_id or not description:
            continue
        ticket = build_ticket(col("date"), col("time"), ticket_id, col("priority"), description)
        tickets[ticket.ticket_id] = ticket  # last one wins on duplicate ids
    return list(tickets.values())


def load_tickets_file(path: Union[str, Path]) -> List[Ticket]:
    return parse_tickets(Path(path).read_text(encoding="utf-8-sig"))


def iter_batches(items: List[Ticket], size: int) -> Iterable[List[Ticket]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
