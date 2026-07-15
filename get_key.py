from dotenv import load_dotenv
import os

load_dotenv()


def get_api_key(env_var_name, default=""):
    """Read a secret/API key from an environment variable (.env)."""
    return os.environ.get(env_var_name, default)
