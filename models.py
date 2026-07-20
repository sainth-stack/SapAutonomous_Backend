"""SQLAlchemy models used by configuration and auth seeding."""
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, Text
from sqlalchemy.orm import relationship
from database_gcp import Base


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), unique=True, nullable=False, index=True)
    description = Column(String(512), default="")
    permissions = Column(Text, default="")
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
    __tablename__ = "sapusercredentials"

    id = Column(Integer, primary_key=True, index=True)
    sapuserid = Column(String(256), unique=True, nullable=False, index=True)
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
    """Singleton (id=1): shared interval times for monitoring sections."""

    __tablename__ = "configuration_global_settings"

    id = Column(Integer, primary_key=True)
    job_interval_time = Column(String(256), default="")
    application_interval_time = Column(String(256), default="")
    failed_idoc_interval_time = Column(String(256), default="3")
