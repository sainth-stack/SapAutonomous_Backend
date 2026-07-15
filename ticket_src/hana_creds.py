from dotenv import load_dotenv
import os

load_dotenv()


class HanaCreds:

        db_host = os.environ.get(
            "HANA_DB_HOST",
            "33d3c496-d9a5-433a-be9e-6293667fb514.hana.prod-us10.hanacloud.ondemand.com",
        )
        db_user = os.environ.get("HANA_DB_USER", "BAINODEVHDB")
        db_password = os.environ.get("HANA_DB_PASSWORD", "Seleccion@2026")
        db_port = int(os.environ.get("HANA_DB_PORT", "443"))
        table_name = os.environ.get("HANA_DB_TABLE", "SLA_TICKETS_DATA")
