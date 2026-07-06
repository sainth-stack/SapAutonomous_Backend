from dotenv import load_dotenv
import os

load_dotenv()


class ConfigParams:
    db_user = os.environ.get("DB_USER", "")
    db_pwd = os.environ.get("DB_PASSWORD", "")
    db_host = os.environ.get("DB_HOST", "")
    db_port = os.environ.get("DB_PORT", "5432")
    db_name = os.environ.get("DB_NAME", "")
    db_vector_name = os.environ.get("DB_VECTOR_NAME", "")
    openai_api_key = os.environ.get("OPENAI_API_KEY", "")
