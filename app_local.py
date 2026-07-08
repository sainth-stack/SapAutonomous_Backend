# Local development entry point — skips GCP/DB-dependent modules.
# Use: uvicorn app_local:app --host 0.0.0.0 --port 4001 --reload

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.self_service_api import sap_router
from api.configuration_api import config_router

import os
import uvicorn
import platform
import sys
from datetime import datetime, timezone
from contextlib import asynccontextmanager

_START_TIME = datetime.now(timezone.utc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="SapAutonomous Backend (Local)", version="1.0.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["Health"])
    async def health():
        uptime_seconds = (datetime.now(timezone.utc) - _START_TIME).total_seconds()
        return JSONResponse({
            "status": "ok",
            "service": "SapAutonomous Backend (Local)",
            "version": "1.0.0",
            "port": int(os.environ.get("PORT", 4001)),
            "uptime_seconds": round(uptime_seconds, 1),
            "started_at": _START_TIME.isoformat(),
            "python_version": sys.version.split()[0],
            "platform": platform.system(),
        })

    app.include_router(config_router)
    app.include_router(sap_router)
    return app


app = create_app()

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 4001))
    uvicorn.run(app, host="0.0.0.0", port=PORT)
