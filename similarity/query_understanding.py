"""
Rule-based query understanding (no LLM, deterministic, free).

A user message is classified into one of:

* ``ticket_lookup`` — message contains ticket id(s) that exist in the store
* ``stats``         — "how many critical tickets in 2025", "count by year and priority"
* ``search``        — anything else: semantic similarity search

and structured filters (year / priority / system / location) are extracted so
they can narrow the Chroma query or the analytics aggregation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .parser import PRIORITIES

TICKET_ID_RE = re.compile(r"\b[A-Za-z]{3,10}\d{4,8}\b")

STATS_RE = re.compile(
    r"\b(how many|count|counts|number of|no\.? of|total|totals|distribution|breakdown|"
    r"statistics|statistic|stats|percentage|percent|per year|by year|yearly|year[- ]?wise|"
    r"by priority|priority[- ]?wise|trend|trends|split)\b",
    re.IGNORECASE,
)

_YEAR_RE = re.compile(r"\b(20\d{2})\b")

_PRIORITY_ALT = "|".join(p.lower() for p in PRIORITIES)
_PRIORITY_QUALIFIED_RE = re.compile(
    rf"\b({_PRIORITY_ALT})\b\s*[- ]?\s*(?:priority|severity|prio)\b"
    rf"|\b(?:priority|severity)\s*(?:is|of|=|:)?\s*({_PRIORITY_ALT})\b"
    rf"|\b({_PRIORITY_ALT})\s+(?:tickets?|incidents?|issues?|cases?)\b",
    re.IGNORECASE,
)
_PRIORITY_BARE_RE = re.compile(rf"\b({_PRIORITY_ALT})\b", re.IGNORECASE)

_GROUP_BY_PATTERNS = [
    ("month", re.compile(r"\b(by|per)\s+month\b|\bmonth[- ]?wise\b|\bmonthly\b", re.I)),
    ("system", re.compile(r"\b(by|per)\s+system\b|\bsystem[- ]?wise\b", re.I)),
    ("category", re.compile(r"\b(by|per)\s+category\b|\bcategory[- ]?wise\b", re.I)),
    ("business_team", re.compile(r"\b(by|per)\s+(business\s+)?team\b|\bteam[- ]?wise\b", re.I)),
    ("location", re.compile(r"\b(by|per)\s+(location|channel|store)\b|\blocation[- ]?wise\b", re.I)),
    ("impact", re.compile(r"\b(by|per)\s+impact\b|\bimpact[- ]?wise\b", re.I)),
]

# Cities that prefix location names ("Delhi Store 031", "Chennai DC", ...)
_NON_CITY_PREFIXES = {"regional", "online"}


# Filters that always restrict a semantic search. system / location only *boost*
# ranking (a hard filter on them would hide the best semantic match).
HARD_FILTERS = ("year", "priority")

_STOPWORDS = {
    "ticket", "tickets", "incident", "incidents", "issue", "issues", "case", "cases",
    "show", "list", "me", "all", "in", "of", "for", "the", "a", "an", "and", "with",
    "from", "give", "display", "get", "find", "what", "are", "is", "any", "there",
    "priority", "severity", "about", "on", "at", "to", "please", "related", "by",
    "those", "that", "raised", "logged", "created", "have", "had", "were", "was",
}


@dataclass
class Intent:
    kind: str = "search"                    # search | stats | ticket_lookup
    ticket_ids: List[str] = field(default_factory=list)
    filters: Dict[str, List] = field(default_factory=dict)   # field -> allowed values
    group_by: str = "year"
    query: str = ""
    matched_terms: List[str] = field(default_factory=list)   # text consumed by filters

    @property
    def hard_filters(self) -> Dict[str, List]:
        return {k: v for k, v in self.filters.items() if k in HARD_FILTERS}

    @property
    def soft_filters(self) -> Dict[str, List]:
        return {k: v for k, v in self.filters.items() if k not in HARD_FILTERS}

    def residual_query(self) -> str:
        """The query with filter phrases and filler words removed ('' = filters only)."""
        text = self.query.lower()
        for term in sorted(self.matched_terms, key=len, reverse=True):
            text = re.sub(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])", " ", text)
        words = re.findall(r"[a-z0-9/\-\.]+", text)
        return " ".join(w for w in words if w.strip(".-/") and w not in _STOPWORDS)

    def describe_filters(self) -> str:
        parts = []
        for key in ("priority", "year", "system", "location"):
            vals = self.filters.get(key)
            if vals:
                label = {"year": "year", "priority": "priority", "system": "system",
                         "location": "location"}[key]
                parts.append(f"{label} {', '.join(str(v) for v in vals)}")
        return "; ".join(parts)


def _vocab_matches(query: str, vocab: List[str]) -> List[tuple]:
    """Match vocabulary entries inside the query (longest first, no overlaps).

    Returns ``[(vocab_value, matched_text), ...]``.
    """
    found: List[str] = []
    out: List[tuple] = []
    taken: List[tuple] = []
    candidates = []
    for value in vocab:
        candidates.append((value, value, re.IGNORECASE))
        if value.upper().startswith("SAP "):
            # "SAP MM" can also be written as "MM" (uppercase only to avoid noise)
            short = value[4:]
            if len(short) >= 2:
                candidates.append((value, short, 0))
    candidates.sort(key=lambda c: len(c[1]), reverse=True)
    for value, text, flags in candidates:
        if value in found:
            continue
        m = re.search(rf"(?<![A-Za-z0-9]){re.escape(text)}(?![A-Za-z0-9])", query, flags)
        if m and not any(m.start() < e and m.end() > s for s, e in taken):
            found.append(value)
            out.append((value, m.group(0)))
            taken.append((m.start(), m.end()))
    return out


def _city_locations(query: str, locations: List[str]) -> List[tuple]:
    cities = {loc.split()[0] for loc in locations if loc.split()[0].lower() not in _NON_CITY_PREFIXES}
    matched = [c for c in cities if re.search(rf"\b{re.escape(c)}\b", query, re.IGNORECASE)]
    return [(loc, loc.split()[0]) for loc in sorted(locations) if loc.split()[0] in matched]


def analyze_query(
    query: str,
    known_ids: Optional[List[str]] = None,
    systems: Optional[List[str]] = None,
    locations: Optional[List[str]] = None,
) -> Intent:
    """
    ``known_ids`` — ticket ids from the query that really exist in the store
    (the caller resolves this, so we never treat random tokens as ticket ids).
    """
    q = query.strip()
    intent = Intent(query=q)

    if known_ids:
        intent.kind = "ticket_lookup"
        intent.ticket_ids = known_ids
        return intent

    is_stats = bool(STATS_RE.search(q))

    # year
    years = sorted({int(y) for y in _YEAR_RE.findall(q)})
    if years:
        intent.filters["year"] = years
        intent.matched_terms += [str(y) for y in years]

    # priority — bare words only count in analytics mode ("how many high ...")
    pr_regex = _PRIORITY_BARE_RE if is_stats else _PRIORITY_QUALIFIED_RE
    priorities: List[str] = []
    for m in pr_regex.finditer(q):
        word = next(g for g in m.groups() if g) if not is_stats else m.group(1)
        p = word.capitalize()
        if p not in priorities:
            priorities.append(p)
            intent.matched_terms.append(word)
    if priorities:
        intent.filters["priority"] = priorities

    if systems:
        matched = _vocab_matches(q, systems)
        if matched:
            intent.filters["system"] = [v for v, _ in matched]
            intent.matched_terms += [t for _, t in matched]
    if locations:
        matched = _vocab_matches(q, locations) or _city_locations(q, locations)
        if matched:
            intent.filters["location"] = [v for v, _ in matched]
            intent.matched_terms += [t for _, t in matched]

    if is_stats:
        intent.kind = "stats"
        for key, pattern in _GROUP_BY_PATTERNS:
            if pattern.search(q):
                intent.group_by = key
                break
    return intent


def extract_ticket_id_candidates(query: str) -> List[str]:
    seen, out = set(), []
    for tok in TICKET_ID_RE.findall(query):
        up = tok.upper()
        if up not in seen:
            seen.add(up)
            out.append(up)
    return out


def matches_filters(meta: dict, filters: Dict[str, List]) -> bool:
    return all(str(meta.get(k)) in {str(v) for v in vals} for k, vals in filters.items() if vals)


def to_chroma_filters(filters: Dict[str, List]) -> List[dict]:
    out: List[dict] = []
    for key, vals in filters.items():
        if not vals:
            continue
        if key == "year":
            vals = [int(v) for v in vals]
        out.append({key: vals[0]} if len(vals) == 1 else {key: {"$in": list(vals)}})
    return out
