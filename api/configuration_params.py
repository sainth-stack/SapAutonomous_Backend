"""
main.py
-------
Headless FastAPI rendering engine.

Exposes two endpoints:
  GET  /api/configurations        – Returns the unified layout contract (metadata + live values)
  POST /api/configurations/save   – Validates and persists updated field values

The backend is strictly frontend-agnostic: all validation is performed
server-side regardless of what the calling client has already checked.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, List, Literal, Optional, Annotated
from sqlalchemy.orm import Session
import re
import sys
from fastapi import FastAPI, HTTPException, Request, status, APIRouter, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)
from api.database_config import close_pool, get_connection, init_pool
from database_gcp import Base, engine, get_db, get_postgres_db_conn
# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
)
logger = logging.getLogger(__name__)
DbSessionPostgres = Annotated[Session, Depends(get_postgres_db_conn)]
# ---------------------------------------------------------------------------
# FastAPI Lifespan – pool init / teardown
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):          # noqa: ANN001
    """
    Manage the asyncpg pool lifecycle alongside the ASGI application.
    Also runs a quick SELECT 1 probe right after pool creation so that a
    misconfigured .env causes a clear startup failure instead of silent 500s.
    """
    logger.info("Application startup - initialising database pool ...")
    await init_pool()

    try:
        async with get_connection() as conn:
            await conn.fetchval("SELECT 1")
        logger.info("Database connectivity verified successfully.")
    except Exception as exc:
        logger.error(
            "STARTUP DB CHECK FAILED - verify DB_HOST, DB_PORT, DB_NAME, "
            "DB_USER, DB_PASSWORD in your .env file. Error: %s", exc
        )
        raise   # Abort startup so uvicorn reports a clear failure

    yield
    logger.info("Application shutdown - closing database pool ...")
    await close_pool()


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------
# app = FastAPI(
#     title="GCP Configuration Layout Engine",
#     description=(
#         "Headless rendering engine that reads GCP Cloud SQL metadata blueprints "
#         "and emits a standardised UI layout contract consumed by any frontend client."
#     ),
#     version="1.0.0",
#     lifespan=lifespan,
# )

app = APIRouter()
# ---------------------------------------------------------------------------
# CORS – permit the React dev server and any future frontend origin.
# Restrict in production via the ALLOWED_ORIGINS environment variable.
# ---------------------------------------------------------------------------
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173")
allowed_origins: list[str] = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=allowed_origins,
#     allow_credentials=True,
#     allow_methods=["GET", "POST", "OPTIONS"],
#     allow_headers=["*"],
#     expose_headers=["*"],
# )

# ---------------------------------------------------------------------------
# Pydantic Output Schema  (GET /api/configurations response contract)
# ---------------------------------------------------------------------------
class ConfigurationFieldOut(BaseModel):
    """
    Single field descriptor emitted by the layout engine.
    Combines immutable blueprint metadata with its current live value.

    data_type and screen_field_type are normalised to canonical contract
    values by the SQL query before they reach this model, so we accept str
    here and enforce the whitelist in the model validator below.  This gives
    a clear server-side error message if an unexpected raw DB value is ever
    encountered, without the cryptic Pydantic Literal mismatch error.
    """
    field_id:          str           = Field(..., description="Unique field identifier (UUID cast to text)")
    field_group:       str           = Field(..., description="UI grouping / section header")
    field_name:        str           = Field(..., description="Human-readable label")
    data_type:         str           = Field(..., description="Expected data domain: String or Numeric")
    screen_field_type: str           = Field(..., description="Frontend component selector")
    mandatory:         bool          = Field(..., description="Whether a non-empty value is required")
    allowed_values:    Optional[str] = Field(
        None,
        description=(
            "Comma-separated option list; populated only when "
            "screen_field_type='Value Select'"
        ),
    )
    validation_pattern: Optional[str] = Field(
        None,
        description=(
            "Optional regex pattern the value must fully match. "
            "Null means no pattern constraint."
        ),
    )
    current_value:     Optional[str] = Field(
        None, description="Live runtime value currently stored (may be null)"
    )
    updated_at:        Optional[datetime] = Field(
        None, description="Timestamp of the last administrative change"
    )
    updated_by:        Optional[str] = Field(
        None, description="Identity of the last admin who saved this field"
    )

    # Whitelist check — catches any DB value the CASE expression didn't map
    @model_validator(mode="after")
    def check_enum_values(self) -> "ConfigurationFieldOut":
        valid_data_types        = {"String", "Numeric"}
        valid_screen_types      = {"free text", "Numeric", "Value Select"}
        if self.data_type not in valid_data_types:
            raise ValueError(
                f"Unexpected data_type '{self.data_type}' returned from DB. "
                f"Expected one of: {valid_data_types}. "
                "Add a WHEN clause to _SQL_LAYOUT to map this value."
            )
        if self.screen_field_type not in valid_screen_types:
            raise ValueError(
                f"Unexpected screen_field_type '{self.screen_field_type}' returned from DB. "
                f"Expected one of: {valid_screen_types}. "
                "Add a WHEN clause to _SQL_LAYOUT to map this value."
            )
        return self


class ConfigurationLayoutResponse(BaseModel):
    """Top-level response envelope for GET /api/configurations."""
    schema_version: str = Field("1.0", description="Contract version for client compatibility checks")
    total_fields:   int = Field(..., description="Count of active configuration fields returned")
    fields:         List[ConfigurationFieldOut]


# ---------------------------------------------------------------------------
# Pydantic Input Schema  (POST /api/configurations/save request contract)
# ---------------------------------------------------------------------------
class FieldUpdateIn(BaseModel):
    """
    Single field update payload submitted by the client.
    Structural integrity is checked here; domain-level rules are validated
    against the live metadata later inside the endpoint.
    """
    field_id:    str            = Field(..., min_length=1, description="Target field identifier")
    field_value: Optional[str] = Field(None, description="New value string (None / empty = clear)")
    updated_by:  str            = Field(..., min_length=1, description="Identity of the submitting admin")

    @field_validator("field_id", "updated_by", mode="before")
    @classmethod
    def strip_whitespace(cls, v: Any) -> str:
        if isinstance(v, str):
            return v.strip()
        return v


class SaveConfigurationRequest(BaseModel):
    """Wrapper for a batch save request carrying one or more field updates."""
    updates: List[FieldUpdateIn] = Field(..., min_length=1)


class FieldSaveResult(BaseModel):
    field_id: str
    status:   Literal["saved", "failed"]
    detail:   Optional[str] = None


class SaveConfigurationResponse(BaseModel):
    """Top-level response envelope for POST /api/configurations/save."""
    saved:   int
    failed:  int
    results: List[FieldSaveResult]


# ---------------------------------------------------------------------------
# SQL Queries
# ---------------------------------------------------------------------------
_SQL_LAYOUT = """
SELECT
    -- Cast UUID primary key to plain text so asyncpg returns a str, not UUID
    m.field_id::TEXT                                    AS field_id,
    m.field_group,
    m.field_name,

    -- Normalise data_type to the contract literals ('String' | 'Numeric').
    -- Covers every common numeric type name stored in the DB.
    CASE LOWER(TRIM(m.data_type))
        WHEN 'numeric'  THEN 'Numeric'
        WHEN 'number'   THEN 'Numeric'
        WHEN 'integer'  THEN 'Numeric'
        WHEN 'int'      THEN 'Numeric'
        WHEN 'bigint'   THEN 'Numeric'
        WHEN 'smallint' THEN 'Numeric'
        WHEN 'float'    THEN 'Numeric'
        WHEN 'decimal'  THEN 'Numeric'
        WHEN 'double'   THEN 'Numeric'
        WHEN 'real'     THEN 'Numeric'
        ELSE 'String'
    END                                                 AS data_type,

    -- Normalise screen_field_type to contract literals.
    -- Map every known DB spelling to the three canonical values.
    CASE LOWER(TRIM(m.screen_field_type))
        WHEN 'numeric'          THEN 'Numeric'
        WHEN 'number'           THEN 'Numeric'
        WHEN 'value select'     THEN 'Value Select'
        WHEN 'select'           THEN 'Value Select'
        WHEN 'dropdown'         THEN 'Value Select'
        ELSE                         'free text'   -- covers 'free text', 'input text field', 'text', etc.
    END                                                 AS screen_field_type,

    m.mandatory,
    m.allowed_values,
    m.validation_pattern,
    v.field_value   AS current_value,
    v.updated_at,
    v.updated_by
FROM  global_configuration_metadata AS m
LEFT  JOIN global_configuration_values AS v
      ON m.field_id::TEXT = v.field_id::TEXT
WHERE m.is_active = TRUE
ORDER BY m.field_group, m.field_name;
"""

# Fetch only the metadata rules for a specific set of field_ids (used in save)
_SQL_METADATA_FOR_IDS = """
SELECT
    field_id::TEXT                                      AS field_id,

    CASE LOWER(TRIM(data_type))
        WHEN 'numeric'  THEN 'Numeric'
        WHEN 'number'   THEN 'Numeric'
        WHEN 'integer'  THEN 'Numeric'
        WHEN 'int'      THEN 'Numeric'
        WHEN 'bigint'   THEN 'Numeric'
        WHEN 'smallint' THEN 'Numeric'
        WHEN 'float'    THEN 'Numeric'
        WHEN 'decimal'  THEN 'Numeric'
        WHEN 'double'   THEN 'Numeric'
        WHEN 'real'     THEN 'Numeric'
        ELSE 'String'
    END                                                 AS data_type,

    CASE LOWER(TRIM(screen_field_type))
        WHEN 'numeric'          THEN 'Numeric'
        WHEN 'number'           THEN 'Numeric'
        WHEN 'value select'     THEN 'Value Select'
        WHEN 'select'           THEN 'Value Select'
        WHEN 'dropdown'         THEN 'Value Select'
        ELSE                         'free text'
    END                                                 AS screen_field_type,

    mandatory,
    allowed_values,
    validation_pattern
FROM  global_configuration_metadata
WHERE field_id::TEXT = ANY($1::text[])
  AND is_active = TRUE;
"""

# Upsert a single field value into global_configuration_values
# Try UPDATE first; if no row exists the UPDATE touches 0 rows and we INSERT.
# This avoids ON CONFLICT which requires a PRIMARY KEY / UNIQUE constraint on
# field_id in global_configuration_values.
_SQL_UPDATE_VALUE = """
UPDATE global_configuration_values
   SET field_value = $2,
       updated_at  = NOW(),
       updated_by  = $3
 WHERE field_id = $1::UUID;
"""

_SQL_INSERT_VALUE = """
INSERT INTO global_configuration_values (field_id, field_value, updated_at, updated_by)
VALUES ($1::UUID, $2, NOW(), $3);
"""


# ---------------------------------------------------------------------------
# Server-Side Validation Helpers
# ---------------------------------------------------------------------------
# Pre-compiled numeric validation pattern.
# Accepts: integers, decimals, negatives, scientific notation (42, -3.14, 1e5).
# Rejects: any value with letters or mixed characters (3002abc, 12.3e, abc).
_NUMERIC_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def _validate_field(
    *,
    field_id:        str,
    submitted_value: Optional[str],
    meta:            dict,
) -> Optional[str]:
    """
    Apply all domain-level rules from the metadata blueprint to a single
    submitted value.  Returns None on success, or an error message string.

    meta values are already normalised by the SQL CASE expressions, so
    comparisons use the canonical contract strings ('Numeric', 'Value Select').
    """
    value = (submitted_value or "").strip()

    # 1. Mandatory rule -------------------------------------------------------
    if meta["mandatory"] and not value:
        return f"Field '{field_id}' is mandatory and cannot be empty."

    # Skip further checks on intentionally blank optional fields
    if not value:
        return None

    # 2. Data-type rule --------------------------------------------------------
    if meta["data_type"] == "Numeric":
        # Use a strict regex instead of float() alone.
        # float("3002abc") raises ValueError and IS caught, but this makes the
        # rule explicit and readable.  Accepts: integers, decimals, negatives,
        # and scientific notation.  Rejects anything with non-numeric characters.
        if not _NUMERIC_RE.match(value.strip()):
            return (
                f"Field '{field_id}' expects a numeric value; "
                f"'{value}' contains non-numeric characters and cannot be saved."
            )

    # 3. Allowed-values rule ---------------------------------------------------
    if meta["screen_field_type"] == "Value Select":
        raw_allowed: str = (meta.get("allowed_values") or "").strip()
        allowed_set: set[str] = set()

        if raw_allowed:
            # Handle JSON array format: ["abcd","user1","user2"]
            if raw_allowed.startswith("["):
                import json as _json
                try:
                    parsed = _json.loads(raw_allowed)
                    if isinstance(parsed, list):
                        allowed_set = {str(v).strip() for v in parsed if str(v).strip()}
                except (_json.JSONDecodeError, ValueError):
                    pass  # fall through to CSV parsing

            # Plain CSV fallback (also handles partial JSON that failed to parse)
            if not allowed_set:
                allowed_set = {
                    opt.strip().strip('"\'\'')
                    for opt in raw_allowed.strip("[]").split(",")
                    if opt.strip().strip('\"\'')
                }

        if allowed_set and value not in allowed_set:
            return (
                f"Field '{field_id}' only accepts one of: "
                f"{chr(44).join(sorted(allowed_set))}. Got: '{value}'."
            )

    # 4. Validation-pattern rule -----------------------------------------------
    pattern_str: Optional[str] = (meta.get("validation_pattern") or "").strip()
    if pattern_str and value:
        try:
            if not re.fullmatch(pattern_str, value):
                return (
                    f"Field '{field_id}' value '{value}' does not match "
                    f"the required format (pattern: {pattern_str})."
                )
        except re.error as exc:
            # Invalid regex in the DB — log and skip rather than crash
            logger.warning(
                "Field '%s' has an invalid validation_pattern '%s': %s — skipping pattern check.",
                field_id, pattern_str, exc,
            )

    return None  # All checks passed


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get(
    "/api/configurations",
    response_model=ConfigurationLayoutResponse,
    summary="Retrieve the unified UI layout contract",
    tags=["Configuration"],
)
async def get_configurations() -> ConfigurationLayoutResponse:
    """
    Performs a LEFT JOIN between the metadata blueprint table and the live
    configuration values table, filtering for active records only.

    Returns a standardised layout contract that any frontend client can
    consume to build a fully validated configuration form.
    """
    async with get_connection() as conn:
        rows = await conn.fetch(_SQL_LAYOUT)

    fields = [
        ConfigurationFieldOut(
            field_id          = row["field_id"],
            field_group       = row["field_group"],
            field_name        = row["field_name"],
            data_type         = row["data_type"],
            screen_field_type = row["screen_field_type"],
            mandatory         = row["mandatory"],
            allowed_values     = row["allowed_values"],
            validation_pattern = row["validation_pattern"],
            current_value      = row["current_value"],
            updated_at        = row["updated_at"],
            updated_by        = row["updated_by"],
        )
        for row in rows
    ]

    return ConfigurationLayoutResponse(
        schema_version="1.0",
        total_fields=len(fields),
        fields=fields,
    )


async def get_configuration_values(conn: DbSessionPostgres, parameter: str):
    data_row = []
    fetch_configuration_metadata_query = f"SELECT field_id, field_group, field_name FROM global_configuration_metadata WHERE field_name = {parameter}"

    cursor = conn.cursor()
    cursor.execute(fetch_configuration_metadata_query)
    metadata = cursor.fetchone()
    
    if not metadata:
        return None
    
    field_id = metadata[0] if metadata else None

    fetch_values_query = f"SELECT field_value FROM global_configuration_values WHERE  field_id = %s"

    cursor.execute(fetch_values_query, (field_id,))
    data_row = cursor.fetchone()
    return {
        "values": data_row[0]
    }
        
@app.get("/api/configurations/values")
async def fetch_configuration_values(conn: DbSessionPostgres, parameter: str):
    value = await get_configuration_values(conn=conn, parameter=parameter)

    if value:
        return value
    else:
        return None

@app.post(
    "/api/configurations/save",
    response_model=SaveConfigurationResponse,
    summary="Validate and persist configuration field values",
    tags=["Configuration"],
)
async def save_configurations(
    payload: SaveConfigurationRequest,
) -> SaveConfigurationResponse:
    """
    Accepts a batch of field updates, enforces absolute server-side schema
    validation against the live metadata rules, and performs an upsert for
    every record that passes validation.

    Validation rules applied per field:
      * Mandatory  – blank value rejected when mandatory=True.
      * Data type  – non-numeric string rejected when data_type='Numeric'.
      * Allowed    – value must match one of allowed_values when
                     screen_field_type='Value Select'.
      * Pattern    – value must fully match validation_pattern regex when set.

    Any validation failure causes the *entire batch* to be rejected with
    HTTP 400 and a structured error payload, preserving data integrity.
    """
    submitted_ids = [u.field_id for u in payload.updates]

    # --- Load metadata for exactly the submitted field IDs --------------------
    async with get_connection() as conn:
        meta_rows = await conn.fetch(_SQL_METADATA_FOR_IDS, submitted_ids)

    meta_by_id: dict[str, dict] = {
        row["field_id"]: dict(row) for row in meta_rows
    }

    # --- Pre-flight validation pass (all-or-nothing) -------------------------
    validation_errors: list[dict] = []

    for update in payload.updates:
        if update.field_id not in meta_by_id:
            validation_errors.append({
                "field_id": update.field_id,
                "error": (
                    f"Field '{update.field_id}' does not exist in the active "
                    "configuration blueprint and cannot be saved."
                ),
            })
            continue

        error_msg = _validate_field(
            field_id        = update.field_id,
            submitted_value = update.field_value,
            meta            = meta_by_id[update.field_id],
        )
        if error_msg:
            validation_errors.append({"field_id": update.field_id, "error": error_msg})

    if validation_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Validation failed.  No records were saved.",
                "errors":  validation_errors,
            },
        )

    # --- Upsert pass (all validations passed) ---------------------------------
    results: list[FieldSaveResult] = []
    saved_count = 0
    failed_count = 0

    # Each field gets its own savepoint so a failure on one row never aborts
    # the surrounding transaction and cascades "transaction is aborted" errors
    # to all subsequent rows.
    async with get_connection() as conn:
        async with conn.transaction():
            for update in payload.updates:
                try:
                    async with conn.transaction():   # savepoint per row
                        params = (
                            update.field_id,    # $1 -> cast to UUID in SQL
                            update.field_value, # $2 -> TEXT
                            update.updated_by,  # $3 -> TEXT
                        )
                        result = await conn.execute(_SQL_UPDATE_VALUE, *params)
                        # result is a string like "UPDATE 1" or "UPDATE 0"
                        rows_updated = int(result.split()[-1])
                        if rows_updated == 0:
                            await conn.execute(_SQL_INSERT_VALUE, *params)
                            logger.info("Inserted field '%s' by '%s'.", update.field_id, update.updated_by)
                        else:
                            logger.info("Updated field '%s' by '%s'.", update.field_id, update.updated_by)

                    results.append(FieldSaveResult(field_id=update.field_id, status="saved"))
                    saved_count += 1

                except Exception as exc:
                    logger.exception("Save failed for field '%s'.", update.field_id)
                    results.append(FieldSaveResult(
                        field_id=update.field_id,
                        status="failed",
                        detail=str(exc),
                    ))
                    failed_count += 1

    return SaveConfigurationResponse(
        saved=saved_count,
        failed=failed_count,
        results=results,
    )


# ---------------------------------------------------------------------------
# Health probe (optional – useful for Cloud Run / GKE readiness checks)
# ---------------------------------------------------------------------------
@app.get("/health", tags=["Ops"], include_in_schema=False)
async def health() -> dict:
    """Lightweight liveness probe; does not touch the database."""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat() + "Z"}


# ---------------------------------------------------------------------------
# Global exception handler – ensures error bodies are always JSON
# ---------------------------------------------------------------------------
#@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Manually inject CORS headers into every unhandled-exception response.

    FastAPI's CORSMiddleware only wraps *successful* ASGI responses that flow
    through the middleware stack normally.  When an exception handler short-
    circuits by returning a JSONResponse directly, the middleware never gets a
    chance to attach the Access-Control-Allow-Origin header – so the browser
    sees a CORS failure on top of the actual 500 error, making debugging much
    harder.  We solve this by reading the allowed origins list and injecting the
    header ourselves before returning.
    """
    logger.exception("Unhandled exception for %s %s", request.method, request.url)

    # Determine which origin header to echo back (must be exact match for
    # credentialed requests; use the first configured origin as a safe fallback)
    request_origin = request.headers.get("origin", "")
    if request_origin in allowed_origins:
        cors_origin = request_origin
    elif "*" in allowed_origins:
        cors_origin = "*"
    else:
        cors_origin = allowed_origins[0] if allowed_origins else "*"

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An unexpected internal error occurred.", "exc": str(exc)},
        headers={
            "Access-Control-Allow-Origin":      cors_origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods":     "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers":     "*",
        },
    )