from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional, List
from datetime import datetime
import requests
import json
import os
import re
import base64
import logging
from pathlib import Path
from openai import OpenAI
from bainocular_configuration import ConfigParams
logger = logging.getLogger(__name__)

# Load .env file manually — works regardless of how uvicorn is started
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    loaded = []
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _, _val = _line.partition("=")
            _val = _val.strip().strip('"').strip("'")
            os.environ.setdefault(_key.strip(), _val)
            if _key.strip() in ("SAP_USERNAME", "SAP_PASSWORD", "SAP_S4_BASE_URL", "SAP_CLIENT"):
                loaded.append(_key.strip())
    logger.info("Loaded .env from %s; SAP vars loaded: %s", _env_path, loaded)
else:
    logger.warning(".env not found at %s", _env_path)

DEFAULT_LLM_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Initialize Router
sap_router = APIRouter()

# Lazy OpenAI client - initialized on first use so env vars are loaded first
_openai_client = None

def get_openai_client():
    global _openai_client
    if _openai_client is None:
        #api_key = os.getenv("OPENAI_API_KEY")
        api_key = ConfigParams.openai_api_key
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable is not set")
        _openai_client = OpenAI(api_key=api_key)
    return _openai_client

# Configuration - SAP OData API
SAP_S4_BASE_URL = os.getenv(
    "SAP_S4_BASE_URL",
    "https://seleccionapidev.test02.apimanagement.us10.hana.ondemand.com",
)
SAP_USERNAME = os.getenv("SAP_USERNAME")
SAP_PASSWORD = os.getenv("SAP_PASSWORD")
SAP_CLIENT = os.getenv("SAP_CLIENT", "100")
SAP_USERNAME_FORMAT = os.getenv("SAP_USERNAME_FORMAT", "user")
# OData path prefix — change to match your SAP system:
#   Standard SAP Gateway : /sap/opu/odata/sap
#   SAP API Management   : /odata  (or leave empty if base URL already includes the path)
SAP_ODATA_PREFIX = os.getenv("SAP_ODATA_PREFIX", "/sap/opu/odata/sap")
SAP_BTP_API = os.getenv("SAP_BTP_API", "https://api.sap.com/btp")
SAP_BATCH_API = os.getenv("SAP_BATCH_API", "https://api.sap.com/batch")

# Local cache: all sales orders fetched from SAP and stored in JSON
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SALES_ORDERS_JSON = DATA_DIR / "sales_orders.json"

# Sample schema for Sales Order (A_SalesOrder) — used by LLM to generate correct $filter/$orderby
SAMPLE_SALES_ORDER_SCHEMA = """
Sales Order entity (A_SalesOrder) — OData fields:
- SalesOrder: string (key, e.g. "1", "2")
- SalesOrderType: string (e.g. "OR", "ZOR")
- SoldToParty: string (sold-to customer)
- CreationDate: date
- LastChangeDate: date
- SalesOrderDate: date
- TotalNetAmount: string (decimal amount, e.g. "100.00")
- TransactionCurrency: string (e.g. "EUR", "USD")
- OverallSDProcessStatus: string
- TotalTaxAmount: string
"""

# Log SAP config at startup (no credentials)
logger.info(
    "SAP config: base_url=%s odata_prefix=%s client=%s username_set=%s password_set=%s",
    SAP_S4_BASE_URL,
    SAP_ODATA_PREFIX,
    SAP_CLIENT,
    bool(SAP_USERNAME),
    bool(SAP_PASSWORD),
)
logger.info("SAP LLM model: %s", DEFAULT_LLM_MODEL)


# --- Pydantic Models ---

class SapQueryRequest(BaseModel):
    query: str
    email: Optional[str] = None

class SapExternalQueryRequest(BaseModel):
    query: str
    mode: str  # 'btp' or 'batch'
    email: Optional[str] = None


# --- Helper Functions ---

def call_llm_with_usage(model: Optional[str], messages: List[Dict[str, str]], temperature: Optional[float] = 0.0, email: Optional[str] = None):
    """
    Wrapper for OpenAI Chat Completion.
    """
    try:
        response = get_openai_client().chat.completions.create(
            model=model or DEFAULT_LLM_MODEL,
            messages=messages,
            temperature=temperature or 0.0,
        )
        return response
    except Exception as e:
        print(f"Error calling LLM: {e}")
        raise e

def _format_sap_date(val: Any) -> Any:
    """Convert SAP /Date(ts)/ to ISO date string."""
    if isinstance(val, str) and val.startswith("/Date(") and val.endswith(")/"):
        try:
            ts = int(val.replace("/Date(", "").replace(")/", "").split("+")[0].split("-")[0])
            return datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            pass
    return val

"""This method transforms the output received from api proxy into user friendly format using LLM"""
def _generate_friendly_answer(user_query: str, sap_data: Dict[str, Any], email: Optional[str] = None) -> str:
    """Use LLM to turn raw SAP data into a natural, user-friendly answer based on the user's question."""
    d = sap_data.get("d", {})
    results = d.get("results", [])
    total_count = d.get("totalCount", len(results))

    if not results and total_count == 0:
        return "<p>No data was found for your request.</p>"

    system_prompt = """You are an S/4HANA assistant. The user asked a question about SAP data. Below you will get the data (and possibly a totalCount).

Your job: Answer the user's question directly and clearly using clean HTML. Use ONLY: <p>, <strong>, <span>, <ul>, <li>, <br/>.
- If the user asked for TOTAL NUMBER, COUNT, or HOW MANY (e.g. "total number of sales orders", "how many orders"): answer with that number first, e.g. <p>The total number of sales orders is <strong>26</strong>.</p> Use the totalCount value if provided.
- Use <strong> for key values (amounts, IDs, dates, counts).
- For lists: keep it short (e.g. first few items + "and X more" if many), or just state the count when that's what they asked.
- Format currencies with symbol (€, $). Be positive and helpful. No raw JSON or technical field names.
Return ONLY the HTML, no markdown, no code blocks."""

    # For "count" / "how many" questions or large lists, send totalCount + sample so LLM answers clearly
    max_results_in_prompt = 15
    if total_count > max_results_in_prompt or any(kw in user_query.lower() for kw in ("total number", "how many", "count", "number of")):
        payload_for_llm = {"totalCount": total_count, "sample": results[:5]}
    else:
        payload_for_llm = {"totalCount": total_count, "results": results}

    user_message = f"""User asked: "{user_query}"

SAP data (totalCount = number of records):
{json.dumps(payload_for_llm, indent=2, default=str)}

Return a friendly HTML answer. Use <p>, <strong>, <ul>, <li> only. If they asked for total/count, say the number clearly."""

    try:
        llm_resp = call_llm_with_usage(
            model=DEFAULT_LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.3,
            email=email,
        )
        answer = llm_resp.choices[0].message.content if llm_resp and llm_resp.choices else ""
        raw = (answer or "").strip()
        # Strip markdown code blocks if present
        if raw.startswith("```"):
            raw = re.sub(r"^```\w*\n?", "", raw)
            raw = re.sub(r"\n?```\s*$", "", raw)
        # Wrap plain text in <p> if no HTML tags
        if raw and not raw.strip().startswith("<"):
            raw = f"<p>{raw.replace(chr(10), '<br/>')}</p>"
        return raw or "<p>Here is the data for your request.</p>"
    except Exception as e:
        logger.warning("Friendly answer LLM call failed: %s", e)
        return ""


def _normalize_sap_response(sap_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize SAP OData response so single entity and list both have d.results array."""
    d = sap_payload.get("d")
    if not d:
        return sap_payload
    # List format: d.results exists
    if "results" in d:
        rows = d["results"]
    else:
        # Single entity: d is the object itself
        rows = [d]
    # Format dates and strip __metadata from each row for display
    display_rows = []
    for row in rows:
        if not isinstance(row, dict):
            display_rows.append(row)
            continue
        cleaned = {}
        for k, v in row.items():
            if k == "__metadata":
                continue
            cleaned[k] = _format_sap_date(v) if isinstance(v, str) and "/Date(" in str(v) else v
        display_rows.append(cleaned)
    return {
        "d": {"results": display_rows, "totalCount": len(display_rows)},
        "url_used": sap_payload.get("url_used"),
        "intent_debug": sap_payload.get("intent_debug"),
    }


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """
    Extracts the first JSON object from a string.
    """
    try:
        # Find JSON string within text (handles code blocks too)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            json_str = match.group(0)
            return json.loads(json_str)
        return None
    except json.JSONDecodeError:
        return None

def _build_odata_url(intent: Dict[str, Any]) -> str:
    """
    Constructs the SAP OData URL based on the intent.
    Format: {base}/odata/{service}/{entity} or {base}/odata/{service}/{entity}('{value}')
    """
    service = intent.get("service")
    entity = intent.get("entity")
    value = intent.get("value")
    input_filter = intent.get("$filter")
    top = intent.get("$top")
    orderby = intent.get("$orderby")

    if not service or not entity:
        raise ValueError("Service and Entity are required to build OData URL")

    url = f"{SAP_S4_BASE_URL.rstrip('/')}{SAP_ODATA_PREFIX}/{service}/{entity}"

    # Handle single entity by key (if value is not "0" and not None)
    if value and value != "0":
        url = f"{url}('{value}')"

    params = []
    if input_filter:
        params.append(f"$filter={input_filter}")
    if orderby:
        params.append(f"$orderby={orderby}")
    # Default $top for GET_LIST when not set (e.g. "all" list) so we get more than SAP's default page
    if top:
        params.append(f"$top={top}")
    elif intent.get("operation") == "GET_LIST":
        params.append("$top=1000")
    params.append("$format=json")
    url += "?" + "&".join(params)
    return url


def _get_sap_auth_headers() -> Dict[str, str]:
    """Build headers for SAP API - Basic auth + sap-usercontext (header and Cookie for compatibility)."""
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "application/gzip",
        "sap-usercontext": f"sap-client={SAP_CLIENT}",
        "Cookie": f"sap-usercontext=sap-client={SAP_CLIENT}",
    }
    if SAP_USERNAME and SAP_PASSWORD:
        auth_user = f"{SAP_USERNAME}@{SAP_CLIENT}" if SAP_USERNAME_FORMAT == "user@client" else SAP_USERNAME
        creds = base64.b64encode(f"{auth_user}:{SAP_PASSWORD}".encode()).decode()
        headers["Authorization"] = f"Basic {creds}"
        logger.info("SAP auth: user=%s client=%s format=%s", auth_user, SAP_CLIENT, SAP_USERNAME_FORMAT)
    else:
        logger.warning("SAP auth: SAP_USERNAME=%s, SAP_PASSWORD=%s", bool(SAP_USERNAME), bool(SAP_PASSWORD))
    return headers


def _fetch_and_save_sales_orders() -> None:
    """Fetch all sales orders from SAP (A_SalesOrder) and save to data/sales_orders.json."""
    if not SAP_USERNAME or not SAP_PASSWORD:
        logger.warning("SAP credentials not set; skipping sales orders fetch")
        return
    url = (
        f"{SAP_S4_BASE_URL.rstrip('/')}{SAP_ODATA_PREFIX}/API_SALES_ORDER_SRV/A_SalesOrder"
        "?$top=1000&$format=json"
    )
    try:
        response = requests.get(url, headers=_get_sap_auth_headers(), timeout=30)
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("d", {}).get("results", [])
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(SALES_ORDERS_JSON, "w") as f:
            json.dump({"d": {"results": rows}, "fetched_at": datetime.utcnow().isoformat()}, f, indent=2, default=str)
        logger.info("Saved %d sales orders to %s", len(rows), SALES_ORDERS_JSON)
    except Exception as e:
        logger.warning("Failed to fetch/save sales orders: %s", e)


def _ensure_sales_orders_cached() -> None:
    """Ensure data/sales_orders.json exists; fetch from SAP if missing."""
    if not SALES_ORDERS_JSON.exists():
        _fetch_and_save_sales_orders()


def _load_sales_orders_from_json() -> Optional[List[Dict[str, Any]]]:
    """Load sales orders from local JSON; return None if file missing or invalid."""
    if not SALES_ORDERS_JSON.exists():
        return None
    try:
        with open(SALES_ORDERS_JSON) as f:
            data = json.load(f)
        return data.get("d", {}).get("results", [])
    except Exception as e:
        logger.warning("Failed to load sales_orders.json: %s", e)
        return None


def _apply_intent_to_sales_orders(
    rows: List[Dict[str, Any]], intent: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Apply $orderby and $top from intent to a list of sales order rows. Strip __metadata."""
    result = []
    for row in rows:
        if not isinstance(row, dict):
            result.append(row)
            continue
        cleaned = {k: _format_sap_date(v) if isinstance(v, str) and "/Date(" in str(v) else v for k, v in row.items() if k != "__metadata"}
        result.append(cleaned)
    orderby = intent.get("$orderby")
    if orderby:
        parts = orderby.strip().split()
        field = parts[0]
        desc = len(parts) > 1 and parts[1].lower() == "desc"
        def key(r):
            v = r.get(field)
            if v is None:
                return "" if not desc else "\uffff"
            try:
                return float(str(v).replace(",", "."))
            except (ValueError, TypeError):
                return str(v)
        result.sort(key=key, reverse=desc)
    top = intent.get("$top")
    if top:
        try:
            n = int(top)
            result = result[:n]
        except (ValueError, TypeError):
            pass
    return result


# --- Endpoints ---

@sap_router.post("/api/sap/query")
async def query_sap(request: SapQueryRequest):
    user_input = (request.query or "").strip()
    if not user_input:
        raise HTTPException(status_code=400, detail="query is required")

    # 1) Ensure we have all sales orders in a JSON file (fetch from SAP if missing)
    _ensure_sales_orders_cached()

    # 2) Send sample schema + user query to LLM to generate the query (intent)
    parser_prompt = """
You are an enterprise integration assistant. You will receive a SAMPLE SCHEMA of available SAP entities and a USER QUERY.
Your job is to output a single JSON object that describes how to execute the query (service, entity, filters, order, limit).

Sample schema of available data:
""" + SAMPLE_SALES_ORDER_SCHEMA + """

Standard SAP OData mapping:
- Sales Order -> API_SALES_ORDER_SRV -> A_SalesOrder
- Sales Order Item -> API_SALES_ORDER_SRV -> A_SalesOrderItem (NetAmount, SalesOrder, SalesOrderItem)
- Purchase Order -> API_PURCHASEORDER_PROCESS_SRV -> A_PurchaseOrder

Rules:
- For a single entity (e.g. "sales order 4"), set value to the identifier ("4"), operation GET_SINGLE.
- For lists, set value = "0", operation GET_LIST.
- For "all" / "full list" / "give all" / "every" (no number), use GET_LIST and "$top" = "1000".
- Use valid OData $filter syntax only (no "$filter=" prefix). Use $orderby with field name and "asc" or "desc".
- For "top N" / "first N" / "N sales orders", set "$top" to that number (e.g. "5").
- For "max" / "highest" amount: entity A_SalesOrderItem, "$orderby": "NetAmount desc", "$top": "1", value "0", GET_LIST.
- For "min" / "lowest" amount: "$orderby": "NetAmount asc", "$top": "1".
- Prefer Sales Order when ambiguous.

Return ONLY this JSON (no markdown, no extra text):
{
  "service": "...",
  "entity": "...",
  "value": "...",
  "operation": "GET_SINGLE|GET_LIST",
  "$filter": "... or null",
  "$top": "... or null",
  "$orderby": "... or null"
}
""".strip()

    intent: Dict[str, Any]
    try:
        llm_resp = call_llm_with_usage(
            model=DEFAULT_LLM_MODEL,
            messages=[
                {"role": "system", "content": parser_prompt},
                {"role": "user", "content": f"User query: {user_input}"},
            ],
            temperature=0.0,
            email=request.email,
        )
        llm_text = llm_resp.choices[0].message.content if llm_resp and llm_resp.choices else ""
        parsed = _extract_json_object(llm_text)
        if not parsed:
            raise HTTPException(
                status_code=502,
                detail="LLM did not return valid JSON for SAP intent parsing.",
            )
        intent = parsed
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SAP intent generation failed: {str(exc)}")

    # 3) Execute: use cached sales orders JSON when possible (A_SalesOrder GET_LIST), else call SAP
    sap_payload: Dict[str, Any]
    if (
        intent.get("entity") == "A_SalesOrder"
        and intent.get("operation") == "GET_LIST"
    ):
        rows = _load_sales_orders_from_json()
        if rows is not None:
            result_rows = _apply_intent_to_sales_orders(rows, intent)
            sap_payload = {
                "d": {"results": result_rows, "totalCount": len(result_rows)},
                "url_used": f"local:{SALES_ORDERS_JSON}",
                "intent_debug": intent,
            }
        else:
            sap_payload = {
                "d": {"results": [], "totalCount": 0},
                "url_used": f"local:{SALES_ORDERS_JSON}",
                "intent_debug": intent,
            }
    elif intent.get("entity") == "A_SalesOrder" and intent.get("operation") == "GET_SINGLE":
        single_id = intent.get("value")
        rows = _load_sales_orders_from_json()
        sap_payload = None
        if rows is not None:
            if single_id:
                single_row = next((r for r in rows if str(r.get("SalesOrder")) == str(single_id)), None)
                if single_row is not None:
                    cleaned = {k: _format_sap_date(v) if isinstance(v, str) and "/Date(" in str(v) else v for k, v in single_row.items() if k != "__metadata"}
                    sap_payload = {"d": {"results": [cleaned], "totalCount": 1}, "url_used": f"local:{SALES_ORDERS_JSON}", "intent_debug": intent}
                else:
                    sap_payload = {"d": {"results": [], "totalCount": 0}, "url_used": f"local:{SALES_ORDERS_JSON}", "intent_debug": intent}
            else:
                sap_payload = {"d": {"results": [], "totalCount": 0}, "url_used": f"local:{SALES_ORDERS_JSON}", "intent_debug": intent}
        if sap_payload is None:
            if not SAP_USERNAME or not SAP_PASSWORD:
                raise HTTPException(status_code=500, detail="SAP credentials required for single sales order when cache missing")
            request_url = _build_odata_url(intent)
            headers = _get_sap_auth_headers()
            response = requests.get(request_url, headers=headers, timeout=20)
            response.raise_for_status()
            sap_payload = response.json()
            sap_payload["url_used"] = request_url
            sap_payload["intent_debug"] = intent
            sap_payload = _normalize_sap_response(sap_payload)
    else:
        if not SAP_USERNAME or not SAP_PASSWORD:
            raise HTTPException(
                status_code=500,
                detail="SAP_USERNAME and SAP_PASSWORD must be set for SAP API authentication",
            )
        try:
            request_url = _build_odata_url(intent)
            headers = _get_sap_auth_headers()
            logger.info("SAP request: GET %s", request_url)
            response = requests.get(request_url, headers=headers, timeout=20)
            logger.info("SAP response: status=%d", response.status_code)
            if response.status_code >= 400:
                err_body = response.text[:500] if response.text else "(empty)"
                logger.error("SAP error: status=%d body=%s", response.status_code, err_body)
            response.raise_for_status()
            sap_payload = response.json()
            sap_payload["url_used"] = request_url
            sap_payload["intent_debug"] = intent
            sap_payload = _normalize_sap_response(sap_payload)
        except requests.RequestException as exc:
            logger.exception("SAP request failed: %s", exc)
            detail = str(exc)
            if hasattr(exc, "response") and exc.response is not None:
                try:
                    err_body = exc.response.text[:800] if exc.response.text else ""
                    if err_body:
                        detail = f"{detail}. Response: {err_body}"
                except Exception:
                    pass
            raise HTTPException(status_code=502, detail=f"SAP request failed: {detail}")
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Error building URL or processing: {str(exc)}")

    # 4) Send answer + user query to LLM to get a friendly answer
    friendly_answer = ""
    try:
        friendly_answer = _generate_friendly_answer(user_input, sap_payload, email=request.email)
    except Exception as exc:
        logger.warning("LLM friendly answer failed: %s", exc)
    if not friendly_answer:
        friendly_answer = "<p>Here is the data for your request.</p>"

    q = user_input.lower()
    show_table = any(kw in q for kw in ("list", "table", "tabular", "in list", "in table", "as list", "as table", "show all", "display as"))

    return {
        "input": user_input,
        "friendlyAnswer": friendly_answer,
        "showTable": show_table,
        "response": sap_payload,
    }


@sap_router.post("/api/sap/sales-orders/refresh")
async def refresh_sales_orders():
    """Fetch all sales orders from SAP and save to data/sales_orders.json."""
    if not SAP_USERNAME or not SAP_PASSWORD:
        raise HTTPException(status_code=500, detail="SAP credentials required to refresh sales orders")
    try:
        _fetch_and_save_sales_orders()
        return {"ok": True, "path": str(SALES_ORDERS_JSON)}
    except Exception as exc:
        logger.exception("Refresh sales orders failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))


@sap_router.post("/api/sap/query-external")
async def query_sap_external(request: SapExternalQueryRequest):
    """
    For BTP and Batch modes: fetch data from external API, send to LLM with user query,
    return LLM answer.
    """
    user_input = (request.query or "").strip()
    mode = (request.mode or "").lower()
    if not user_input:
        raise HTTPException(status_code=400, detail="query is required")
    if mode not in ("btp", "batch"):
        raise HTTPException(status_code=400, detail="mode must be 'btp' or 'batch'")

    api_url = SAP_BTP_API if mode == "btp" else SAP_BATCH_API

    try:
        resp = requests.get(api_url, timeout=30)
        resp.raise_for_status()
        external_data = resp.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch from {mode} API: {str(exc)}")

    system_prompt = """You are an assistant that helps users understand and analyze SAP data.

Below is the raw data fetched from an external SAP API. The user has asked a question about this data.

Your task:
1. Analyze the provided data
2. Answer the user's question based on the data
3. If the data is complex (e.g. nested objects, arrays), summarize key insights and answer clearly
4. Use the data to support your answer. If the question cannot be answered from the data, say so
5. Format your response in clear, readable text. Use bullet points or short paragraphs where helpful."""

    user_message = f"""Data from SAP {mode.upper()} API:

```json
{json.dumps(external_data, indent=2, default=str)}
```

User question: {user_input}

Please answer the user's question based on the data above."""

    try:
        llm_resp = call_llm_with_usage(
            model=DEFAULT_LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.5,
            email=request.email,
        )
        answer = (
            llm_resp.choices[0].message.content if llm_resp and llm_resp.choices else ""
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM processing failed: {str(exc)}")

    return {
        "input": user_input,
        "mode": mode,
        "answer": answer,
        "data_source": api_url,
    }
