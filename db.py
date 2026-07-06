from typing import dataclass_transform
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from fastapi import Depends, FastAPI
import uvicorn
import os
from dotenv import load_dotenv
from bainocular_configuration import ConfigParams

app = FastAPI()

load_dotenv()
pool = SimpleConnectionPool(
    minconn=1,
    maxconn=10,
    user='postgres',
    password='Bainocular123',
    host='10.10.2.2',
    database='ragdb',
    port=5432
)

# pool = SimpleConnectionPool(
#     minconn=1,
#     maxconn=10,
#     user=ConfigParams.db_user,
#     password=ConfigParams.db_pwd,
#     host=ConfigParams.db_host,
#     database=ConfigParams.db_vector_name,
#     port=ConfigParams.db_port
# )

def get_db_connection():
    conn = pool.getconn()
    try:
        yield conn
    finally:
        pool.putconn(conn)

@app.get("/test-db-connection")
def test_db_connection(conn=Depends(get_db_connection)):
    with conn.cursor() as cursor:
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        return result


if __name__=="__main__":
    PORT = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=PORT)