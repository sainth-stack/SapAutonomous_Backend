"""Synthetic SAP monitoring feeds (background jobs, failed IDocs).

Used when SAP CPI/APIM is not configured or when SAP_MONITORING_DATA_SOURCE=static.
Dates are shifted on read so rows look current relative to the server clock.
"""
from __future__ import annotations

import json
import logging
import os
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BACKGROUND_JOBS_JSON = DATA_DIR / "background_jobs.json"
FAILED_IDOCS_JSON = DATA_DIR / "failed_idocs.json"

_ODATA_DATE_RE = re.compile(r"^/Date\((\d+)\)/$")
_JOB_DATE_FIELDS = (
    "ScheduledStartDate",
    "ExecutionStartDate",
    "ActualEndDate",
    "LastChangeOn",
)
_IDOC_DATE_FIELDS = ("creation_date", "last_updated_date")
_JOB_TIME_FIELDS = (
    ("ScheduledStartDate", "ScheduledStartTime"),
    ("ExecutionStartDate", "ExecutionStartTime"),
    ("ActualEndDate", "ActualEndTime"),
)
_IDOC_TIME_FIELDS = (
    ("creation_date", "creation_time"),
    ("last_updated_date", "last_updated_time"),
)


def monitoring_data_source() -> str:
    """auto | static | live — default auto."""
    return (os.environ.get("SAP_MONITORING_DATA_SOURCE") or "auto").strip().lower()


def _parse_odata_ms(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    m = _ODATA_DATE_RE.match(s)
    if m:
        return int(m.group(1))
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _format_odata_ms(ms: int) -> str:
    return f"/Date({ms})/"


def _sap_time_from_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H%M%S")


def _shift_document(doc: dict, date_fields: tuple, time_pairs: tuple, shift_ms: int) -> dict:
    out = deepcopy(doc)
    for field in date_fields:
        base = _parse_odata_ms(out.get(field))
        if base is None:
            continue
        new_ms = base + shift_ms
        out[field] = _format_odata_ms(new_ms)
    for date_key, time_key in time_pairs:
        base = _parse_odata_ms(out.get(date_key))
        if base is not None:
            out[time_key] = _sap_time_from_ms(base + shift_ms)
    return out


def _load_json(path: Path) -> Optional[dict]:
    if not path.is_file():
        logger.warning("Monitoring static data missing: %s", path)
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Failed to read %s: %s", path, exc)
        return None


def _shift_rows(rows: List[dict], date_fields: tuple, time_pairs: tuple, reference_ms: int) -> List[dict]:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    shift_ms = now_ms - reference_ms
    return [_shift_document(row, date_fields, time_pairs, shift_ms) for row in rows]


def load_background_jobs_static() -> List[dict]:
    payload = _load_json(BACKGROUND_JOBS_JSON)
    if not payload:
        return []
    rows = payload.get("rows") or []
    ref = int(payload.get("reference_epoch_ms") or 0)
    if not ref or not rows:
        return list(rows)
    shifted = _shift_rows(rows, _JOB_DATE_FIELDS, _JOB_TIME_FIELDS, ref)
    logger.info("background-jobs static: %s rows (dates shifted)", len(shifted))
    return shifted


def load_failed_idocs_static() -> List[dict]:
    payload = _load_json(FAILED_IDOCS_JSON)
    if not payload:
        return []
    rows = payload.get("result") or []
    ref = int(payload.get("reference_epoch_ms") or 0)
    if not ref or not rows:
        return list(rows)
    shifted = _shift_rows(rows, _IDOC_DATE_FIELDS, _IDOC_TIME_FIELDS, ref)
    logger.info("failed-idocs static: %s rows (dates shifted)", len(shifted))
    return shifted


def monitoring_live_configured(cfg: dict) -> bool:
    """True when real SAP monitoring integrations are configured."""
    if cfg.get("proxy_base"):
        return True
    cpi_ok = bool(
        cfg.get("bg_jobs_cpi_url")
        and cfg.get("bg_jobs_cpi_user")
        and cfg.get("bg_jobs_cpi_password")
    )
    if cpi_ok and cfg.get("bg_jobs_odata_base"):
        return True
    if cpi_ok and cfg.get("failed_idocs_odata_base"):
        return True
    if cfg.get("failed_idocs_url") and cfg.get("sap_username") and cfg.get("sap_password"):
        return True
    return False


def use_static_monitoring(cfg: dict, *, live_result_count: Optional[int] = None) -> bool:
    mode = monitoring_data_source()
    if mode == "static":
        return True
    if mode == "live":
        return False
    if not monitoring_live_configured(cfg):
        return True
    if live_result_count is not None and live_result_count == 0:
        return True
    return False