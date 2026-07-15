import os
import json
from fastapi import FastAPI, Request, UploadFile, File, Depends, Form
from fastapi.middleware.cors import CORSMiddleware
from openai.types.responses.response_code_interpreter_tool_call import Output
from ticket_src.classification_src import get_data, get_data_gcp, insert_data, predict_sentence
from sqlalchemy.orm import Session
from database_gcp import Base, engine, get_db, get_postgres_db_conn
import uvicorn
import pandas as pd
import numpy as np
from datetime import date, datetime, timedelta
import xmltodict
from zoneinfo import ZoneInfo
from ticket_src.ams_kedb import extract_text_from_image, get_context_tickets, get_embedding, add_log, process_user_query, save_embeddings_to_gcp_db, summarize_result, process_file
from openai import OpenAI
from fastapi import Body, FastAPI, Query, Request, UploadFile, File, APIRouter, HTTPException
from typing import List, Optional, cast, Annotated
from ticket_src.free_text_analysis import generate_sql, get_classified_ticket_database, insert_table_replica_values, run_sql_query, replica_chatbot, get_database_to_file, process_llm_with_file
import httpx as hp
import io
try:
    import psycopg2
except ImportError:
    psycopg2 = None
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker, declarative_base
from database_gcp import Base, engine, get_db, get_postgres_db_conn
from models import SlaTicketData, SAPUser 
from schemas import SlaTicketCreate
import sys
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)
from db import get_db_connection
from bainocular_configuration import ConfigParams
from cryptography.fernet import Fernet
import logging
from api.log_api import add_user_log

app = APIRouter()

DbSession = Annotated[Session, Depends(get_db)]
DbSessionPostgres = Annotated[Session, Depends(get_postgres_db_conn)]
DbSessionVector = Annotated[Session, Depends(get_db_connection)]
					 

FERNET_KEY = os.environ.get("FERNET_KEY")
cipher = Fernet(FERNET_KEY.encode()) if FERNET_KEY else None

client = OpenAI(api_key=ConfigParams.openai_api_key)

logger = logging.getLogger(__name__)
EXCEL_FILE_DIR = "/tmp/excel_files"
if not os.path.exists(EXCEL_FILE_DIR):
    os.makedirs(EXCEL_FILE_DIR, exist_ok=True)



def decrypt_password(encrypted_password: str) -> str:
    if cipher is None:
        return encrypted_password
    return cipher.decrypt(encrypted_password.encode()).decode()

@app.post("/maintain_tickets")
async def handle_ticket_data(conn: DbSessionPostgres, db: DbSession, file:UploadFile = File(...), email: Optional[str] = Form(None)):
    try:
        contents = await file.read()
        file_name = (file.filename or "").lower()
        await add_user_log("DataSource", "SLA Input File", email or "", "S", f"user uploaded file {file_name}")
        print(f"filename:{file_name}")
        if file_name.endswith(".csv"):
            df2 = pd.read_csv(io.BytesIO(contents))
        elif file_name.endswith((".xlsx", ".xls")):
            df2 = pd.read_excel(io.BytesIO(contents))
        else:
            await add_user_log("DataSource", "SLA Input File", email or "", "E", f"user uploaded unsupported file format")
            return{
                        "error": "Unsupported file format"
                    }
            
        print(df2.columns)
        df1 = pd.read_sql_query("SELECT * FROM sla_tickets_data", conn)
        df1 =   df1[[
                "ReqCreationDate",
                "CreationTime",
                "ReqCreationDateYearWeekISO",
                "RequestID",
                "RequestPriorityDescription",
                "HistoricalStatusStatusFrom",
                "HistoricalStatusStatusTo",
                "HistoricalStatusChangeDate",
                "HistoricalStatusChangeTime",
                "MacroAreaName",
                "RequestResourceAssignedToGROUPSAPMD",
                "MacroArea",
                "RequestUserName",
                "RequestResourceAssignedToName",
                "ReqTypeDescription",
                "ReqStatusDescription",
                "ReqClosingDate",
                "RequestTextRequest",
                "RequestTextAnswer",
                "RequestCategory",
                "RequestSubjectdescription",
                "ResolSLA",
                "RespSLA",
                "ReqComp",
                "ReqCrDtConc",
                "EnDtConc",
                "HistoricalChangeDateTimeConc",
                "ElapsedTime",
                "CalcPreDt",
                "RefinedPreDt",
                "CalcStDt",
                "RefinedStDt",
                "Cumulative",
                "ResolSow",
                "RespSow",
                "ResolutionRem",
                "RespRem",
                "Rollover"
            ]]
        
        COLUMN_MAP = {
            # IDs
            "Request - ID": "RequestID",

            # Dates / Times
            "Req. Creation Date": "ReqCreationDate",
            "Creation Time": "CreationTime",
            "Req. Creation Date - Year Week ISO": "ReqCreationDateYearWeekISO",
            "Historical Status - Change Date": "HistoricalStatusChangeDate",
            "Historical Status - Change Time": "HistoricalStatusChangeTime",
            "Req. Closing Date": "ReqClosingDate",
            "ReqCrDtConc": "ReqCrDtConc",
            "HisChDtTiConc": "HistoricalChangeDateTimeConc",

            # Priority / Status
            "Request - Priority Description": "RequestPriorityDescription",
            "Req Status - Description": "ReqStatusDescription",
            "Req Type - Description EN": "ReqTypeDescription",

            # Historical Status
            "Historical Status - Status From": "HistoricalStatusStatusFrom",
            "Historical Status - Status To": "HistoricalStatusStatusTo",

            # Area / Assignment
            "Macro Area - Name": "MacroAreaName",
            "Macro Area (SAP) - MA Area": "MacroArea",
            "Request - Resource Assigned To - GROUP SAP MD": "RequestResourceAssignedToGROUPSAPMD",
            "Request - Resource Assigned To - Name": "RequestResourceAssignedToName",

            # User Info
            "Request - User Name": "RequestUserName",

            # Text fields
            "Request - Text Request": "RequestTextRequest",
            "Request - Text Answer": "RequestTextAnswer",
            "Request - Category": "RequestCategory",
            "Request - Subject description": "RequestSubjectdescription",

            # SLA fields
            "ResolSLA": "ResolSLA",
            "RespSLA": "RespSLA",
            "ResolSOW": "ResolSow",
            "RespSOW": "RespSow",
            "ResolRem": "ResolutionRem",
            "RespRem": "RespRem",

            # Metrics / Calculations
            "ReqComp": "ReqComp",
            "ElapsedTime": "ElapsedTime",
            "CalcPreDt": "CalcPreDt",
            "RefinedPreDt": "RefinedPreDt",
            "CalcStDt": "CalcStDt",
            "RefinedStDt": "RefinedStDt",
            "Cumilative": "Cumulative",

            # Other
            "Rollover": "Rollover",
            "DateRollover": "DateRollover",
            "DateReqCrYM": "ReqCrYM"
        }

        DB_TO_FILE_MAP = {
            "RequestID": "Request - ID",

            # Dates / Times
            "ReqCreationDate": "Req. Creation Date",
            "CreationTime": "Creation Time",
            "ReqCreationDateYearWeekISO": "Req. Creation Date - Year Week ISO",
            "HistoricalStatusChangeDate": "Historical Status - Change Date",
            "HistoricalStatusChangeTime": "Historical Status - Change Time",
            "ReqClosingDate": "Req. Closing Date",
            "ReqCrDtConc": "ReqCrDtConc",
            "HistoricalChangeDateTimeConc": "HisChDtTiConc",

            # Priority / Status
            "RequestPriorityDescription": "Request - Priority Description",
            "ReqStatusDescription": "Req Status - Description",
            "ReqTypeDescription": "Req Type - Description EN",

            # Historical Status
            "HistoricalStatusStatusFrom": "Historical Status - Status From",
            "HistoricalStatusStatusTo": "Historical Status - Status To",

            # Area / Assignment
            "MacroAreaName": "Macro Area - Name",
            "MacroArea": "Macro Area (SAP) - MA Area",
            "RequestResourceAssignedToGROUPSAPMD": "Request - Resource Assigned To - GROUP SAP MD",
            "RequestResourceAssignedToName": "Request - Resource Assigned To - Name",

            # User Info
            "RequestUserName": "Request - User Name",

            # Text fields
            "RequestTextRequest": "Request - Text Request",
            "RequestTextAnswer": "Request - Text Answer",
            "RequestCategory": "Request - Category",
            "RequestSubjectdescription": "Request - Subject description",

            # SLA fields
            "ResolSLA": "ResolSLA",
            "RespSLA": "RespSLA",
            "ResolSow": "ResolSOW",
            "RespSow": "RespSOW",
            "ResolutionRem": "ResolRem",
            "RespRem": "RespRem",

            # Metrics / Calculations
            "ReqComp": "ReqComp",
            "ElapsedTime": "ElapsedTime",
            "CalcPreDt": "CalcPreDt",
            "RefinedPreDt": "RefinedPreDt",
            "CalcStDt": "CalcStDt",
            "RefinedStDt": "RefinedStDt",
            "Cumulative": "Cumilative",

            # Other
            "Rollover": "Rollover",
            "DateRollover": "DateRollover",
            "ReqCrYM": "DateReqCrYM"
        }
        df1 = df1.rename(columns=DB_TO_FILE_MAP)
         # ── Your exact reconciliation logic ──────────────────────────────────────
        ids_in_file2 = set(df2["Request - ID"].dropna().unique())
        ids_in_file1 = set(df1["Request - ID"].dropna().unique())
        missing_ids  = ids_in_file1 - ids_in_file2

        if not missing_ids:
            final_df = df2.copy()
            summary = {
                "file1_ids": len(ids_in_file1),
                "file2_ids": len(ids_in_file2),
                "missing": 0,
                "total_output_rows": len(final_df),
            }
        else:
            missing_rows = df1[df1["Request - ID"].isin(missing_ids)].copy()
            final_df = pd.concat([df2, missing_rows], ignore_index=True)
            summary = {
                "file1_ids": len(ids_in_file1),
                "file2_ids": len(ids_in_file2),
                "missing": len(missing_ids),
                "appended_rows": len(missing_rows),
                "total_output_rows": len(final_df),
            }

        print(f"[INFO] Reconciliation summary: {summary}")
        print(final_df.columns)

        columns = ['Req. Creation Date', 'Creation Time',
            'Req. Creation Date - Year Week ISO', 'Request - ID',
            'Request - Priority Description', 'Historical Status - Status From',
            'Historical Status - Status To', 'Historical Status - Change Date',
            'Historical Status - Change Time', 'Macro Area - Name',
            'Request - Resource Assigned To - GROUP SAP MD',
            'Macro Area (SAP) - MA Area', 'Request - User Name',
            'Request - Resource Assigned To - Name', 'Req. Type - Description EN',
            'Req. Status - Description', 'Req. Closing Date',
            'Request - Text Request', 'Request - Text Answer', 'Request - Category',
            'Request - Subject description']
    
        existing_cols = [col for col in columns if col in final_df.columns]
        missing_cols = [col for col in columns if col not in final_df.columns]
        print(f"Existing Columns: {existing_cols}")
        print(f"Missing Columns: {missing_cols}")
        if len(existing_cols) > 0 and not missing_cols:
            df = final_df[['Req. Creation Date', 'Creation Time',
                        'Req. Creation Date - Year Week ISO', 'Request - ID',
                        'Request - Priority Description', 'Historical Status - Status From',
                        'Historical Status - Status To', 'Historical Status - Change Date',
                        'Historical Status - Change Time', 'Macro Area - Name',
                        'Request - Resource Assigned To - GROUP SAP MD',
                        'Macro Area (SAP) - MA Area', 'Request - User Name',
                        'Request - Resource Assigned To - Name', 'Req. Type - Description EN',
                        'Req. Status - Description', 'Req. Closing Date',
                        'Request - Text Request', 'Request - Text Answer', 'Request - Category',
                        'Request - Subject description']]

            df["Historical Status - Change Time"] = (
                    df["Historical Status - Change Time"]
                    .astype(str)
                    .str.replace(r"\.0$", "", regex=True)
                )
            date_cols = [
                                "Req. Creation Date",
                                "Historical Status - Change Date",
                                "Req. Closing Date"
                            ]

            for col in date_cols:
                df[col] = pd.Series(pd.to_datetime(df[col], errors="coerce")).dt.date
            
            df = df.replace({np.nan: None, pd.NA: None})
            db.query(SlaTicketData).delete() 
            db.commit()

            delete_table_log = {
                        "module_name": "Bainocular",
                        "program_name": "classification_src.py",
                        "user": "",
                        "log_type": "S",
                        "content": f"Deleted the table sla_tickets_data: insert_data()"
                    }

            resp = await add_log(delete_table_log)
            print(f"Logging Status: {resp}")
            records = df.to_dict(orient="records") #type: ignore
            sla_tickets = []

            for r in records:
                request_id = r.get("Request - ID")

                if request_id is None:
                    continue

                if str(request_id).strip() == "":
                    continue

                if str(request_id).lower() == "nan":
                    continue
            
                sla_tickets.append(
                    SlaTicketData(
                        ReqCreationDate = r["Req. Creation Date"],
                        CreationTime = r["Creation Time"],
                        ReqCreationDateYearWeekISO = r["Req. Creation Date - Year Week ISO"],
                        RequestID = r["Request - ID"],
                        RequestPriorityDescription = r["Request - Priority Description"],
                        HistoricalStatusStatusFrom = r["Historical Status - Status From"],
                        HistoricalStatusStatusTo = r["Historical Status - Status To"],
                        HistoricalStatusChangeDate = r["Historical Status - Change Date"],
                        HistoricalStatusChangeTime = r["Historical Status - Change Time"],
                        MacroAreaName = r["Macro Area - Name"],
                        RequestResourceAssignedToGROUPSAPMD = r["Request - Resource Assigned To - GROUP SAP MD"],
                        MacroArea = r["Macro Area (SAP) - MA Area"],
                        RequestUserName = r["Request - User Name"],
                        RequestResourceAssignedToName = r["Request - Resource Assigned To - Name"],
                        ReqTypeDescription = r["Req. Type - Description EN"],
                        ReqStatusDescription = r["Req. Status - Description"],
                        ReqClosingDate = r["Req. Closing Date"],
                        RequestTextRequest = r["Request - Text Request"],
                        RequestTextAnswer = r["Request - Text Answer"],
                        RequestCategory = r["Request - Category"],
                        RequestSubjectdescription = r["Request - Subject description"]
                    ) 
                )
           
            db.add_all(sla_tickets)
            db.commit()

            insert_values_log = {
                        "module_name": "Bainocular",
                        "program_name": "classification_src.py",
                        "user": "",
                        "log_type": "S",
                        "content": f"Inserted the tickets data into table: insert_data()"
                    }

            resp = await add_log(insert_values_log)
            print(f"Logging Status: {resp}")
            await add_user_log("DataSource", "SLA Input File", email or "", "S", f"Data uploaded successfully")
            return{
                "message": "Insertion successful",
                "length": len(sla_tickets)
            }

    except Exception as e:
        await add_user_log("DataSource", "SLA Input File", email or "", "E", f"Failed to upload file")
        raise HTTPException(status_code=500, detail=f"Error occured while processing - {str(e)}")


@app.post("/process_file_replace")
async def upload_sla_data(db: DbSession, file: UploadFile = File(...), email: Optional[str] = Form(None)):
    try:
        contents = await file.read()
        file_name = (file.filename or "").lower()
        print(f"filename:{file_name}")
        if file_name.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(contents))
        elif file_name.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(contents))
        else:
            return{
                        "error": "Unsupported file format"
                    }
        
        print(df.columns)
										 
				   

        columns = ['Req. Creation Date', 'Creation Time',
                'Req. Creation Date - Year Week ISO', 'Request - ID',
                'Request - Priority Description', 'Historical Status - Status From',
                'Historical Status - Status To', 'Historical Status - Change Date',
                'Historical Status - Change Time', 'Macro Area - Name',
                'Request - Resource Assigned To - GROUP SAP MD',
                'Macro Area (SAP) - MA Area', 'Request - User Name',
                'Request - Resource Assigned To - Name', 'Req. Type - Description EN',
                'Req. Status - Description', 'Req. Closing Date',
                'Request - Text Request', 'Request - Text Answer', 'Request - Category',
                'Request - Subject description']
        
        existing_cols = [col for col in columns if col in df.columns]
        missing_cols = [col for col in columns if col not in df.columns]
        print(f"Existing Columns: {existing_cols}")
        print(f"Missing Columns: {missing_cols}")
        if len(existing_cols) > 0 and not missing_cols:
            df = df[['Req. Creation Date', 'Creation Time',
                        'Req. Creation Date - Year Week ISO', 'Request - ID',
                        'Request - Priority Description', 'Historical Status - Status From',
                        'Historical Status - Status To', 'Historical Status - Change Date',
                        'Historical Status - Change Time', 'Macro Area - Name',
                        'Request - Resource Assigned To - GROUP SAP MD',
                        'Macro Area (SAP) - MA Area', 'Request - User Name',
                        'Request - Resource Assigned To - Name', 'Req. Type - Description EN',
                        'Req. Status - Description', 'Req. Closing Date',
                        'Request - Text Request', 'Request - Text Answer', 'Request - Category',
                        'Request - Subject description']]

            df["Historical Status - Change Time"] = (
                    df["Historical Status - Change Time"]
                    .astype(str)
                    .str.replace(r"\.0$", "", regex=True)
                )
            date_cols = [
                                "Req. Creation Date",
                                "Historical Status - Change Date",
                                "Req. Closing Date"
                            ]

            for col in date_cols:
                df[col] = pd.Series(pd.to_datetime(df[col], errors="coerce")).dt.date
            
            df = df.replace({np.nan: None, pd.NA: None})
            db.query(SlaTicketData).delete() 
            db.commit()

            delete_table_log = {
                        "module_name": "Bainocular",
                        "program_name": "ams_classification.py",
                        "user": email if email else "",
                        "log_type": "S",
                        "content": f"Deleted the table sla_tickets_data: insert_data()"
                    }

            resp = await add_log(delete_table_log)
            print(f"Logging Status: {resp}")
            records = df.to_dict(orient="records") #type: ignore
            sla_tickets = []

            for r in records:
                request_id = r.get("Request - ID")

                if request_id is None:
                    continue

                if str(request_id).strip() == "":
                    continue

                if str(request_id).lower() == "nan":
                    continue
            
                sla_tickets.append(
                    SlaTicketData(
                        ReqCreationDate = r["Req. Creation Date"],
                        CreationTime = r["Creation Time"],
                        ReqCreationDateYearWeekISO = r["Req. Creation Date - Year Week ISO"],
                        RequestID = r["Request - ID"],
                        RequestPriorityDescription = r["Request - Priority Description"],
                        HistoricalStatusStatusFrom = r["Historical Status - Status From"],
                        HistoricalStatusStatusTo = r["Historical Status - Status To"],
                        HistoricalStatusChangeDate = r["Historical Status - Change Date"],
                        HistoricalStatusChangeTime = r["Historical Status - Change Time"],
                        MacroAreaName = r["Macro Area - Name"],
                        RequestResourceAssignedToGROUPSAPMD = r["Request - Resource Assigned To - GROUP SAP MD"],
                        MacroArea = r["Macro Area (SAP) - MA Area"],
                        RequestUserName = r["Request - User Name"],
                        RequestResourceAssignedToName = r["Request - Resource Assigned To - Name"],
                        ReqTypeDescription = r["Req. Type - Description EN"],
                        ReqStatusDescription = r["Req. Status - Description"],
                        ReqClosingDate = r["Req. Closing Date"],
                        RequestTextRequest = r["Request - Text Request"],
                        RequestTextAnswer = r["Request - Text Answer"],
                        RequestCategory = r["Request - Category"],
                        RequestSubjectdescription = r["Request - Subject description"]
                    ) 
                )
        
							   
		 
																	
		   

            db.add_all(sla_tickets)
            db.commit()

            insert_values_log = {
                        "module_name": "Bainocular",
                        "program_name": "ams_classification.py",
                        "user": email if email else "",
                        "log_type": "S",
                        "content": f"Inserted the tickets data into table: upload_sla_data()"
                    }

            resp = await add_log(insert_values_log)
            print(f"Logging Status: {resp}")
            return{
                "message": "Insertion successful",
                "length": len(sla_tickets)
            }
        else:
            insert_values_log = {
                        "module_name": "Bainocular",
                        "program_name": "ams_classification.py",
                        "user": email if email else "",
                        "log_type": "E",
                        "content": f"File does not contain required fields: upload_sla_data()"
                    }

            resp = await add_log(insert_values_log)
            print(f"Logging Status: {resp}")
            return{
                "message": "Insertion FAILED",
                "length": 0
            }
    except Exception as e:
        insert_error_log = {
                        "module_name": "Bainocular",
                        "program_name": "ams_classification.py",
                        "user": email if email else "",
                        "log_type": "E",
                        "content": f"Error occurred while processing file: upload_sla_data() - {str(e)}"
                    }

        resp = await add_log(insert_error_log)
        print(f"Logging Status: {resp}")
        raise HTTPException(status_code=500, detail=f"Error occured while fetching processing file: {str(e)}")

@app.post("/update_knowledge_base")
def update_knowledge_base(conn: DbSessionPostgres, conn_vector: DbSessionVector):
    df = pd.read_sql_query("SELECT * FROM sla_ticekts_data", conn)
    vectorized_records = process_file(df)
    if vectorized_records is not None:
        save_embeddings_to_gcp_db(conn_vector, vectorized_records, "ams_tickets")


@app.get("/v1/classification/records")
async def classify_data(db: DbSession):
    return await get_data_gcp(db)

@app.post("/v1/classification/sentence")
async def classify_statement(request: Request):
    body = await request.json()
    query = body.get('sentence')
    return predict_sentence(query)

@app.get("/api/v1/analysis/stream-wise/get")
async def get_stream_wise_ticket_analysis():
    """
      Department and sub functions stats
    """
    try:
        return await get_classified_ticket_database()
    except Exception as e:
        print("Exception: ",e)
        return{
            "text": "Sorry! Unable to fetch analysis"
        }

				   
			
					  
	 

@app.post("/v3/lux/similar-tickets/query")
async def similar_ticket_search(conn: DbSessionVector, request: dict = Body(...)):
    query = None
    query = request.get("query")
    resp = None
    res = await process_user_query(conn, query, "ams_tickets")
    if len(res) > 0:
        resp = await summarize_result(res, query)
    return {
        "result": resp
    }


@app.post("/context-search/query")
async def context_ticket_search(conn: DbSessionVector, query: str = Form(None), image: UploadFile = File(None), email: Optional[str] = Form(None)):
    
    try:
        resp = None
        image_description = ""
        if image:
            image_description = await extract_text_from_image(image)
            await add_user_log("TroubleShooting Assistance", "AI Context Lookup", email or "", "S", "User queried context search with image")

        
        query_parts = []

        if query:
            query_parts.append(query)
            await add_user_log("TroubleShooting Assistance", "AI Context Lookup", email or "", "S", f"User queried context search {query}")
        if image_description:
            query_parts.append(image_description)

        final_query = " ".join(query_parts)

        if not final_query:
            return {
                "result": "No query or image provided"
            }
        res = await process_user_query(conn, final_query, "ams_tickets", email)
        if len(res) > 0:
            resp = await summarize_result(res, final_query, email)

            insert_values_log = {
                        "module_name": "Bainocular",
                        "program_name": "ams_classification.py",
                        "user": email if email else "",
                        "log_type": "S",
                        "content": f"Context solution processed successfully: context_ticket_search()"
                    }

            resplog = await add_log(insert_values_log)
            print(f"Logging Status: {resplog}")
            await add_user_log("TroubleShooting Assistance", "AI Context Lookup", email or "", "S", "Solution loaded for user query successfully")
        return {
            "result": resp
        }
    except Exception as e:
        insert_values_log = {
                    "module_name": "Bainocular",
                    "program_name": "ams_classification.py",
                    "user": email if email else "",
                    "log_type": "E",
                    "content": f"Context search failed due to error: context_ticket_search() - {str(e)}"
                }

        resp = await add_log(insert_values_log)
        print(f"Logging Status: {resp}")
        await add_user_log("TroubleShooting Assistance", "AI Context Lookup", email or "", "E", "Failed to process user query")
        raise HTTPException(status_code=500, detail=f"Error occurred in context search - {str(e)}")

#@app.post("/v3/lux/similar-tickets/query")
async def process_query(request: dict = Body(...)):
    #body = await request.json()
    #query =body.get("query")
    query = None
    query = request.get("query")
    
    if query and query.strip():

        query_receival_log = {
            "module_name": "KEDB Search",
            "program_name": "ams_classification.py",
            "user": "",
            "log_type": "S",
            "content": f"User Query Received Successfully{query}"
        }

        async with hp.AsyncClient() as c:
            log_response = await c.post("https://bainocular-log-api.cfapps.us10-001.hana.ondemand.com/log", json=query_receival_log)

        print(f"Logging Status: {log_response}")
        query_embeddings = await get_embedding(str(query))
        res = await get_context_tickets(query_embeddings)
        
        res = cast(QueryResponse, res)
        result_list = []
        for match in res.matches:
            result_list.append(match.metadata)

        if result_list and len(result_list) > 0:

            prompt_template = f"""
            
            ### Input Provided:
            📩 New Ticket Description:
            "{{query}}"
            
            📂 Similar Past Tickets:
            {{similar_tickets_list}}
            
            ### Action:
            🔍 Task:
            1. Describe the problem in your own words.
            2. Classify the SAP area (e.g., MM, SD, SCM, etc.) based on context.
            3. If relevant, suggest a solution based on similar tickets.
            4. Mention the ticket IDs with percentage matched you used for reference.
            
            ### Output Format:
            Problem Description:
            [Your summary]
            
            SAP Area:
            [Your classification]
            
            Suggested Solution (if applicable):
            [Suggested solution]
            
            Referenced Ticket IDs:
            [List of ticket IDs with percentage match]
            """
            input_prompt = f"""**User Query**: {query}
                **Similar Past Tickets**: {result_list}
            """

            try:
                response = client.responses.create(
                    model="gpt-4o-mini",
                    input=[
                        {"role": "system", "content": prompt_template},
                        {"role": "user", "content": input_prompt}
                    ],
                    temperature=0.2,
                )
                print(response)
                assistant_reply = response.output_text

                if assistant_reply:
                    openai_response_log = {
                        "module_name": "KEDB Search",
                        "program_name": "ams_classification.py",
                        "user": "",
                        "log_type": "S",
                        "content": f"Open AI response received successfully"
                    }

                    resp = await add_log(openai_response_log)
                    print(f"Loggin Status: {resp}")
                else:
                    openai_response_log = {
                        "module_name": "KEDB Search",
                        "program_name": "ams_classification.py",
                        "user": "",
                        "log_type": "S",
                        "content": f"Failed OpenAI response"
                    }

                    resp = await add_log(openai_response_log)
                    print(f"Logging Status: {resp}")
                
                output = {
                    "request": query,
                    "response": assistant_reply,
                    "metadata": {
                        "model": response.model,
                        "usage": response.usage
                        # "finish_reason": response.finish_reason,
                        # "index": response.output[0].index
                    }
                }
                return output
            except Exception as e:
                print(f"Error occured in openAI: {e}")
                openai_response_log = {
                        "module_name": "KEDB Search",
                        "program_name": "ams_classification.py",
                        "user": "",
                        "log_type": "E",
                        "content": f"Error in openAI"
                    }

                resp = await add_log(openai_response_log)
                print(f"Logging Status: {resp}")

        else:

            empty_data_log = {
                "module_name": "KEDB Search",
                "program_name": "ams_kedb.py",
                "user": "",
                "log_type": "S",
                "content": f"No Similar Context Ticket"
            }

            r = add_log(empty_data_log)
            print(f"Logging Status: {r}")
    else:

        query_receival_log = {
            "module_name": "KEDB Search",
            "program_name": "ams_kedb.py",
            "user": "",
            "log_type": "S",
            "content": f"Query Not Received"
        }

        resp = add_log(query_receival_log)
        print(f"Logging Status: {resp}")
        return "Empty Query"

@app.post("/get-problem-description")
async def get_problem_description(request: dict = Body(...)):
    query = None
    query = request.get("query")

    if query and query.strip():

        prompt_template = f"""
        
            ### Input Provided:
            📩 New Ticket Description:
            "{{query}}"
            
            📂 Similar Past Tickets:
            {{similar_tickets_list}}
            
            ### Action:
            🔍 Task:
            1. Describe the problem in your own words.

             ### Output Format:
            Problem Description:
            [Your summary]
        """

        input_prompt = f"""
           **User Query**: {query}
        """

        try:
            response = client.responses.create(
                model="gpt-4o-mini",
                input=[
                    {"role": "system", "content": prompt_template},
                    {"role": "user", "content":input_prompt}
                ],
                temperature=0.2
            )
            print(response)
            assistant_reply = response.output_text
            problem_description_log = {
                        "module_name": "Bainocular",
                        "program_name": "ams_classification.py",
                        "user": "",
                        "log_type": "S",
                        "content": f"problem description processed successfully: get_problem_description()"
                    }

            resp = await add_log(problem_description_log)
            print(f"Logging Status: {resp}")
            return  {"description": assistant_reply}
        except Exception as e:
            problem_description_log = {
                        "module_name": "Bainocular",
                        "program_name": "ams_classification.py",
                        "user": "",
                        "log_type": "E",
                        "content": f"problem description processing failed: get_problem_description() - {str(e)}"
                    }

            resp = await add_log(problem_description_log)
            print(f"Logging Status: {resp}")
            print(f"Error Occured in openAI: {e}")
    else:
        problem_description_log = {
                        "module_name": "Bainocular",
                        "program_name": "ams_classification.py",
                        "user": "",
                        "log_type": "E",
                        "content": f"user query not received: get_problem_description()"
                    }

        resp = await add_log(problem_description_log)
        print(f"Logging Status: {resp}")
        return None

@app.post("/api/v2/enquire/query")
async def query_ams_enquirer_v2(conn: DbSessionPostgres, request: Request):

    try:
        json_request_data = await request.json()
        print("json_raw_request: ", json_request_data)
        query_text = json_request_data.get('query')
        response_text = await replica_chatbot(conn, user_input=query_text)

        return {"text": response_text}
    except Exception as e:
        return {"text": "Sorry!Unable to access the query right now"}

@app.post("/api/v1/enquire/query")
async def query_ams_enquirer(request: Request):

    """
       Free Text Analysis - NLP Analysis
    """
    try:
        json_request_data = await request.json()
        print(f"json_raw_request: {json_request_data}")

        query_text = json_request_data.get('query')
        file_path = await get_database_to_file(upload_directory=EXCEL_FILE_DIR)
        response_text = await process_llm_with_file(user_query=query_text, excel_file_path=file_path)

        return{
            "text": response_text
        }
    except Exception as e:
        print(f"Error Occured in processing query")
        return{
            "text": "Error Occured in processing query"
        }

@app.get("/failed-idocs")
async def callFailedIdoc():
    print(f"Inside Failed Idoc")
    async with hp.AsyncClient(timeout=30) as client:
        response = await client.get(
            #"https://7c7a6dc7trial-trial.integrationsuitetrial-apim.us10.hana.ondemand.com/7c7a6dc7trial/s4hodata/ZC_IDOC_FAILED_CDS/ZC_IDOC_FAILED?$format=json",
            "https://a107a740trial-trial.integrationsuitetrial-apim.us10.hana.ondemand.com/a107a740trial/retrigger-bulk-idoc/ZC_IDOC_FAILED_CDS/ZC_IDOC_FAILED?$format=json",
            auth=hp.BasicAuth("abaphana82", "welcome@82"),
            headers={"Accept-Encoding": "application/gzip"}
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
                "error_category": item["ErrorCategory"]
            }
            for item in results
        ]
    
    return {
        "result": idocs
    }

@app.post("/retrigger-idocs")
async def reTriggerFailedIdoc(request: dict = Body(...), db:Session = Depends(get_db)):
    IvDocnum = request.get("idocno")
    user_email = request.get("email")

    sap_credential = db.query(SAPUser).filter(SAPUser.email == user_email).first()

    if not sap_credential:
        raise HTTPException(status_code=500, detail="SAP Credentials for this user does not exist")

    logger.info(f"SAP User: {sap_credential.sapuserid}")
    logger.info(f"SAP Password: {decrypt_password(sap_credential.password_hash)}")
    csrf_response = {}
    try:
        print("========== START ==========", flush=True)
        print("SAP User:", sap_credential.sapuserid, flush=True)
        print("SAP Password: ", sap_credential.password_hash, flush=True)
        print("========== END ==========", flush=True)
    except Exception as e:
        print("Error occurred in printing username and password", e)
        raise

    async with hp.AsyncClient(timeout=30) as client:
        csrf_response = await client.get(
            #"https://7c7a6dc7trial-trial.integrationsuitetrial-apim.us10.hana.ondemand.com/7c7a6dc7trial/retrigger-idoc/ZREPROCESS_IDOC_SRV_SRV/$metadata",
            "https://a107a740trial-trial.integrationsuitetrial-apim.us10.hana.ondemand.com/a107a740trial/retrigger-bulk-idoc/ZREPROCESS_IDOC_SRV_SRV/$metadata",
            auth=hp.BasicAuth(sap_credential.sapuserid, decrypt_password(sap_credential.password_hash)),
            headers={
                "Accept-Encoding": "application/gzip",
                "x-csrf-token": "fetch"
            }
        )
        
        csrf_token = csrf_response.headers.get("x-csrf-token")
        print("GET Status:", csrf_response.status_code)
        print("GET Headers:", dict(csrf_response.headers))
        print("GET Body:", csrf_response.text)
        print("CSRF Token:", csrf_token)
      
        try:
            csrf_response.raise_for_status()
        except Exception as e:
            print("GET Status:", csrf_response.status_code)
            print("GET Headers:", dict(csrf_response.headers))
            print("GET Body:", csrf_response.text)
            print("CSRF Token:", csrf_token)
            print(f"Error: {e}")
            raise

        body = {
            "IvDocnum": IvDocnum
        }
        
   
        response = await client.post(
            #"https://7c7a6dc7trial-trial.integrationsuitetrial-apim.us10.hana.ondemand.com/7c7a6dc7trial/retrigger-idoc/ZREPROCESS_IDOC_SRV_SRV/reprocess_idoc",
            "https://a107a740trial-trial.integrationsuitetrial-apim.us10.hana.ondemand.com/a107a740trial/retrigger-bulk-idoc/ZREPROCESS_IDOC_SRV_SRV/reprocess_idoc",
            json=body,
            auth=hp.BasicAuth(sap_credential.sapuserid, decrypt_password(sap_credential.password_hash)),
            headers={
                "Accept-Encoding": "application/gzip",
                "x-csrf-token": csrf_token,
                "Accept": "application/json",
                "Content-Type": "application/json"
            }
        )

    response.raise_for_status()
    data = response.json()

    results = data.get("d", {})
    idocs = {
                "IvDocnum": results.get("IvDocnum"),
                "EvMessage": results.get("EvMessage"),
                "EvStatus": results.get("EvStatus")
            }
        
    
    return {
        "result": idocs
    }

@app.post("/retrigger-bulk-idocs")
async def bulkFailedIdocRetrigger(request: dict = Body(...), db:Session = Depends(get_db)):
    #IvDocnum = request.get("idocno")
    user_email = request.get("email")

    sap_credential = db.query(SAPUser).filter(SAPUser.email == user_email).first()

    if not sap_credential:
        raise HTTPException(status_code=500, detail="SAP Credentials for this user does not exist")

    logger.info(f"SAP User: {sap_credential.sapuserid}")
    logger.info(f"SAP Password: {decrypt_password(sap_credential.password_hash)}")
    csrf_response = {}
    try:
        print("========== START ==========", flush=True)
        print("SAP User:", sap_credential.sapuserid, flush=True)
        print("SAP Password: ", sap_credential.password_hash, flush=True)
        print("========== END ==========", flush=True)
    except Exception as e:
        print("Error occurred in printing username and password", e)
        raise

    async with hp.AsyncClient(timeout=30) as client:
        csrf_response = await client.get(
            #"https://7c7a6dc7trial-trial.integrationsuitetrial-apim.us10.hana.ondemand.com/7c7a6dc7trial/bulk-idoc-retrigger/zidoc_reprocess_srv/srvd/sap/zidoc_reprocess_sd/0001/$metadata",
            "https://a107a740trial-trial.integrationsuitetrial-apim.us10.hana.ondemand.com/a107a740trial/retrigger-idoc/zidoc_reprocess_srv/srvd/sap/zidoc_reprocess_sd/0001/$metadata",
            auth=hp.BasicAuth(sap_credential.sapuserid, decrypt_password(sap_credential.password_hash)),
            headers={
                "Accept-Encoding": "application/gzip",
                "x-csrf-token": "fetch"
            }
        )
        
        csrf_token = csrf_response.headers.get("x-csrf-token")
        print("GET Status:", csrf_response.status_code)
        print("GET Headers:", dict(csrf_response.headers))
        print("GET Body:", csrf_response.text)
        print("CSRF Token:", csrf_token)
      
        try:
            csrf_response.raise_for_status()
        except Exception as e:
            print("GET Status:", csrf_response.status_code)
            print("GET Headers:", dict(csrf_response.headers))
            print("GET Body:", csrf_response.text)
            print("CSRF Token:", csrf_token)
            print(f"Error: {e}")
            raise
        body: str = ""
        for i in request.get("idocno"):
            body_format = f"""
                --batch_001

                Content-Type: application/http

                Content-Transfer-Encoding: binary

                POST bulkRetriggerIdoc HTTP/1.1

                Content-Type: application/json

                {{ "DOCNUM": "{i}" }}
        """
        
        #     body += body_format

        # body += "\n--batch_001--" 

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
            #"https://7c7a6dc7trial-trial.integrationsuitetrial-apim.us10.hana.ondemand.com/7c7a6dc7trial/bulk-idoc-retrigger/zidoc_reprocess_srv/srvd/sap/zidoc_reprocess_sd/0001/$batch",
            "https://a107a740trial-trial.integrationsuitetrial-apim.us10.hana.ondemand.com/a107a740trial/retrigger-idoc/zidoc_reprocess_srv/srvd/sap/zidoc_reprocess_sd/0001/$batch",
            data=body,
            auth=hp.BasicAuth(sap_credential.sapuserid, decrypt_password(sap_credential.password_hash)),
            headers={
                "Accept-Encoding": "gzip, deflate",
                "x-csrf-token": csrf_token,
                "Accept": "multipart/mixed",
                "Content-Type": "multipart/mixed; boundary=batch_001"
            }
        )

    response.raise_for_status()
    text = response.text

    #results = text.get("d", {})
    # idocs = {
    #             "IvDocnum": results.get("IvDocnum"),
    #             "EvMessage": results.get("EvMessage"),
    #             "EvStatus": results.get("EvStatus")
    #         }
        
    
    return {
        "result": text
    }

@app.get("/background-jobs")
async def callBgJobs():
    # Working CPI + SAP OData (same as Backend_Bainocular_FastAPI)
    url = "https://a107a740trial.it-cpitrial05-rt.cfapps.us10-001.hana.ondemand.com/http/s4odata"
    headers = {"Content-Type": "application/json"}
    job_data = []
    cet = ZoneInfo("Asia/Kolkata")
    now = datetime.now(tz=cet)
    # Today's jobs from midnight through current time (include minutes/seconds)
    sap_date = now.strftime("%Y-%m-%dT00:00:00")
    start_time = "000000"
    end_time = now.strftime("%H%M%S")
    print(f"{sap_date} || {start_time}..{end_time}")
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
            response = await client.post(url, json=body, headers=headers, auth=auth)
            response.raise_for_status()
            xml_data = response.text
            dict_data = xmltodict.parse(xml_data)
            json_data = json.loads(json.dumps(dict_data))
            job_data = normalize_jobs(json_data)
            print(len(job_data))
        except (hp.RequestError, hp.HTTPStatusError, ValueError, KeyError, TypeError) as e:
            print(f"Error Fetching the data: {e}")
            raise HTTPException(
                status_code=502,
                detail=f"Failed to fetch background jobs from SAP: {e}",
            ) from e

    return job_data


def normalize_jobs(data):
    cleaned = []
    entries = data.get("feed", {}).get("entry", [])
    # xmltodict returns a dict for a single entry, list for multiple
    if isinstance(entries, dict):
        entries = [entries]
    if not entries:
        return cleaned

    for e in entries:
        props = e["content"]["m:properties"]
        job = {
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
        }
        cleaned.append(job)

    return cleaned

if __name__=="__main__":
    PORT = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=PORT) 