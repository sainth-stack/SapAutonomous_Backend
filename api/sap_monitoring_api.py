"""SAP monitoring endpoints used by Job Monitoring and Failed IDocs pages.

Configuration (see .env.example):
  - Direct SAP: SAP_MONITORING_APIM_BASE, SAP_BACKGROUND_JOBS_CPI_* , SAP_BACKGROUND_JOBS_ODATA_BASE_URL
  - Optional proxy: SAP_MONITORING_PROXY_BASE → another backend that exposes /failed-idocs and /background-jobs
  - Not used for monitoring: SAP_S4_BASE_URL (self-service only)
"""
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import httpx as hp
import xmltodict
from fastapi import APIRouter, Body, HTTPException

logger = logging.getLogger(__name__)

monitoring_router = APIRouter(tags=["SAP Monitoring"])


def _strip(value: Optional[str]) -> str:
    return (value or "").strip().rstrip("/")


def _load_monitoring_settings() -> dict:
    """Read monitoring URLs/credentials from environment (no hardcoded trial tenants)."""
    proxy_base = _strip(os.environ.get("SAP_MONITORING_PROXY_BASE"))
    apim_base = _strip(os.environ.get("SAP_MONITORING_APIM_BASE"))
    apim_v4 = _strip(os.environ.get("SAP_MONITORING_APIM_V4_BASE"))
    if not apim_v4 and apim_base:
        apim_v4 = apim_base.replace("/odatasap", "/odatasapv4")

    failed_idocs_url = _strip(os.environ.get("SAP_FAILED_IDOCS_URL"))
    if not failed_idocs_url and apim_base:
        failed_idocs_url = (
            f"{apim_base}/ZC_IDOC_FAILED_CDS/ZC_IDOC_FAILED?$format=json"
        )

    retrigger_meta = (
        f"{apim_base}/ZREPROCESS_IDOC_SRV_SRV/$metadata" if apim_base else ""
    )
    retrigger_post = (
        f"{apim_base}/ZREPROCESS_IDOC_SRV_SRV/reprocess_idoc" if apim_base else ""
    )
    bulk_meta = (
        f"{apim_v4}/zidoc_reprocess_srv/srvd/sap/zidoc_reprocess_sd/0001/$metadata"
        if apim_v4
        else ""
    )
    bulk_post = (
        f"{apim_v4}/zidoc_reprocess_srv/srvd/sap/zidoc_reprocess_sd/0001/$batch"
        if apim_v4
        else ""
    )

    return {
        "proxy_base": proxy_base,
        "failed_idocs_url": failed_idocs_url,
        "retrigger_metadata_url": retrigger_meta,
        "retrigger_post_url": retrigger_post,
        "bulk_retrigger_metadata_url": bulk_meta,
        "bulk_retrigger_post_url": bulk_post,
        "bg_jobs_cpi_url": _strip(os.environ.get("SAP_BACKGROUND_JOBS_CPI_URL")),
        "bg_jobs_cpi_user": _strip(os.environ.get("SAP_BACKGROUND_JOBS_CPI_USER")),
        "bg_jobs_cpi_password": os.environ.get("SAP_BACKGROUND_JOBS_CPI_PASSWORD", "").strip(),
        "bg_jobs_odata_base": _strip(os.environ.get("SAP_BACKGROUND_JOBS_ODATA_BASE_URL")),
        "failed_idocs_odata_base": _strip(
            os.environ.get("SAP_FAILED_IDOCS_ODATA_BASE_URL")
        ),
        "failed_idocs_entity": _strip(
            os.environ.get("SAP_FAILED_IDOCS_ENTITY")
        )
        or "ZC_IDOC_FAILED",
        "failed_idocs_fetch_mode": _strip(
            os.environ.get("SAP_FAILED_IDOCS_FETCH_MODE")
        ).lower()
        or "cpi",
        "sap_username": _strip(os.environ.get("SAP_USERNAME")),
        "sap_password": os.environ.get("SAP_PASSWORD", "").strip(),
    }


def _log_monitoring_config() -> None:
    cfg = _load_monitoring_settings()
    logger.info(
        "SAP monitoring: proxy=%s failed_idocs=%s bg_cpi=%s bg_odata=%s sap_user_set=%s",
        cfg["proxy_base"] or "(none)",
        cfg["failed_idocs_fetch_mode"],
        "set" if cfg["bg_jobs_cpi_url"] else "missing",
        "set" if cfg["bg_jobs_odata_base"] else "missing",
        bool(cfg["sap_username"]),
    )


_log_monitoring_config()


def normalize_jobs(data):
    cleaned = []
    entries = data.get("feed", {}).get("entry", [])
    if isinstance(entries, dict):
        entries = [entries]
    if not entries:
        return cleaned

    for e in entries:
        props = e.get("content", {}).get("m:properties", {})
        cleaned.append({
            "JobName": props.get("d:JobName"),
            "JobCount": props.get("d:JobCount"),
            "ScheduledStartDate": props.get("d:ScheduledStartDate"),
            "ScheduledStartTime": props.get("d:ScheduledStartTime"),
            "ExecutionStartDate": props.get("d:ExecutionStartDate"),
            "ExecutionStartTime": props.get("d:ExecutionStartTime"),
            "ActualEndDate": props.get("d:ActualEndDate"),
            "ActualEndTime": props.get("d:ActualEndTime"),
            "RunTimeSeconds": props.get("d:RunTimeSeconds"),
            "Priority": props.get("d:Priority"),
            "JobStatus": props.get("d:JobStatus"),
            "JobClass": props.get("d:JobClass"),
            "CreatedBy": props.get("d:CreatedBy"),
            "ScheduledBy": props.get("d:ScheduledBy"),
            "StepCount": props.get("d:StepCount"),
            "EventId": props.get("d:EventId"),
            "StartHour": props.get("d:StartHour"),
            "IsWeekend": props.get("d:IsWeekend"),
            "LastChangeOn": props.get("d:LastChangeOn"),
        })
    return cleaned


def _map_failed_idoc_row(item: dict) -> dict:
    def _get(key: str, dkey: str) -> Optional[str]:
        return item.get(key) or item.get(dkey)

    return {
        "idoc_number": _get("IDocNumber", "d:IDocNumber"),
        "status": _get("IDocStatus", "d:IDocStatus"),
        "message_type": _get("MessageType", "d:MessageType"),
        "status_text": _get("StatusText", "d:StatusText"),
        "Direction": _get("DirectionText", "d:DirectionText"),
        "sender": _get("SenderPartnerNumber", "d:SenderPartnerNumber"),
        "receiver": _get("ReceiverPartnerNumber", "d:ReceiverPartnerNumber"),
        "sender_type": _get("SenderPartnerType", "d:SenderPartnerType"),
        "receiver_type": _get("ReceiverPartnerType", "d:ReceiverPartnerType"),
        "creation_date": _get("CreatedDate", "d:CreatedDate"),
        "creation_time": _get("CreatedTime", "d:CreatedTime"),
        "last_updated_date": _get("LastUpdatedDate", "d:LastUpdatedDate"),
        "last_updated_time": _get("LastUpdatedTime", "d:LastUpdatedTime"),
        "error_category": _get("ErrorCategory", "d:ErrorCategory"),
    }


def _map_failed_idoc_rows(results):
    return [_map_failed_idoc_row(item) for item in results]


def _parse_failed_idoc_cpi_response(raw_text: str) -> list:
    """Parse CPI /s4odata response (JSON OData or Atom XML) into UI rows."""
    stripped = (raw_text or "").strip()
    if not stripped:
        return []

    if stripped[0] in "{[":
        payload = json.loads(stripped)
        if isinstance(payload, list):
            return _map_failed_idoc_rows(payload)
        results = payload.get("d", {}).get("results")
        if isinstance(results, list):
            return _map_failed_idoc_rows(results)
        nested = payload.get("feed", {}).get("entry")
        if nested:
            return _parse_failed_idoc_atom_entries(nested)

    dict_data = xmltodict.parse(stripped)
    json_data = json.loads(json.dumps(dict_data))
    entries = json_data.get("feed", {}).get("entry")
    if entries:
        return _parse_failed_idoc_atom_entries(entries)
    return []


def _parse_failed_idoc_atom_entries(entries) -> list:
    if isinstance(entries, dict):
        entries = [entries]
    rows = []
    for entry in entries:
        props = entry.get("content", {}).get("m:properties", {})
        if props:
            rows.append(_map_failed_idoc_row(props))
    return rows


def _cpi_credentials(cfg: dict) -> Optional[hp.BasicAuth]:
    if cfg["bg_jobs_cpi_user"] and cfg["bg_jobs_cpi_password"]:
        return hp.BasicAuth(cfg["bg_jobs_cpi_user"], cfg["bg_jobs_cpi_password"])
    return None


async def _fetch_failed_idocs_via_cpi(
    client: hp.AsyncClient, cfg: dict
) -> list:
    """Same CPI iFlow as background-jobs, different OData service/entity."""
    if not cfg["failed_idocs_odata_base"]:
        logger.error(
            "failed-idocs CPI: set SAP_FAILED_IDOCS_ODATA_BASE_URL in .env"
        )
        return []
    if not cfg["bg_jobs_cpi_url"]:
        logger.error("failed-idocs CPI: set SAP_BACKGROUND_JOBS_CPI_URL in .env")
        return []
    auth = _cpi_credentials(cfg)
    if not auth:
        logger.error("failed-idocs CPI: set SAP_BACKGROUND_JOBS_CPI_USER/PASSWORD")
        return []

    body = {
        "baseUrl": cfg["failed_idocs_odata_base"],
        "entity": cfg["failed_idocs_entity"],
        "method": "GET",
        "queryParams": {"$format": "json", "$top": "1000"},
    }
    logger.info(
        "failed-idocs via CPI %s entity=%s",
        cfg["bg_jobs_cpi_url"],
        cfg["failed_idocs_entity"],
    )
    response = await client.post(
        cfg["bg_jobs_cpi_url"],
        json=body,
        headers={"Content-Type": "application/json"},
        auth=auth,
    )
    response.raise_for_status()
    idocs = _parse_failed_idoc_cpi_response(response.text)
    logger.info("failed-idocs CPI returned %s rows", len(idocs))
    return idocs


def _sap_basic_auth(cfg: dict) -> Optional[hp.BasicAuth]:
    if cfg["sap_username"] and cfg["sap_password"]:
        return hp.BasicAuth(cfg["sap_username"], cfg["sap_password"])
    return None


@monitoring_router.get("/failed-idocs")
async def call_failed_idoc():
    """Fetch failed IDocs from SAP APIM (env) or optional SAP_MONITORING_PROXY_BASE."""
    cfg = _load_monitoring_settings()
    idocs = []
    async with hp.AsyncClient(timeout=60.0) as client:
        try:
            if cfg["proxy_base"]:
                proxy_url = f"{cfg['proxy_base']}/failed-idocs"
                logger.info("failed-idocs via proxy: %s", proxy_url)
                response = await client.get(
                    proxy_url, headers={"Accept": "application/json"}
                )
                response.raise_for_status()
                data = response.json()
                if isinstance(data.get("result"), list):
                    logger.info("failed-idocs proxy returned %s rows", len(data["result"]))
                    return data
                results = data.get("d", {}).get("results", [])
                idocs = _map_failed_idoc_rows(results)
                logger.info("failed-idocs proxy (odata) returned %s rows", len(idocs))
                return {"result": idocs}

            use_cpi_first = cfg["failed_idocs_fetch_mode"] == "cpi"
            if use_cpi_first and cfg["failed_idocs_odata_base"]:
                idocs = await _fetch_failed_idocs_via_cpi(client, cfg)
                if idocs or use_cpi_first:
                    return {"result": idocs}

            if not cfg["failed_idocs_url"]:
                logger.error(
                    "failed-idocs: set SAP_FAILED_IDOCS_ODATA_BASE_URL (cpi) or "
                    "SAP_MONITORING_APIM_BASE / SAP_FAILED_IDOCS_URL (apim)"
                )
                return {"result": idocs}

            auth = _sap_basic_auth(cfg)
            if not auth:
                logger.error("failed-idocs APIM: SAP_USERNAME and SAP_PASSWORD required")
                return {"result": idocs}

            logger.info("failed-idocs direct APIM: %s", cfg["failed_idocs_url"])
            try:
                response = await client.get(
                    cfg["failed_idocs_url"],
                    auth=auth,
                    headers={"Accept-Encoding": "application/gzip"},
                )
                response.raise_for_status()
                data = response.json()
                results = data.get("d", {}).get("results", [])
                idocs = _map_failed_idoc_rows(results)
                logger.info("failed-idocs APIM returned %s rows", len(idocs))
            except hp.HTTPStatusError as apim_err:
                if (
                    apim_err.response.status_code == 404
                    and cfg["failed_idocs_odata_base"]
                ):
                    logger.warning(
                        "failed-idocs APIM 404 — falling back to CPI OData proxy"
                    )
                    idocs = await _fetch_failed_idocs_via_cpi(client, cfg)
                else:
                    raise
        except (hp.RequestError, hp.HTTPStatusError, ValueError, KeyError, TypeError) as e:
            logger.error("Error fetching failed idocs: %s", e)

    return {"result": idocs}


@monitoring_router.post("/retrigger-idocs")
async def retrigger_failed_idoc(request: dict = Body(...)):
    cfg = _load_monitoring_settings()
    iv_docnum = request.get("idocno")
    auth = _sap_basic_auth(cfg)
    if not auth or not cfg["retrigger_metadata_url"] or not cfg["retrigger_post_url"]:
        raise HTTPException(
            status_code=503,
            detail="Configure SAP_MONITORING_APIM_BASE and SAP credentials in .env",
        )

    async with hp.AsyncClient(timeout=30) as client:
        csrf_response = await client.get(
            cfg["retrigger_metadata_url"],
            auth=auth,
            headers={
                "Accept-Encoding": "application/gzip",
                "x-csrf-token": "fetch",
            },
        )
        csrf_token = csrf_response.headers.get("x-csrf-token")
        csrf_response.raise_for_status()

        response = await client.post(
            cfg["retrigger_post_url"],
            json={"IvDocnum": iv_docnum},
            auth=auth,
            headers={
                "Accept-Encoding": "application/gzip",
                "x-csrf-token": csrf_token,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    response.raise_for_status()
    results = response.json().get("d", {})
    return {
        "result": {
            "IvDocnum": results.get("IvDocnum"),
            "EvMessage": results.get("EvMessage"),
            "EvStatus": results.get("EvStatus"),
        }
    }


@monitoring_router.post("/retrigger-bulk-idocs")
async def bulk_failed_idoc_retrigger(request: dict = Body(...)):
    cfg = _load_monitoring_settings()
    auth = _sap_basic_auth(cfg)
    if not auth or not cfg["bulk_retrigger_metadata_url"] or not cfg["bulk_retrigger_post_url"]:
        raise HTTPException(
            status_code=503,
            detail="Configure SAP_MONITORING_APIM_V4_BASE and SAP credentials in .env",
        )

    async with hp.AsyncClient(timeout=30) as client:
        csrf_response = await client.get(
            cfg["bulk_retrigger_metadata_url"],
            auth=auth,
            headers={
                "Accept-Encoding": "application/gzip",
                "x-csrf-token": "fetch",
            },
        )
        csrf_token = csrf_response.headers.get("x-csrf-token")
        csrf_response.raise_for_status()

        body = ""
        for i in request.get("idocno") or []:
            body += (
                "--batch_001\r\n"
                "Content-Type: application/http\r\n"
                "Content-Transfer-Encoding: binary\r\n"
                "\r\n"
                "POST IdocReprocess/com.sap.gateway.srvd.zidoc_reprocess_sd.v0001.bulkRetriggerIdoc HTTP/1.1\r\n"
                "Content-Type: application/json\r\n"
                "\r\n"
                f'{{ "DOCNUM": "{i}" }}\r\n'
                "\r\n"
            )
        body += "--batch_001--"

        response = await client.post(
            cfg["bulk_retrigger_post_url"],
            data=body,
            auth=auth,
            headers={
                "Accept-Encoding": "gzip, deflate",
                "x-csrf-token": csrf_token,
                "Accept": "multipart/mixed",
                "Content-Type": "multipart/mixed; boundary=batch_001",
            },
        )

    response.raise_for_status()
    return {"result": response.text}


@monitoring_router.get("/background-jobs")
async def call_bg_jobs():
    """Fetch SAP background jobs via CPI (env) or optional SAP_MONITORING_PROXY_BASE."""
    cfg = _load_monitoring_settings()
    headers = {"Content-Type": "application/json"}
    job_data = []
    cet = ZoneInfo("Asia/Kolkata")
    now = datetime.now(tz=cet).replace(minute=0, second=0, microsecond=0)
    end_time = now.strftime("%H%M%S")
    one_hour_ago = now - timedelta(hours=1)
    sap_date = now.strftime("%Y-%m-%dT00:00:00")
    start_time = one_hour_ago.strftime("%H%M%S")
    logger.info("background-jobs window: %s || %s || %s", sap_date, start_time, end_time)

    async with hp.AsyncClient(timeout=60.0) as client:
        try:
            if cfg["proxy_base"]:
                proxy_url = f"{cfg['proxy_base']}/background-jobs"
                logger.info("background-jobs via proxy: %s", proxy_url)
                response = await client.get(
                    proxy_url, headers={"Accept": "application/json"}
                )
                response.raise_for_status()
                job_data = response.json()
                if isinstance(job_data, list):
                    logger.info("background-jobs proxy returned %s rows", len(job_data))
                    return job_data
                return job_data

            missing = [
                name
                for name, val in (
                    ("SAP_BACKGROUND_JOBS_CPI_URL", cfg["bg_jobs_cpi_url"]),
                    ("SAP_BACKGROUND_JOBS_CPI_USER", cfg["bg_jobs_cpi_user"]),
                    ("SAP_BACKGROUND_JOBS_CPI_PASSWORD", cfg["bg_jobs_cpi_password"]),
                    ("SAP_BACKGROUND_JOBS_ODATA_BASE_URL", cfg["bg_jobs_odata_base"]),
                )
                if not val
            ]
            if missing:
                logger.error(
                    "background-jobs: missing .env: %s (or set SAP_MONITORING_PROXY_BASE)",
                    ", ".join(missing),
                )
                return job_data

            body = {
                "baseUrl": cfg["bg_jobs_odata_base"],
                "entity": "Z_I_FA_JOBS",
                "method": "GET",
                "queryParams": {
                    "$top": "1000",
                    "$filter": (
                        f"ExecutionStartDate eq datetime'{sap_date}' "
                        f"and ExecutionStartTime ge '{start_time}' "
                        f"and ExecutionStartTime le '{end_time}'"
                    ),
                },
            }
            auth = hp.BasicAuth(cfg["bg_jobs_cpi_user"], cfg["bg_jobs_cpi_password"])

            response = await client.post(
                cfg["bg_jobs_cpi_url"], json=body, headers=headers, auth=auth
            )
            response.raise_for_status()
            dict_data = xmltodict.parse(response.text)
            json_data = json.loads(json.dumps(dict_data))
            job_data = normalize_jobs(json_data)
            logger.info("background-jobs returned %s rows", len(job_data))
        except (hp.RequestError, hp.HTTPStatusError, ValueError, KeyError, TypeError) as e:
            logger.error("Error fetching background jobs: %s", e)

    return job_data
