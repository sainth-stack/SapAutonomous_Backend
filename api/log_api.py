import os
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import fcntl
import httpx as hp
import tempfile
import zipfile

load_dotenv()

#app = FastAPI(title="BAInocular SAP CF Logging API")
app = APIRouter()

# Configure CORS
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"], # In production, replace with specific React app URL
#     allow_methods=["POST", "GET"],
#     allow_headers=["*"],
# )

PORT = int(os.getenv("PORT", 8000))
# Base directory for logs; default to current directory
LOG_DIR = os.getenv("LOG_DIR", "./")
BASE_DIR = Path(__file__).resolve().parent

class LogEntry(BaseModel):
    module_name: str
    program_name: str
    user: str
    log_type: str
    content: str = Field(..., max_length=1000)

def get_current_log_path():
    """Generates the filename: bainocular_log_mmddyyyy.txt"""
    date_str = datetime.now().strftime("%m%d%Y")
    filename = f"bainocular_log_{date_str}.txt"
    print(f"Filename-get)current_log_path(): {filename}")
    return os.path.join(BASE_DIR, filename)

def get_current_userlog_path():
    """Generates the filename: bainocular_log_mmddyyyy.txt"""
    date_str = datetime.now().strftime("%m%d%Y")
    filename = f"bainocular_userlog_{date_str}.txt"
    print(f"Filename-get)current_log_path(): {filename}")
    return os.path.join(BASE_DIR, filename)

@app.get("/")
async def health_check():
    return {"status": "running", "environment": "SAP Cloud Foundry"}

@app.post("/log")
async def create_log(entry: LogEntry):
    # 1. Determine the file path for today
    current_file_path = get_current_log_path()
    
    # 2. Build the log entry
    timestamp = datetime.now().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    header = f"{entry.module_name}_{entry.program_name}_{entry.user}_{timestamp}_{entry.log_type.lower()}:"
    log_string = f"{header}:{entry.content}\n" # Added newline between header and content
    
    try:
        # 'a' mode appends or creates the file if it doesn't exist
        print(f"File path: {current_file_path}")
        # with open(current_file_path, "a", encoding="utf-8") as f:
        #     f.write(log_string)
        
        with open(current_file_path, "a", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)

            f.write(log_string)
            f.flush()
            os.fsync(f.fileno())

            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        # Print to standard out for SAP BTP Cockpit
        print(f"Log saved to {current_file_path}: {log_string.strip()}")
        return {"status": "success", "file": os.path.basename(current_file_path)}
    except Exception as e:
        print(f"Write Error: {e}")
        raise HTTPException(status_code=500, detail="Log storage unavailable")

@app.post("/user-log")
async def create_user_log(entry: LogEntry):
    # 1. Determine the file path for today
    current_file_path = get_current_userlog_path()
    
    # 2. Build the log entry
    timestamp = datetime.now().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    header = f"{entry.module_name}_{entry.program_name}_{entry.user}_{timestamp}_{entry.log_type.lower()}:"
    log_string = f"{header}:{entry.content}\n" # Added newline between header and content
    
    try:
        # 'a' mode appends or creates the file if it doesn't exist
        print(f"File path: {current_file_path}")
        # with open(current_file_path, "a", encoding="utf-8") as f:
        #     f.write(log_string)
        
        with open(current_file_path, "a", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)

            f.write(log_string)
            f.flush()
            os.fsync(f.fileno())

            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        # Print to standard out for SAP BTP Cockpit
        print(f"Log saved to {current_file_path}: {log_string.strip()}")
        return {"status": "success", "file": os.path.basename(current_file_path)}
    except Exception as e:
        print(f"Write Error: {e}")
        raise HTTPException(status_code=500, detail="Log storage unavailable")
    
@app.get("/download_user_activity")
async def download_date_user_logs(from_date: str = Query(..., description="YYYY-MM-DD"), to_date: str = Query(..., description="YYYY-MM-DD")):
    try:
        start_date = datetime.strptime(from_date, "%Y-%m-%d")
        end_date = datetime.strptime(to_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Date format should be YYYY-MM-DD"
        )
    
    if start_date > end_date:
        raise HTTPException(
            status_code=400,
            detail="from_date cannot be after to_date"
        )
    
    log_files = []
    current_date = start_date

    while current_date <= end_date:
        #log_path = get_current_userlog_path(current_date)

        date_str = current_date.strftime("%m%d%Y")
        filename = f"bainocular_userlog_{date_str}.txt"

        log_path = os.path.join(BASE_DIR, filename)

        if os.path.exists(log_path):
            log_files.append(log_path)

        current_date += timedelta(days=1)

    if not log_files:
        raise HTTPException(status_code=404, detail="No log files found for selected dates")
    
    zip_file = tempfile.NamedTemporaryFile(
        delete=False,
        suffix=".zip"
    )

    with zipfile.ZipFile(zip_file.name, "w") as zipf:
        for log_file in log_files:
            zipf.write(
                log_file,
                arcname=os.path.basename(log_file)
            )

    return FileResponse(
        path=zip_file.name,
        filename=f"bainocular_userlogs_{from_date}_to_{to_date}.zip",
        media_type="applicaton/zip"
    )

@app.get("/download")
async def download_logs():
    """
    Downloads the log file for the CURRENT date.
    """
    print(f"Downloading log file")
    print(f"CWD: {os.getcwd()}")
   
    current_file_path = get_current_log_path()
    print(f"Absolute path: {os.path.abspath(current_file_path)}")
    print(f"Exist: {os.path.exists(current_file_path)}")
    print(f"Filepath: {current_file_path}")
    if os.path.exists(current_file_path):
        return FileResponse(
            path=current_file_path, 
            filename=os.path.basename(current_file_path), 
            media_type='text/plain'
        )
    else:
        BASE_DIR = Path(__file__).resolve().parent

        print("Files in BASE_DIR:")
        for f in BASE_DIR.iterdir():
            print(f.name)
        raise HTTPException(status_code=404, detail="No log file found for today yet.")

@app.get("/download-user-logs")
async def download_logs():
    """
    Downloads the log file for the CURRENT date.
    """
    print(f"Downloading log file")
    print(f"CWD: {os.getcwd()}")
   
    current_file_path = get_current_userlog_path()
    print(f"Absolute path: {os.path.abspath(current_file_path)}")
    print(f"Exist: {os.path.exists(current_file_path)}")
    print(f"Filepath: {current_file_path}")
    if os.path.exists(current_file_path):
        return FileResponse(
            path=current_file_path, 
            filename=os.path.basename(current_file_path), 
            media_type='text/plain'
        )
    else:
        BASE_DIR = Path(__file__).resolve().parent

        print("Files in BASE_DIR:")
        for f in BASE_DIR.iterdir():
            print(f.name)
        raise HTTPException(status_code=404, detail="No log file found for today yet.")


async def add_log(payload):
    """Forward a structured log payload to the shared log endpoint."""
    print("Inside add logs")
    try:
        async with hp.AsyncClient() as c:
            log_response = await c.post(
                url="https://api-dev.bainocular.seleccionconsulting.com/log",
                json=payload,
            )
            print(payload, log_response)
        return log_response.json()
    except Exception as e:
        print(f"Logging Failed: {e}")
        return None


async def add_user_log(module_name: str, program_name: str, user: str, logType: str, content: str):

    payload = {
      "module_name": module_name,
      "program_name": program_name,
      "user": user,
      "log_type": logType,
      "content": content
    }
    print(f"Inside add logs")
    try:
        async with hp.AsyncClient() as c:
            #log_response = await c.post(url="https://bainocular-log-api.cfapps.us10-001.hana.ondemand.com/log", json=payload)
            log_response = await c.post(url="https://api-dev.bainocular.seleccionconsulting.com/user-log", json=payload)
            print(payload, log_response)
        return log_response.json()
    except Exception as e:
        print(f"Logging Failed: {e}")
        return None


if __name__ == "__main__":
    import uvicorn
    # Ensure LOG_DIR exists
    if LOG_DIR != "./":
        os.makedirs(LOG_DIR, exist_ok=True)
    uvicorn.run(app, host="0.0.0.0", port=PORT)