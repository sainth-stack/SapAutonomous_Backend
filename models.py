"""SQLAlchemy models for admin roles and users."""
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, Table, Text, Date
from sqlalchemy.orm import relationship
from database_gcp import Base

# Role <-> Permission (page path) many-to-many via JSON/list stored on Role
# User -> Role many-to-one


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), unique=True, nullable=False, index=True)
    description = Column(String(512), default="")
    # Comma-separated or JSON list of allowed page paths; we use comma-separated for simplicity
    permissions = Column(Text, default="")  # e.g. "/kedb,/web-suggested-actions"
    # When True, users with this role see all SLA tickets (same as legacy admin emails)
    access_all_data = Column(Boolean, default=False, nullable=False)

    users = relationship("User", back_populates="role")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(128), nullable=False, index=True)
    email = Column(String(256), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=True)
    is_super_admin = Column(Boolean, default=False)

    role = relationship("Role", back_populates="users")
    sapcredentials = relationship("SAPUser", back_populates="user", uselist=False)

class SAPUser(Base):
    __tablename__="sapusercredentials"

    id = Column(Integer, primary_key=True, index=True)
    sapuserid = Column(String(256),unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    userid = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    email = Column(String(256), nullable=False)

    user = relationship("User", back_populates="sapcredentials")

class JobConfiguration(Base):
    """Background job monitoring window configuration."""

    __tablename__ = "job_configurations"

    id = Column(Integer, primary_key=True, index=True)
    job_name = Column(String(512), nullable=False)
    system = Column(String(256), nullable=False)
    period_start = Column(String(128), nullable=False)
    period_end = Column(String(128), nullable=False)


class ApplicationConfiguration(Base):
    """Application monitoring / alert routing configuration."""

    __tablename__ = "application_configurations"

    id = Column(Integer, primary_key=True, index=True)
    app_name = Column(String(256), nullable=False)
    status = Column(String(128), nullable=False, default="")
    details = Column(Text, default="")


class FailedIdocConfiguration(Base):
    """Failed IDOC monitoring filter / alert routing configuration."""

    __tablename__ = "failed_idoc_configurations"

    id = Column(Integer, primary_key=True, index=True)
    message_type = Column(String(256), nullable=False)
    sender = Column(String(256), nullable=False, default="")
    receiver = Column(String(256), nullable=False, default="")
    details = Column(Text, default="")


class ConfigurationGlobalSettings(Base):
    """Singleton (id=1): shared interval times for Job, Application, and Failed IDOC sections — not per-row."""

    __tablename__ = "configuration_global_settings"

    id = Column(Integer, primary_key=True)  # always 1
    job_interval_time = Column(String(256), default="")
    application_interval_time = Column(String(256), default="")
    failed_idoc_interval_time = Column(String(256), default="3")

class SlaTicketData(Base):
    __tablename__ = "sla_tickets_data"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    ReqCreationDate = Column(Date, nullable=True)
    CreationTime = Column(Text, nullable=True)
    ReqCreationDateYearWeekISO = Column(Text, index=True)
    #RequestID = Column(String, primary_key=True, index=True)
    RequestID = Column(String, nullable=False ,index=True)
    RequestPriorityDescription = Column(Text, nullable=True)
    HistoricalStatusStatusFrom = Column(Text, nullable=True)
    HistoricalStatusStatusTo = Column(Text, nullable=True)
    HistoricalStatusChangeDate = Column(Date, nullable=True)
    HistoricalStatusChangeTime = Column(Text, nullable=True)
    MacroAreaName = Column(Text, nullable=True)
    RequestResourceAssignedToGROUPSAPMD = Column(Text, nullable=True)
    MacroArea = Column(Text, nullable=True)
    RequestUserName = Column(Text, nullable=True)
    RequestResourceAssignedToName = Column(Text, nullable=True)
    ReqTypeDescription = Column(Text, nullable=True)
    ReqStatusDescription = Column(Text, nullable=True)
    ReqClosingDate = Column(Date, nullable=True)
    RequestTextRequest = Column(Text, nullable=True)
    RequestTextAnswer = Column(Text, nullable=True)
    RequestCategory = Column(Text, nullable=True)
    RequestSubjectdescription = Column(Text, nullable=True)