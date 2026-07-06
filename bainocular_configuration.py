from dotenv import load_dotenv
import os
from get_key import get_api_key
import sys
# sys.path.append(
#     os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# )
# from api.configuration_params import get_configuration_values
# from database_gcp import Base, engine, get_db, get_postgres_db_conn
# from typing import Annotated
# from fastapi import Depends
# from sqlalchemy.orm import Session
load_dotenv()


#DbSessionPostgres = Annotated[Session, Depends(get_postgres_db_conn)]

db_user_key=os.environ.get("DBUSER_KEY")
db_pwd_key=os.environ.get("DBPWD_KEY")
db_host_key=os.environ.get("DBHOST_KEY")
db_port_key=os.environ.get("DBPORT_KEY")
db_name_key=os.environ.get("DBNAME_KEY")
db_vector_name_key=os.environ.get("DBVECTORNAME_KEY")
my_project = os.environ.get("PROJECT")
ai_prod_key = os.environ.get("AI_PROD_KEY")

class ConfigParams:
    db_user=get_api_key(my_project, db_user_key)
    db_pwd=get_api_key(my_project, db_pwd_key)
    #db_host=get_api_key(my_project, db_host_key)
    db_host="10.10.2.2"
    db_port=get_api_key(my_project, db_port_key)
    db_name=get_api_key(my_project, db_name_key)
    db_vector_name=get_api_key(my_project, db_vector_name_key)
    openai_api_key=get_api_key(my_project, ai_prod_key)
    #api_integration_proxy = get_configuration_values(conn: DbSessionPostgres, "api_iflow_integration_url")









