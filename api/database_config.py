"""
database.py
-----------
GCP Cloud SQL async connection pool engine.

Environment variables expected (set via .env or cloud secret manager):
  DB_HOST        – Cloud SQL public/private IP or Unix socket path
  DB_PORT        – TCP port (default 5432 for PostgreSQL)
  DB_NAME        – Target database name
  DB_USER        – Database user
  DB_PASSWORD    – Database password
  DB_SSL_CA      – Path to server CA certificate (server-ca.pem)
  DB_SSL_CERT    – Path to client certificate (client-cert.pem)
  DB_SSL_KEY     – Path to client private key  (client-key.pem)
  DB_POOL_MIN    – Minimum pool connections (default 2)
  DB_POOL_MAX    – Maximum pool connections (default 10)

For Cloud SQL Auth Proxy usage, set DB_HOST to 127.0.0.1 and omit SSL vars.
"""

from __future__ import annotations

import logging
import os
import ssl
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from dotenv import load_dotenv

load_dotenv()

import asyncpg
from asyncpg import Pool, Connection
import sys
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level pool singleton – initialised once at application startup
# ---------------------------------------------------------------------------
_pool: Pool | None = None

def _build_ssl_context() -> ssl.SSLContext | None:
    """
    Construct an SSLContext from the three PEM artefacts expected by GCP Cloud
    SQL when direct (non-proxy) TLS connections are required.  Returns None if
    none of the SSL environment variables are set, allowing proxy-based
    deployments to skip mutual-TLS handshake.
    """
    ca   = os.getenv("DB_SSL_CA")
    cert = os.getenv("DB_SSL_CERT")
    key  = os.getenv("DB_SSL_KEY")

    if not any([ca, cert, key]):
        logger.info("No SSL certificate paths provided – connecting without mutual TLS.")
        return None

    if not all([ca, cert, key]):
        raise EnvironmentError(
            "Partial SSL configuration detected.  "
            "All three variables DB_SSL_CA, DB_SSL_CERT, and DB_SSL_KEY must "
            "be provided together, or all omitted for proxy-mode connections."
        )

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cafile=ca)           # Trust the Cloud SQL CA
    ctx.load_cert_chain(certfile=cert, keyfile=key) # Present client identity
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = False  # Cloud SQL IPs don't match a hostname CN
    logger.info("Mutual-TLS SSL context constructed for GCP Cloud SQL.")
    return ctx


async def init_pool() -> None:
    """
    Create the asyncpg connection pool and store it in the module-level
    singleton.  Called once from the FastAPI lifespan startup hook.
    """
    global _pool

    # dsn_params: dict = {
    #    # "host":     ConfigParams.db_host,
    #     "host":     '10.128.0.5',
    #     "port":     ConfigParams.db_port,
    #     "database": ConfigParams.db_name,      # Required – fail fast if absent
    #     "user":     ConfigParams.db_user,
    #     "password": ConfigParams.db_pwd,
    #     "min_size": int(os.getenv("DB_POOL_MIN", "2")),
    #     "max_size": int(os.getenv("DB_POOL_MAX", "10")),
    #     "command_timeout": 30,
    # }

    dsn_params: dict = {
        "host":     os.getenv("DB_HOST", ""),
        "port":     os.getenv("DB_PORT", "5432"),
        "database": os.getenv("DB_NAME", ""),
        "user":     os.getenv("DB_USER", ""),
        "password": os.getenv("DB_PASSWORD", ""),
        "min_size": int(os.getenv("DB_POOL_MIN", "2")),
        "max_size": int(os.getenv("DB_POOL_MAX", "10")),
        "command_timeout": 30,
    }

    ssl_ctx = _build_ssl_context()
    if ssl_ctx:
        dsn_params["ssl"] = ssl_ctx

    logger.info(
        "Initialising asyncpg pool → %s:%s/%s (pool %s–%s)",
        dsn_params["host"], dsn_params["port"], dsn_params["database"],
        dsn_params["min_size"], dsn_params["max_size"],
    )

    _pool = await asyncpg.create_pool(**dsn_params)
    logger.info("asyncpg connection pool ready.")


async def close_pool() -> None:
    """
    Gracefully drain and close the connection pool.  Called from the FastAPI
    lifespan shutdown hook.
    """
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("asyncpg connection pool closed.")


def get_pool() -> Pool:
    """
    Return the live pool.  Raises RuntimeError if the pool was never
    initialised – guards against calls before application startup completes.
    """
    if _pool is None:
        raise RuntimeError(
            "Database pool has not been initialised.  "
            "Ensure init_pool() is awaited inside the FastAPI lifespan startup."
        )
    return _pool


@asynccontextmanager
async def get_connection() -> AsyncGenerator[Connection, None]:
    """
    Async context manager that acquires one connection from the pool,
    yields it to the caller, and releases it back on exit.

    Usage inside a route or service function:
        async with get_connection() as conn:
            rows = await conn.fetch("SELECT ...")
    """
    pool = get_pool()
    async with pool.acquire() as connection:
        yield connection
