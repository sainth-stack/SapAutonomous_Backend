"""SAP monitoring endpoints used by Job Monitoring and Failed IDocs pages."""
import json
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx as hp
import xmltodict
from fastapi import APIRouter, Body, HTTPException

logger = logging.getLogger(__name__)

monitoring_router = APIRouter(tags=["SAP Monitoring"])

SAP_USERNAME = os.environ.get("SAP_USERNAME", "abaphana82")
SAP_PASSWORD = os.environ.get("SAP_PASSWORD", "welcome@82")

FAILED_IDOCS_URL = (
    "https://a107a740trial-trial.integrationsuitetrial-apim.us10.hana.ondemand.com"
    "/a107a740trial/retrigger-bulk-idoc/ZC_IDOC_FAILED_CDS/ZC_IDOC_FAILED?$format=json"
)
RETRIGGER_METADATA_URL = (
    "https://a107a740trial-trial.integrationsuitetrial-apim.us10.hana.ondemand.com"
    "/a107a740trial/retrigger-bulk-idoc/ZREPROCESS_IDOC_SRV_SRV/$metadata"
)
RETRIGGER_POST_URL = (
    "https://a107a740trial-trial.integrationsuitetrial-apim.us10.hana.ondemand.com"
    "/a107a740trial/retrigger-bulk-idoc/ZREPROCESS_IDOC_SRV_SRV/reprocess_idoc"
)
BULK_RETRIGGER_METADATA_URL = (
    "https://a107a740trial-trial.integrationsuitetrial-apim.us10.hana.ondemand.com"
    "/a107a740trial/retrigger-idoc/zidoc_reprocess_srv/srvd/sap/zidoc_reprocess_sd/0001/$metadata"
)
BULK_RETRIGGER_POST_URL = (
    "https://a107a740trial-trial.integrationsuitetrial-apim.us10.hana.ondemand.com"
    "/a107a740trial/retrigger-idoc/zidoc_reprocess_srv/srvd/sap/zidoc_reprocess_sd/0001/$batch"
)
BACKGROUND_JOBS_CPI_URL = (
    "https://a107a740trial.it-cpitrial05-rt.cfapps.us10-001.hana.ondemand.com/http/s4odata"
)


def normalize_jobs(data):
    cleaned = []
    entries = data.get("feed", {}).get("entry", [])
    if isinstance(entries, dict):
        entries = [entries]
    if not entries:
        return cleaned

    for e in entries:
        props = e["content"]["m:properties"]
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


@monitoring_router.get("/failed-idocs")
async def call_failed_idoc():
    async with hp.AsyncClient(timeout=30) as client:
        response = await client.get(
            FAILED_IDOCS_URL,
            auth=hp.BasicAuth(SAP_USERNAME, SAP_PASSWORD),
            headers={"Accept-Encoding": "application/gzip"},
        )

    response.raise_for_status()
    data = response.json()
    results = data.get("d", {}).get("results", [])
    idocs = [
        {
            "idoc_number": item["IDocNumber"],
            "status": item["IDocStatus"],
            "message_type": item["MessageType"],
            "status_text": item["StatusText"],
            "Direction": item["DirectionText"],
            "sender": item["SenderPartnerNumber"],
            "receiver": item["ReceiverPartnerNumber"],
            "sender_type": item["SenderPartnerType"],
            "receiver_type": item["ReceiverPartnerType"],
            "creation_date": item["CreatedDate"],
            "creation_time": item["CreatedTime"],
            "last_updated_date": item["LastUpdatedDate"],
            "last_updated_time": item["LastUpdatedTime"],
            "error_category": item["ErrorCategory"],
        }
        for item in results
    ]
    return {"result": idocs}


@monitoring_router.post("/retrigger-idocs")
async def retrigger_failed_idoc(request: dict = Body(...)):
    iv_docnum = request.get("idocno")
    logger.info("SAP User: %s", SAP_USERNAME)

    async with hp.AsyncClient(timeout=30) as client:
        csrf_response = await client.get(
            RETRIGGER_METADATA_URL,
            auth=hp.BasicAuth(SAP_USERNAME, SAP_PASSWORD),
            headers={
                "Accept-Encoding": "application/gzip",
                "x-csrf-token": "fetch",
            },
        )
        csrf_token = csrf_response.headers.get("x-csrf-token")
        csrf_response.raise_for_status()

        response = await client.post(
            RETRIGGER_POST_URL,
            json={"IvDocnum": iv_docnum},
            auth=hp.BasicAuth(SAP_USERNAME, SAP_PASSWORD),
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
    logger.info("SAP User: %s", SAP_USERNAME)

    async with hp.AsyncClient(timeout=30) as client:
        csrf_response = await client.get(
            BULK_RETRIGGER_METADATA_URL,
            auth=hp.BasicAuth(SAP_USERNAME, SAP_PASSWORD),
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
            BULK_RETRIGGER_POST_URL,
            data=body,
            auth=hp.BasicAuth(SAP_USERNAME, SAP_PASSWORD),
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
    headers = {"Content-Type": "application/json"}
    cet = ZoneInfo("Asia/Kolkata")
    now = datetime.now(tz=cet)
    sap_date = now.strftime("%Y-%m-%dT00:00:00")
    start_time = "000000"
    end_time = now.strftime("%H%M%S")
    body = {
        "baseUrl": "http://bainocularsapai.com:8000/sap/opu/odata/sap/ZSB_JOB_MONITOR",
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
    auth = hp.BasicAuth(
        "sb-98017383-8d5f-4ff8-b27e-418e4b17372b!b674382|it-rt-a107a740trial!b26655",
        "cb3230a1-c7be-43d3-b2c2-267de2f23033$ht8cAHHFzignC0Ej4iTiqNwtY3gWQyCEG5VvHO8NJdQ=",
    )

    async with hp.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(
                BACKGROUND_JOBS_CPI_URL, json=body, headers=headers, auth=auth
            )
            response.raise_for_status()
            dict_data = xmltodict.parse(response.text)
            json_data = json.loads(json.dumps(dict_data))
            return normalize_jobs(json_data)
        except (hp.RequestError, hp.HTTPStatusError, ValueError, KeyError, TypeError) as e:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to fetch background jobs from SAP: {e}",
            ) from e
