from fastapi import Body, FastAPI, Query, Request, UploadFile, File, Depends
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, cast, Annotated
import os
import json
import uvicorn
from langchain_openai import OpenAIEmbeddings
from dotenv import load_dotenv
from pydantic import SecretStr
import uuid
import chromadb
import pandas as pd
from openai import OpenAI, RateLimitError
import zlib
import base64
import sys
import httpx as hp
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)
from db import get_db_connection
from sqlalchemy.orm import Session
from bainocular_configuration import ConfigParams

DbSession = Annotated[Session, Depends(get_db_connection)]

load_dotenv()
app = FastAPI()

# ChromaDB — local persistent vector store, no API key needed
_chroma_client = chromadb.PersistentClient(path="./chroma_db")


def _get_collection(name: str = "ams_tickets"):
    return _chroma_client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )

#client = OpenAI(api_key=get_api_key(my_project, ai_prod_key))
client = OpenAI(api_key=ConfigParams.openai_api_key)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # MUST be False with "*"
    allow_methods=["*"],
    allow_headers=["*"],
)

def compress_text(text: str) -> str:
    if text is None or pd.isna(text):
        return ""

    text = str(text)   # 🔥 convert float/int → string

    compressed = zlib.compress(text.encode("utf-8"))
    return base64.b64encode(compressed).decode("utf-8")


def decompress_text(encoded: str) -> str:
    if not encoded:
        return ""
    decoded = base64.b64decode(encoded.encode("utf-8"))
    return zlib.decompress(decoded).decode("utf-8")

def safe_upsert(index, batch):
    size = sys.getsizeof(batch)

    if size > 3_500_000:
        print("Batch too large, reducing...")
        return False

    index.upsert(vectors=batch)
    return True


def clean_metadata(d):
    return{
        k: (None if v != v else v)
        for k,v in d.items()
    }

async def get_embedding(text: str) -> list:

    try:

        key = os.environ.get("OPEN_API_KEY")

        embedding_model = OpenAIEmbeddings(
            model="text-embedding-3-small",
            api_key=SecretStr(key) if key is not None else None
            #openai_api_key=os.environ.get("OPEN_API_KEY")
        )

        vector_embedding = embedding_model.embed_query(text)
        if vector_embedding:
            embedding_generation_log = {
                "module_name": "Bainocular",
                "program_name": "ams_kedb.py",
                "user": "",
                "log_type": "S",
                "content": "Embedding Generated Successfully"
            }
            resp = await add_log(embedding_generation_log)
            print(f"Logging Status: {resp}")
        return vector_embedding
    except Exception as e:
        embedding_generation_log = {
                "module_name": "Bainocular",
                "program_name": "ams_kedb.py",
                "user": "",
                "log_type": "E",
                "content": f"Embedding Generation Failed: {e}"
            }
        
        resp = await add_log(embedding_generation_log)
        print(f"Logging Status: {resp}")
        return []
        


def process_file(df):
    df = df[df['Req. Status - Description'].isin(['Closed','Solved'])]
    #df = df[df['Historical Status - Status To'].isin(['Closed', 'Solved'])] 
    df = df[df['Historical Status - Status To'].isin(['Closed'])] 
    row, colums = df.shape
    print(f"Size of Data Frame:{row}")
    """Now we need to vectorize the text request and user input. Also create a metadata with fields Request ID, Macro Area - Name, Req. Type - Description EN, Request - Text Answer. Using cosine similarity search for request description similary to user input and fetch metadata for it."""
    t = df.to_numpy()
    columns = df.columns
    print(f"Type of Columns: {type(columns)}")
    #print(f"Lenght of dataframe: {df.length}")
    #print(t[:3])
 
   
   
    key = ConfigParams.openai_api_key
    #embedding_model = OpenAIEmbeddings(deployment_id=deployment_id, proxy_client=proxy_client)
    embedding_model = OpenAIEmbeddings(
        model="text-embedding-3-small",
        api_key=SecretStr(key) if key is not None else None
        #openai_api_key=os.environ.get("OPEN_API_KEY")
    )

    ticket_records = []
    creation_date_idx = columns.get_loc("Req. Creation Date")
    text_request_index = columns.get_loc("Request - Text Request")
    request_id_idx = columns.get_loc("Request - ID")
    macro_name_idx = columns.get_loc("Macro Area - Name")
    request_type_idx = columns.get_loc("Req. Type - Description EN")
    request_answ_idx =columns.get_loc("Request - Text Answer")
    request_assignee_idx = columns.get_loc("Request - Resource Assigned To - Name")
    print("Initiating vectorization process")

    texts = [str(row[text_request_index]) if row[text_request_index] is not None else "" for row in  df.itertuples(index=False)]
    embeddings = embedding_model.embed_documents(texts=texts)

    for row,embedding in zip(df.itertuples(index=False), embeddings):

        #embedding = embedding_model.embed_query(row[text_request_index])
        metadata = {
            "requestId": row[request_id_idx],
            "macroarea": row[macro_name_idx],
            "requesttype": row[request_type_idx],
            "text_request": compress_text(row[text_request_index]),
            "requestansw": compress_text(row[request_answ_idx]),
            "consultant": row[request_assignee_idx] if row[request_assignee_idx] is not None else ""
        }
        record = {
            "id": str(uuid.uuid4()),
            "vector": embedding,
            "metadata": metadata,
            
        }    
        
       # print(f"processing row {record}")
        ticket_records.append(record)
        #print(f"Request - Text Request {row[text_request_index]}")
    print(f"Finished Processing records")
    return ticket_records

def save_embeddings_to_gcp_db(conn, ticket_records, table_name):
    """Save embeddings to ChromaDB (local, no credentials needed)."""
    if not ticket_records:
        return
    collection = _get_collection(table_name)
    collection.upsert(
        ids=[t["id"] for t in ticket_records],
        embeddings=[t["vector"] for t in ticket_records],
        metadatas=[clean_metadata(t["metadata"]) for t in ticket_records],
    )
    print(f"{len(ticket_records)} embeddings saved to ChromaDB collection '{table_name}'")

def save_embeddings_to_db(ticket_records):
    """Save embeddings to ChromaDB local collection."""
    if not ticket_records:
        return
    collection = _get_collection("ams_tickets")
    collection.upsert(
        ids=[t["id"] for t in ticket_records],
        embeddings=[t["vector"] for t in ticket_records],
        metadatas=[clean_metadata(t["metadata"]) for t in ticket_records],
    )
    print(f"Saved {len(ticket_records)} records to ChromaDB")


async def get_context_tickets(query_vector: list[float]):
    try:
        collection = _get_collection("ams_tickets")
        results = collection.query(query_embeddings=[query_vector], n_results=3)
        # Return as list of (metadata, answer, similarity) tuples matching summarize_result expectations
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        formatted = [(m, None, 1 - d) for m, d in zip(metadatas, distances)]
        return formatted
    except Exception as e:
        print(f"ChromaDB fetch error: {e}")
        return []

async def process_user_query(conn, user_input, table_name, email: Optional[str] = None):
    try:
        key = ConfigParams.openai_api_key
        embedding_model = OpenAIEmbeddings(
            model="text-embedding-3-small",
            api_key=SecretStr(key) if key else None,
        )
        user_query_embedding = await embedding_model.aembed_query(user_input)

        collection = _get_collection(table_name)
        results = collection.query(query_embeddings=[user_query_embedding], n_results=3)
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        # Return as (metadata, answer, similarity) tuples — same shape as the old PostgreSQL rows
        return [(m, None, 1 - d) for m, d in zip(metadatas, distances)]

    except RateLimitError as e:
        print(f"OpenAI rate limit: {e}")
        return []
    except Exception as e:
        print(f"process_user_query error: {e}")
        return []

async def summarize_result(result, query, email: Optional[str] = None):
    result_list = []
    for r in result:
        result_list.append(r[0])

    if result_list and len(result_list) > 0:


        query_receival_log = {
            "module_name": "KEDB Search",
            "program_name": "ams_kedb.py",
            "user": email if email else "",
            "log_type": "S",
            "content": f"Result received Successfully{query}"
        }

        resp = await add_log(query_receival_log)
        print(f"Logging Status: {resp}")


        prompt_template = f"""
            
            ### Role
            You are an SAP Incident Analysis Assistant specialized exclusively in SAP systems and SAP support tickets.

            ### Rules
            1. Only analyze and respond to SAP-related issues.
            2. If the ticket is not related to SAP, respond exactly:

            "NON_SAP_REQUEST: The provided ticket does not appear to be related to SAP. Please submit an SAP-related issue for analysis."

            3. Do not provide troubleshooting, recommendations, or classifications for non-SAP technologies.

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
        return assistant_reply
    
    else:

        query_receival_log = {
            "module_name": "KEDB Search",
            "program_name": "ams_kedb.py",
            "user": email if email else "",
            "log_type": "W",
            "content": f"No Similar context tickets or empty data fetched from database - summarize_result()"
        }

        resp = add_log(query_receival_log)
        print(f"Logging Status: {resp}")
        return "Empty Query"


async def extract_text_from_image(image: UploadFile) -> str:
    image_bytes = await image.read()

    base64_image = base64.b64encode(image_bytes).decode("utf-8")

    response = client.chat.completions.create(
        model="gpt-5-nano",
        messages=[
            {
                "role": "user",
                "content":[
                    {
                        "type": "text",
                        "text": """
                        Analyze this image which is related to SAP queries, doubts, issues , problems and errors.

                        Extract:
                        1. Visible text (OCR)
                        2. Error messages
                        3. UI details
                        4. Technical issue description

                        Return a concise ticket description.
                    """
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    }
                ]
            }
        ]
    )

    return response.choices[0].message.content

@app.post("/kedb")
async def process_query(request: dict = Body(...)):
    #body = await request.json()
    #query =body.get("query")
    query = None
    query = request.get("query")
    
    if query and query.strip():

        query_receival_log = {
            "module_name": "KEDB Search",
            "program_name": "ams_kedb.py",
            "user": "",
            "log_type": "S",
            "content": f"Query Received Successfully{query}"
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
            return assistant_reply
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
 							

async def add_log(payload):
    print(f"Inside add logs")
    try:
        async with hp.AsyncClient() as c:
            #log_response = await c.post(url="https://bainocular-log-api.cfapps.us10-001.hana.ondemand.com/log", json=payload)
            log_response = await c.post(url="https://api-dev.bainocular.seleccionconsulting.com/log", json=payload)
            print(payload, log_response)
        return log_response.json()
    except Exception as e:
        print(f"Logging Failed: {e}")
        return None
        

if __name__=="__main__":
    conn_gen = get_db_connection()
    conn = next(conn_gen)
    df = pd.read_excel("./HDA for SAP Retail (FMS1) NA.2026-03-13-09-00-14.xlsx")
    # print(client.models.list())
    records = process_file(df)
    print(records[1])
    #save_embeddings_to_gcp_db(conn, records, "ams_tickets")
    #save_embeddings_to_db(records)
    # PORT = int(os.environ.get("PORT", 8000))
    # uvicorn.run(app, host="0.0.0.0", port=PORT) 
