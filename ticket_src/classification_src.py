import pandas as pd
import nltk
import string
import joblib
import json
from sklearn.model_selection import train_test_split
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import accuracy_score, classification_report
from nltk.tokenize import word_tokenize
from sqlalchemy.orm import Session
from database_gcp import Base, engine, get_db
from models import SlaTicketData
from typing import Annotated
try:
    from hdbcli import dbapi
except ImportError:
    dbapi = None
from ticket_src.hana_creds import HanaCreds
from ticket_src.ams_kedb import add_log
import psycopg2
import os
from dotenv import load_dotenv
from fastapi.params import Depends

load_dotenv()
DbSession = Annotated[Session, Depends(get_db)]

db_host = os.environ.get("supabase_db_host")
db_name=os.environ.get("supabase_db_name")
db_user=os.environ.get("supabase_db_username")
db_key=os.environ.get("supabase_db_key")
db_password = os.environ.get("supabase_db_password")
db_port = os.environ.get("supabase_db_port")

def get_connection():
    conn = psycopg2.connect(
        host=db_host,
        port=db_port,
        user=db_user,
        password=db_password,
        dbname=db_name,
        sslmode="require"
    )
    return conn
# joblib == 1.4.2
# nltk == 3.9.1

# app = Flask(__name__)
# nltk.download('punkt')
# nltk.download('punkt_tab')

# model_json = XGBClassifier()
# model_json.load_model('xgboost_cf_json.json')
logreg_model = joblib.load('models/logreg_cf_model.pkl')
label_encoder_model = joblib.load('models/logreg_cf_labelencoder.pkl')
tfidf_model = joblib.load('models/logreg_cf_tfidfvect.pkl')

depart_logreg_model = joblib.load('models/depart_logreg_cf_model.pkl')
depart_label_encoder_model = joblib.load('models/depart_logreg_cf_labelencoder.pkl')
depart_tfidf_model = joblib.load('models/depart_logreg_cf_tfidfvect.pkl')

def classify_area(sentence: str):
    
    if type(sentence) is str:
        raw_description = [sentence]
        description = tfidf_model.transform(raw_description)
        
        predicted_value = logreg_model.predict(description)
        #print(predicted_value)
        predicted_label = label_encoder_model.inverse_transform(predicted_value)
        #print("class:", predicted_label, type(predicted_label))
        return predicted_label[0]
    else:
        return "XXX"

def classify_department(sentence: str):
    if type(sentence) is str:
        raw_description = [sentence]
        description = depart_tfidf_model.transform(raw_description)
       
        predicted_value = depart_logreg_model.predict(description)
        #print(predicted_value)
        predicted_label = depart_label_encoder_model.inverse_transform(predicted_value)
        #print("class:", predicted_label, type(predicted_label))
        return predicted_label[0]
    else:
        return "XXX"

def predict_sentence(sentence: str):
    sentence_dict = {}
    sentence_dict['department'] = classify_department(sentence)
    sentence_dict['subfunctional_area'] = classify_area(sentence)
    sentence_dict['brand'] = extracting_brand(sentence)
    sentence_dict['location'] = extracting_country(sentence)
    sentence_dict['site'] = extracting_site(sentence)
    sentence_dict['text'] = sentence
    # sentence_dict['d_ticket_id'] = row[3]
    sentence_dict['z_review'] = str(classify_department(sentence)) + "_" + str(classify_area(sentence)) + "_" + str(extracting_brand(sentence)) + "_" + str(extracting_country(sentence)) + "_" + str(extracting_site(sentence))
    return sentence_dict

async def insert_data(df):
    connection = dbapi.connect(
        address = HanaCreds.db_host,
        port = int(HanaCreds.db_port),
        user = HanaCreds.db_user,
        password = HanaCreds.db_password,
        #encrypt=True,
        #sslValidateCertificate=False
    )
    cursor = connection.cursor()
    try:
        if cursor is not None:
            connection_log = {
                    "module_name": "Bainocular",
                    "program_name": "classification_src.py",
                    "user": "",
                    "log_type": "S",
                    "content": f"Connection establishment success: insert_data()"
            }

            resp = await add_log(connection_log)
            print(f"Logging_Status: {resp}")

        
            cursor.execute('''
                DROP TABLE SLA_TICKETS_DATA
                ''')

            delete_table_log = {
                    "module_name": "Bainocular",
                    "program_name": "classification_src.py",
                    "user": "",
                    "log_type": "S",
                    "content": f"Deleted the table sla_tickets_data: insert_data()"
                }

            resp = await add_log(delete_table_log)
            print(f"Logging Status: {resp}")

            create_table_query = f'''
                    CREATE COLUMN TABLE sla_tickets_data ("Req. Creation Date" DATE, "Creation Time" NVARCHAR(128), "Req. Creation Date - Year Week ISO" INTEGER, "Request - ID" NVARCHAR(128), "Request - Priority Description" NVARCHAR(128), "Historical Status - Status From" NVARCHAR(128), "Historical Status - Status To" NVARCHAR(128), "Historical Status - Change Date" DATE, "Historical Status - Change Time" NVARCHAR(128), "Macro Area - Name" NVARCHAR(256), "Request - Resource Assigned To - GROUP SAP MD" NVARCHAR(256), "Macro Area (SAP) - MA Area" NVARCHAR(128), "Request - User Name" NVARCHAR(256), "Request - Resource Assigned To - Name" NVARCHAR(256), "Req. Type - Description EN" NVARCHAR(128), "Req. Status - Description" NVARCHAR(128), "Req. Closing Date" DATE, "Request - Text Request" NCLOB, "Request - Text Answer" NVARCHAR(2788), "Request - Category" NVARCHAR(256), "Request - Subject description" NVARCHAR(512))
            '''
            cursor.execute(create_table_query)

            create_table_log = {
                    "module_name": "Bainocular",
                    "program_name": "classification_src.py",
                    "user": "",
                    "log_type": "S",
                    "content": f"created table sla_tickets_Data: insert_data()"
                }

            resp = await add_log(create_table_log)
            print(f"Logging Status: {resp}")

            placeholders = ",".join(["?"] * len(df.columns))
            insert_sql_query = f'''INSERT INTO SLA_TICKETS_DATA ("Req. Creation Date", "Creation Time", "Req. Creation Date - Year Week ISO", "Request - ID", "Request - Priority Description", "Historical Status - Status From", "Historical Status - Status To", "Historical Status - Change Date", "Historical Status - Change Time", "Macro Area - Name", "Request - Resource Assigned To - GROUP SAP MD", "Macro Area (SAP) - MA Area", "Request - User Name", "Request - Resource Assigned To - Name", "Req. Type - Description EN", "Req. Status - Description", "Req. Closing Date", "Request - Text Request", "Request - Text Answer", "Request - Category", "Request - Subject description")
                                    VALUES({placeholders})'''
            #values = []
            # for _, row in df.iterrows():
            #     values.append((row))

            values = [tuple(row) for row in df.to_numpy()]
            print(values[1])
            print(len(values))
            cursor.executemany(insert_sql_query, values)

            insert_values_log = {
                    "module_name": "Bainocular",
                    "program_name": "classification_src.py",
                    "user": "",
                    "log_type": "S",
                    "content": f"Inserted the tickets data into table: insert_data()"
                }

            resp = await add_log(insert_values_log)
            print(f"Logging Status: {resp}")

            connection.commit()
            cursor.close()
            connection.close()
            return {
                "response": "successfully added records to the Database"
                }
        else:

            connection_log = {
                    "module_name": "Bainocular",
                    "program_name": "classification_src.py",
                    "user": "",
                    "log_type": "E",
                    "content": f"Database connection failed: insert_data()"
            }

            resp = await add_log(connection_log)
            print(f"Logging_Status: {resp}")
            return {
                "response": "Database connection Failed"
            }

            
    except Exception as e:
        print(f"Error: {e}")
        error_log = {
                "module_name": "Bainocular",
                "program_name": "classification_src.py",
                "user": "",
                "log_type": "S",
                "content": f"Error in uploading ticket data into table: insert_data() - {e}"
            }

        resp = await add_log(error_log)
        print(f"Logging Status: {resp}")
        return {
            "response": "Something went wrong"
        }

async def get_data():
    # SELECT * FROM "fifteenhundred";
    try:

        print(f"HANA DB Host: {HanaCreds.db_host}")
        print(f"HANA DB Port: {HanaCreds.db_port}")
        print(f"HANA DB User: {HanaCreds.db_user}")
        print(f"HANA DB Password: {HanaCreds.db_password}")

        connection = dbapi.connect(
            address = HanaCreds.db_host,
            port = int(HanaCreds.db_port),
            user = HanaCreds.db_user,
            password = HanaCreds.db_password
        )

       
        cursor = connection.cursor()
        if cursor is not None:
            connection_log = {
                "module_name": "Bainocular",
                "program_name": "classification_src.py",
                "user": "",
                "log_type": "S",
                "content": f"Connection establishment success: get_data()"
            }

            resp = await add_log(connection_log)
            print(f"Logging_Status: {resp}")
        
        # sql_query = 'SELECT * FROM "fifteenhundred";'
            #sql_query = '''SELECT * FROM "bainocular_sla_data" WHERE "Historical Status - Status To" = 'Solved';'''
            sql_query = '''SELECT * FROM "SLA_TICKETS_DATA" WHERE "Historical Status - Status To" = 'Solved';'''
            cursor.execute(sql_query)
            resultset = cursor.fetchall()
            count = 0
            data_list = []

            if resultset and len(resultset) > 0:
                fetched_data_log = {
                    "module_name": "Bainocular",
                    "program_name": "classification_src.py",
                    "user": "",
                    "log_type": "S",
                    "content": f"Fetched data successfully: get_data()"
                }
                resp = await add_log(fetched_data_log)
                print(f"Logging Status: {resp}")

                for row in resultset:
                    data_dict = {}
                    print(count, row[17], '\n')
                    print('Department:', classify_department(row[17]))
                    print('Area:', classify_area(row[17]), '\n')
                    count = count + 1
                    data_dict['count'] = count
                    data_dict['department'] = classify_department(row[17])
                    data_dict['subfunctional_area'] = classify_area(row[17])
                    data_dict['brand'] = extracting_brand(row[17])
                    data_dict['location'] = extracting_country(row[17])
                    data_dict['site'] = extracting_site(row[17])
                    data_dict['text'] = row[17]
                    data_dict['d_ticket_id'] = row[3]
                    data_dict['z_review'] = str(classify_department(row[17])) + "_" + str(classify_area(row[17])) + "_" + str(extracting_brand(row[17])) + "_" + str(extracting_country(row[17])) + "_" + str(extracting_site(row[17]))
                    data_list.append(data_dict)
            else:
                empty_data_log = {
                    "module_name": "Bainocular",
                    "program_name": "classification_src.py",
                    "user": "",
                    "log_type": "S",
                    "content": f"Empty Data in database: get_data()"
                }

                resp = await add_log(empty_data_log)
                print(f"Logging Status: {resp}")

           


            cursor.close()
            connection.close()
            # return jsonify({"status": 200})
            return {"response": data_list}
        else:
            connection_log = {
                "module_name": "Bainocular",
                "program_name": "classification_src.py",
                "user": "",
                "log_type": "S",
                "content": f"Database Connection Failure: get_data()"
            }

            resp = await add_log(connection_log)
            print(f"Logging Status: {resp}")
    except Exception as e:
        error_log = {
                "module_name": "Bainocular",
                "program_name": "classification_src.py",
                "user": "",
                "log_type": "E",
                "content": f"Error in classification: get_data() - {e}"
            }
        resp = await add_log(error_log)
        print(f"Logging Status: {resp}")

async def get_data_gcp(db: Session):
    
    try:
              
        records = db.query(SlaTicketData).all()
        print(len(records))
        count = 0
        data_list = []

        if records and len(records) > 0:
            fetched_data_log = {
                "module_name": "Bainocular",
                "program_name": "classification_src.py",
                "user": "",
                "log_type": "S",
                "content": f"Fetched data successfully: get_data()"
            }
            resp = await add_log(fetched_data_log)
            print(f"Logging Status: {resp}")

            for row in records:
                data_dict = {}
               
                count = count + 1
                data_dict['count'] = count
                area = classify_department(str(row.RequestTextRequest or ""))
                subarea = classify_area(str(row.RequestTextRequest or ""))
                brand = extracting_brand(str(row.RequestTextRequest or ""))
                location = extracting_country(str(row.RequestTextRequest or ""))
                site = extracting_site(str(row.RequestTextRequest or ""))
                data_dict['department'] = area
                data_dict['subfunctional_area'] = subarea
                data_dict['brand'] = brand
                data_dict['location'] = location
                data_dict['site'] = site
                data_dict['text'] = str(row.RequestTextRequest or "")
                data_dict['d_ticket_id'] = row.RequestID
                data_dict['z_review'] = str(area) + "_" + str(subarea) + "_" + str(brand) + "_" + str(location) + "_" + str(site)
                data_list.append(data_dict)
        else:
            empty_data_log = {
                "module_name": "Bainocular",
                "program_name": "classification_src.py",
                "user": "",
                "log_type": "E",
                "content": f"Empty Data in database: get_data_gcp()"
            }

            resp = await add_log(empty_data_log)
            print(f"Logging Status: {resp}")

        


        classify_data_log = {
                    "module_name": "Bainocular",
                    "program_name": "classification_src.py",
                    "user": "",
                    "log_type": "S",
                    "content": f"Classification of records successful: get_data_gcp()"
                }

        resp = await add_log(classify_data_log)
        print(f"Logging Status: {resp}")
        return {"response": data_list}
        
    except Exception as e:
        error_log = {
                "module_name": "Bainocular",
                "program_name": "classification_src.py",
                "user": "",
                "log_type": "E",
                "content": f"Error in classification: get_data() - {e}"
            }
        resp = await add_log(error_log)
        print(f"Logging Status: {resp}")

def extracting_brand(sentence: str):
    if type(sentence) is not str:
        return "XXX"
    else:
        index = sentence.find("Brand :")
        if index == -1:
            return "XXX"
        else:
            brand = sentence[index+8:index+sentence[index:].find("..")]
            if brand == "":
                return "XXX"
            else:
                return brand

def extracting_country(sentence: str):
    if type(sentence) is not str:
        return "XXX"
    else:
        # index = sentence.find("COUNTRY :")
        if sentence.find("COUNTRY :") == -1 and sentence.find("Country :") == -1:
            return "XXX"
        else:
            index = sentence.find("COUNTRY :") if sentence.find("COUNTRY :") != -1 else sentence.find("Country :")
            country = "XXX" if sentence[index+10:index+10+sentence[index+10:].find("..")] == "" else sentence[index+10:index+10+sentence[index+10:].find("..")]
            return country

def extracting_site(sentence: str):
    if type(sentence) is not str:
        return "XXX"
    else:
        index = sentence.find("Site")
        last_index = sentence.find("..SAP User")
        if last_index == -1:
            return "XXX"
        else:
            site_number = "XXX" if index == -1 else sentence[index+9:sentence.find("..SAP User")]
            return site_number

					
						   

						  

											  
# @app.route('/process-file', methods=['POST'])
# def processing_file():
#     try:
#         file = request.files['file']
#         if not file or file.filename.endswith('.csv') is False:
#             return jsonify({"error": "Invalid file type."}), 400
        
#         df = pd.read_csv(file, encoding='windows-1252')

#         data = []
#         count = 1
#         for text in df['Request - Text Request']:
#             element = {}
#             element['Serial_Number'] = count
#             element['Text_Description'] = text
#             element['SubFunction'] = extracting_subfnc(text)
#             element['Brand'] = extracting_brand(text)
#             element['Country'] = extracting_country(text)
#             element['SITE'] = extracting_site(text)
#             count = count + 1
#             data.append(element)
        
#         return jsonify({"data": data})
#     except:
#         return jsonify({"data": "Something went wrong"})

# @app.route('/predict-json', methods=['POST'])
# def predict_json():
#     try:
#         raw_description = request.json.get("description")
#         description = tfidf.transform(raw_description)
#         predicted_value = model_json.predict(description)
#         print(predicted_value)
#         predicted_label = label_encoder.inverse_transform([predicted_value.argmax()])
#         print("class:", predicted_label, type(predicted_label))
#         # print("class:", predicted_value.tolist(), type(predicted_value))
#         return "<h1>Success</h1>"

#     except Exception as e:
#         return jsonify({"error": str(e)}), 500


# @app.route('/predict-model', methods=['POST'])
# def predict_model():
#     # Extract the string data from the request
#     data = request.get_json()
#     text_input = data.get('description', None)

#     if text_input is None:
#         return jsonify({'error': 'No text input provided!'}), 400

#     # Transform the input string using the TF-IDF vectorizer
#     text_vectorized = tfidf.transform([text_input])

#     # Get the prediction from the XGBoost model
#     print(text_vectorized, type(text_vectorized))
#     dmatrix = xgb.DMatrix(text_vectorized)  # XGBoost expects DMatrix for prediction
#     prediction = xgboost_model.predict(dmatrix)

#     # Convert prediction to label using the label encoder
#     predicted_label = label_encoder.inverse_transform([prediction.argmax()])

#     # Return the predicted label
#     return jsonify({'predicted_class': predicted_label[0]})
