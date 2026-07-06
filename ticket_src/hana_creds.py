from google.cloud import secretmanager

# def get_api_key(project_id, secret_id, version_id="latest"):
#                 client = secretmanager.SecretManagerServiceClient()
#                 name=f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"
#                 response = client.access_secret_version(request={"name":name})
#                 return response.payload.data.decode("UTF-8")

class HanaCreds:
   
        db_host = "33d3c496-d9a5-433a-be9e-6293667fb514.hana.prod-us10.hanacloud.ondemand.com"
        db_user = "BAINODEVHDB"
        db_password = "Seleccion@2026"
        db_port = 443
        table_name = "SLA_TICKETS_DATA"
        #open_ai_key = get_api_key("976023343538", "bainocular-api-key")

        