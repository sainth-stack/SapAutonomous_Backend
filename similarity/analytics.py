"""Ticket analytics computed from Chroma metadata (exact, no LLM involved)."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List

from .parser import PRIORITIES
from .query_understanding import matches_filters


def _group_value(meta: dict, group_by: str) -> str:
    if group_by == "month":
        return f"{meta.get('year', 0):04d}-{meta.get('month', 0):02d}"
    return str(meta.get(group_by) or "Unknown")


def priority_matrix(
    metadata: List[dict],
    group_by: str = "year",
    filters: Dict[str, List] | None = None,
) -> Dict[str, Any]:
    """Rows = ``group_by`` values, columns = priorities, plus a Total column."""
    filters = filters or {}
    rows_map: Dict[str, Counter] = defaultdict(Counter)
    for meta in metadata:
        if not matches_filters(meta, filters):
            continue
        rows_map[_group_value(meta, group_by)][meta.get("priority", "Unknown")] += 1

    wanted = filters.get("priority") or PRIORITIES
    # Same column order as the reference table: Critical, High, Low, Medium
    columns = [p for p in sorted(PRIORITIES) if p in wanted]
    rows: List[Dict[str, Any]] = []
    for key in sorted(rows_map):
        counts = rows_map[key]
        row: Dict[str, Any] = {"group": key}
        for p in columns:
            row[p] = counts.get(p, 0)
        row["Total"] = sum(counts.get(p, 0) for p in columns)
        rows.append(row)

    totals: Dict[str, Any] = {"group": "Total"}
    for p in columns:
        totals[p] = sum(r[p] for r in rows)
    totals["Total"] = sum(r["Total"] for r in rows)

    return {
        "group_by": group_by,
        "columns": columns,
        "rows": rows,
        "totals": totals,
    }


def year_priority_counts(metadata: List[dict]) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    for row in priority_matrix(metadata)["rows"]:
        out[row["group"]] = {p: row[p] for p in PRIORITIES}
    return out
