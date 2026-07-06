import os
from typing import Optional
from fastapi import FastAPI, Depends
from google.cloud.sql.connector import Connector
from sqlalchemy import create_engine, text
import uvicorn
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from psycopg2.pool import SimpleConnectionPool
from get_key import get_api_key
from dotenv import load_dotenv
from bainocular_configuration import ConfigParams

app = FastAPI()

load_dotenv()
#DATABASE_URL: Optional[str] = "postgresql://postgres:Bainocular123@34.69.106.128:5432/postgres"
DATABASE_URL: Optional[str] = f"postgresql://{ConfigParams.db_user}:{ConfigParams.db_pwd}@10.10.2.2:{ConfigParams.db_port}/{ConfigParams.db_name}"
if not DATABASE_URL:
    raise ValueError("DATABASE_URL not found")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    connect_args={"connect_timeout": 10},
)

SessionLocal = sessionmaker(autoflush=False, bind=engine, autocommit=False)

Base = declarative_base()

#db = SessionLocal()


pool = SimpleConnectionPool(
    minconn=1,
    maxconn=10,
    user='postgres',
    password='Bainocular123',
    host='10.10.2.2',
    database='postgres',
    port=5432
)

def get_db():
    db = SessionLocal()
    try:
        yield db
        print(f"FastAPI connected to postgreSql")
    finally:
        db.close()

def get_postgres_db_conn():
    """Get a raw psycopg2 connection from SQLAlchemy engine's pool."""
    connection = engine.raw_connection()
    try:
        yield connection
    finally:
       connection.close()
       
# def get_postgres_db_conn():
#     conn = pool.getconn()
#     try:
#         yield conn
#     finally:
#         pool.putconn(conn)
        
@app.get("/test-db")
def test_db(db: Session = Depends(get_db)):
    result = db.execute(text("SELECT 1"))
    return {"status": result.scalar()}
if __name__=="__main__":
    PORT = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=PORT)
