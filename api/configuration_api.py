"""CRUD APIs for Job Configuration and Application Configuration (PostgreSQL)."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from cryptography.fernet import Fernet
import os 
from database_gcp import get_db
from models import (
    ApplicationConfiguration,
    ConfigurationGlobalSettings,
    FailedIdocConfiguration,
    JobConfiguration,
    User,
    SAPUser
)

config_router = APIRouter(prefix="/api/configuration", tags=["configuration"])

FERNET_KEY = os.environ.get("FERNET_KEY")
cipher = Fernet(FERNET_KEY.encode()) if FERNET_KEY else None
_GLOBAL_ID = 1


def _get_or_create_global(db: Session) -> ConfigurationGlobalSettings:
    row = (
        db.query(ConfigurationGlobalSettings)
        .filter(ConfigurationGlobalSettings.id == _GLOBAL_ID)
        .first()
    )
    if not row:
        row = ConfigurationGlobalSettings(
            id=_GLOBAL_ID,
            job_interval_time="",
            application_interval_time="",
            failed_idoc_interval_time="3",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


# --- Shared interval (one per section, not per record) ---


class GlobalIntervalsResponse(BaseModel):
    job_interval_time: str
    application_interval_time: str
    failed_idoc_interval_time: str


class GlobalIntervalsUpdate(BaseModel):
    job_interval_time: Optional[str] = Field(None, max_length=256)
    application_interval_time: Optional[str] = Field(None, max_length=256)
    failed_idoc_interval_time: Optional[str] = Field(None, max_length=256)

def encrypt_password(password: str) -> str:
    if cipher is None:
        return password
    return cipher.encrypt(password.encode()).decode()

BCRYPT_MAX_BYTES = 72

try:
    import bcrypt as _bcrypt
except ImportError:
    _bcrypt = None

def _password_bytes(password: str) -> bytes:
    """Truncate to 72 bytes for bcrypt limit."""
    enc = password.encode("utf-8")
    return enc[:BCRYPT_MAX_BYTES] if len(enc) > BCRYPT_MAX_BYTES else enc

def hash_password(password: str) -> str:
    if _bcrypt:
        secret = _password_bytes(password)
        return _bcrypt.hashpw(secret, _bcrypt.gensalt()).decode("utf-8")
    return password  # fallback if bcrypt not installed (dev only)

@config_router.get("/global-intervals", response_model=GlobalIntervalsResponse)
def get_global_intervals(db: Session = Depends(get_db)):
    row = _get_or_create_global(db)
    return GlobalIntervalsResponse(
        job_interval_time=row.job_interval_time or "",
        application_interval_time=row.application_interval_time or "",
        failed_idoc_interval_time=row.failed_idoc_interval_time or "3",
    )


@config_router.put("/global-intervals", response_model=GlobalIntervalsResponse)
def put_global_intervals(payload: GlobalIntervalsUpdate, db: Session = Depends(get_db)):
    row = _get_or_create_global(db)
    data = (
        payload.model_dump(exclude_unset=True)
        if hasattr(payload, "model_dump")
        else payload.dict(exclude_unset=True)
    )
    for key, val in data.items():
        if val is None:
            continue
        setattr(row, key, val.strip() if isinstance(val, str) else val)
    db.commit()
    db.refresh(row)
    return GlobalIntervalsResponse(
        job_interval_time=row.job_interval_time or "",
        application_interval_time=row.application_interval_time or "",
        failed_idoc_interval_time=row.failed_idoc_interval_time or "3",
    )


# --- Job configuration ---


class JobBase(BaseModel):
    job_name: str = Field(..., min_length=1, max_length=512)
    system: str = Field(..., min_length=1, max_length=256)
    period_start: str = Field(..., min_length=1, max_length=128)
    period_end: str = Field(..., min_length=1, max_length=128)


class JobCreate(JobBase):
    pass


class JobUpdate(BaseModel):
    job_name: Optional[str] = Field(None, min_length=1, max_length=512)
    system: Optional[str] = Field(None, min_length=1, max_length=256)
    period_start: Optional[str] = Field(None, min_length=1, max_length=128)
    period_end: Optional[str] = Field(None, min_length=1, max_length=128)


class JobResponse(BaseModel):
    id: int
    job_name: str
    system: str
    period_start: str
    period_end: str

    class Config:
        from_attributes = True


@config_router.get("/jobs", response_model=List[JobResponse])
def list_jobs(db: Session = Depends(get_db)):
    rows = db.query(JobConfiguration).order_by(JobConfiguration.id.asc()).all()
    return rows


@config_router.post("/jobs", response_model=JobResponse, status_code=201)
def create_job(payload: JobCreate, db: Session = Depends(get_db)):
    row = JobConfiguration(
        job_name=payload.job_name.strip(),
        system=payload.system.strip(),
        period_start=payload.period_start.strip(),
        period_end=payload.period_end.strip(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@config_router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: int, db: Session = Depends(get_db)):
    row = db.query(JobConfiguration).filter(JobConfiguration.id == job_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Job configuration not found")
    return row

@config_router.post("/register_sap_credentials")
def register_sap(sapcredentials: dict = Body(...), db: Session = Depends(get_db)):
    user_email = sapcredentials.get("email")

    if not user_email:
        raise HTTPException(status_code=400, detail="Email is required")
    
    row = db.query(User).filter(User.email == user_email).first()
    
    if not row:
        raise HTTPException(status_code=404, detail="User Not Found")
    

    sap_user = db.query(SAPUser).filter(SAPUser.userid == row.id).first()

    if sap_user:
        print(f"User already exists. so edit the value")
        sap_user.sapuserid = sapcredentials.get("sapuserid")
        sap_user.password_hash = encrypt_password(sapcredentials.get("password"))
        db.commit()
        db.refresh(sap_user)
    
    sap_user = SAPUser(
        sapuserid = sapcredentials.get("sapuserid"),
        password_hash = encrypt_password(sapcredentials.get("password")),
        userid = row.id,
        email = row.email
    )

    db.add(sap_user)

    db.commit()
    db.refresh(sap_user)

    return {
        "response": "Successfully configured SAP credentials"
    }
    
    


@config_router.put("/jobs/{job_id}", response_model=JobResponse)
def update_job(job_id: int, payload: JobUpdate, db: Session = Depends(get_db)):
    row = db.query(JobConfiguration).filter(JobConfiguration.id == job_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Job configuration not found")
    data = (
        payload.model_dump(exclude_unset=True)
        if hasattr(payload, "model_dump")
        else payload.dict(exclude_unset=True)
    )
    for key, val in data.items():
        if val is None:
            continue
        if isinstance(val, str):
            val = val.strip()
        setattr(row, key, val)
    db.commit()
    db.refresh(row)
    return row


@config_router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: int, db: Session = Depends(get_db)):
    row = db.query(JobConfiguration).filter(JobConfiguration.id == job_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Job configuration not found")
    db.delete(row)
    db.commit()
    return None


# --- Application configuration ---


class ApplicationBase(BaseModel):
    app_name: str = Field(..., min_length=1, max_length=256)
    status: str = Field(default="", max_length=128)
    details: str = Field(default="", max_length=4000)


class ApplicationCreate(ApplicationBase):
    pass


class ApplicationUpdate(BaseModel):
    app_name: Optional[str] = Field(None, min_length=1, max_length=256)
    status: Optional[str] = Field(None, max_length=128)
    details: Optional[str] = Field(None, max_length=4000)


class ApplicationResponse(BaseModel):
    id: int
    app_name: str
    status: str
    details: str

    class Config:
        from_attributes = True


@config_router.get("/applications", response_model=List[ApplicationResponse])
def list_applications(db: Session = Depends(get_db)):
    rows = db.query(ApplicationConfiguration).order_by(ApplicationConfiguration.id.asc()).all()
    return rows


@config_router.post("/applications", response_model=ApplicationResponse, status_code=201)
def create_application(payload: ApplicationCreate, db: Session = Depends(get_db)):
    row = ApplicationConfiguration(
        app_name=payload.app_name.strip(),
        status=(payload.status or "").strip(),
        details=(payload.details or "").strip(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@config_router.get("/applications/{app_id}", response_model=ApplicationResponse)
def get_application(app_id: int, db: Session = Depends(get_db)):
    row = db.query(ApplicationConfiguration).filter(ApplicationConfiguration.id == app_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Application configuration not found")
    return row


@config_router.put("/applications/{app_id}", response_model=ApplicationResponse)
def update_application(app_id: int, payload: ApplicationUpdate, db: Session = Depends(get_db)):
    row = db.query(ApplicationConfiguration).filter(ApplicationConfiguration.id == app_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Application configuration not found")
    data = (
        payload.model_dump(exclude_unset=True)
        if hasattr(payload, "model_dump")
        else payload.dict(exclude_unset=True)
    )
    for key, val in data.items():
        if val is None:
            continue
        if isinstance(val, str):
            val = val.strip()
        setattr(row, key, val)
    db.commit()
    db.refresh(row)
    return row


@config_router.delete("/applications/{app_id}", status_code=204)
def delete_application(app_id: int, db: Session = Depends(get_db)):
    row = db.query(ApplicationConfiguration).filter(ApplicationConfiguration.id == app_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Application configuration not found")
    db.delete(row)
    db.commit()
    return None


# --- Failed IDOC configuration ---


class FailedIdocBase(BaseModel):
    message_type: str = Field(..., min_length=1, max_length=256)
    sender: str = Field(default="", max_length=256)
    receiver: str = Field(default="", max_length=256)
    details: str = Field(default="", max_length=4000)


class FailedIdocCreate(FailedIdocBase):
    pass


class FailedIdocUpdate(BaseModel):
    message_type: Optional[str] = Field(None, min_length=1, max_length=256)
    sender: Optional[str] = Field(None, max_length=256)
    receiver: Optional[str] = Field(None, max_length=256)
    details: Optional[str] = Field(None, max_length=4000)


class FailedIdocResponse(BaseModel):
    id: int
    message_type: str
    sender: str
    receiver: str
    details: str

    class Config:
        from_attributes = True


@config_router.get("/failed-idocs", response_model=List[FailedIdocResponse])
def list_failed_idocs(db: Session = Depends(get_db)):
    rows = db.query(FailedIdocConfiguration).order_by(FailedIdocConfiguration.id.asc()).all()
    return rows


@config_router.post("/failed-idocs", response_model=FailedIdocResponse, status_code=201)
def create_failed_idoc(payload: FailedIdocCreate, db: Session = Depends(get_db)):
    row = FailedIdocConfiguration(
        message_type=payload.message_type.strip(),
        sender=(payload.sender or "").strip(),
        receiver=(payload.receiver or "").strip(),
        details=(payload.details or "").strip(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@config_router.get("/failed-idocs/{failed_idoc_id}", response_model=FailedIdocResponse)
def get_failed_idoc(failed_idoc_id: int, db: Session = Depends(get_db)):
    row = (
        db.query(FailedIdocConfiguration)
        .filter(FailedIdocConfiguration.id == failed_idoc_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Failed IDOC configuration not found")
    return row


@config_router.put("/failed-idocs/{failed_idoc_id}", response_model=FailedIdocResponse)
def update_failed_idoc(
    failed_idoc_id: int, payload: FailedIdocUpdate, db: Session = Depends(get_db)
):
    row = (
        db.query(FailedIdocConfiguration)
        .filter(FailedIdocConfiguration.id == failed_idoc_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Failed IDOC configuration not found")
    data = (
        payload.model_dump(exclude_unset=True)
        if hasattr(payload, "model_dump")
        else payload.dict(exclude_unset=True)
    )
    for key, val in data.items():
        if val is None:
            continue
        if isinstance(val, str):
            val = val.strip()
        setattr(row, key, val)
    db.commit()
    db.refresh(row)
    return row


@config_router.delete("/failed-idocs/{failed_idoc_id}", status_code=204)
def delete_failed_idoc(failed_idoc_id: int, db: Session = Depends(get_db)):
    row = (
        db.query(FailedIdocConfiguration)
        .filter(FailedIdocConfiguration.id == failed_idoc_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Failed IDOC configuration not found")
    db.delete(row)
    db.commit()
    return None

