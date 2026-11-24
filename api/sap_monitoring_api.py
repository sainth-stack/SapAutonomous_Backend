"""SAP Job Monitoring and Failed IDocs — static JSON feeds (demo / production UI).

Data: data/background_jobs.json, data/failed_idocs.json
Retrigger endpoints return synthetic SAP-shaped responses for the UI.
"""
import logging

from fastapi import APIRouter, Body, HTTPException

from api.monitoring_static_data import (
    load_background_jobs_static,
    load_failed_idocs_static,
    synthetic_bulk_retrigger_multipart,
    synthetic_retrigger_single_result,
)

logger = logging.getLogger(__name__)

monitoring_router = APIRouter(tags=["SAP Monitoring"])

logger.info("SAP monitoring: static JSON only (background_jobs.json, failed_idocs.json)")


@monitoring_router.get("/failed-idocs")
async def call_failed_idoc():
    rows = load_failed_idocs_static()
    logger.debug("failed-idocs: %s rows", len(rows))
    return {"result": rows}


@monitoring_router.get("/background-jobs")
async def call_bg_jobs():
    rows = load_background_jobs_static()
    logger.debug("background-jobs: %s rows", len(rows))
    return rows


@monitoring_router.post("/retrigger-idocs")
async def retrigger_failed_idoc(request: dict = Body(...)):
    iv_docnum = request.get("idocno")
    if iv_docnum is None or str(iv_docnum).strip() == "":
        raise HTTPException(status_code=400, detail="idocno is required")
    doc = str(iv_docnum).strip()
    logger.info("retrigger-idocs: %s", doc)
    return {"result": synthetic_retrigger_single_result(doc)}


@monitoring_router.post("/retrigger-bulk-idocs")
async def bulk_failed_idoc_retrigger(request: dict = Body(...)):
    idoc_numbers = [
        str(i).strip() for i in (request.get("idocno") or []) if str(i).strip()
    ]
    if not idoc_numbers:
        raise HTTPException(status_code=400, detail="idocno must be a non-empty list")
    logger.info("retrigger-bulk-idocs: %s", idoc_numbers)
    return {"result": synthetic_bulk_retrigger_multipart(idoc_numbers)}