from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.self_service_api import sap_router
from api.configuration_api import config_router
import os
import uvicorn
from ticket_src.ams_classification import app as bainocular_backend
from api.log_api import app as app_logging_api
from powersearch.power_search_context import app as powersearch_api
from api.configuration_params import app as confgparams_api
import sys
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all SQLite tables on startup (no-op if they already exist)
    from database_gcp import Base, engine
    Base.metadata.create_all(bind=engine)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="SapAutonomous Backend", version="1.0.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(config_router)
    app.include_router(sap_router)
    app.include_router(bainocular_backend)
    app.include_router(app_logging_api)
    app.include_router(powersearch_api)
    app.include_router(confgparams_api)
    return app


app = create_app()

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=PORT)
