try:
    from hdbcli import dbapi
except ImportError:
    dbapi = None
from sqlalchemy.engine import cursor
from ticket_src.hana_creds import HanaCreds
from dotenv import load_dotenv
from openai import OpenAI
import os
import sys
import pandas as pd
from typing import cast, Any
from ticket_src.classification_src import add_log
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)
from bainocular_configuration import ConfigParams


load_dotenv()

SYSTEM_PROMPT = """
You are an expert postgres SQL assistant for a ticketing system.

Generate ONLY valid postgresQL SELECT queries. Do not include explanations, comments, or any non-SQL text.

--------------------------------------------------
TABLE DETAILS
--------------------------------------------------
Table name: "sla_tickets_data"

Columns:
- ReqCreationDate           (date, format YYYY-MM-DD)              
- CreationTime              (text, format HH:MM:SS)            
- ReqCreationDateYearWeekISO(text, format YYYYMM)             
- RequestID    (text)
- RequestPriorityDescription(text)             
- HistoricalStatusStatusFrom(text)             
- HistoricalStatusStatusTo  (text)             
- HistoricalStatusChangeDate(date, format YYYY-MM-DD)              
- HistoricalStatusChangeTime(text, format HH:MM:SS)             
- MacroAreaName             (text)             
- RequestResourceAssignedToGROUPSAPMD (text)              
- MacroArea                 (text)             
- RequestUserName           (text)             
- RequestResourceAssignedToName (text)              
- ReqTypeDescription        (text)             
- ReqStatusDescription      (text)             
- ReqClosingDate            (date, format YYYY-MM-DD)              
- RequestTextRequest        (text)             
- RequestTextAnswer         (text)             
- RequestCategory           (text)             
- RequestSubjectdescription (text)
--------------------------------------------------
STRICT RULES
--------------------------------------------------
1. ONLY generate SELECT queries.
2. NEVER use INSERT, UPDATE, DELETE, DROP, ALTER.
3. ALWAYS include: LIMIT 50 (unless explicitly requested otherwise).
4. NEVER use SELECT DISTINCT * (NCLOB columns exist).
5. Avoid using DISTINCT on NCLOB columns.
6. Always wrap column names in double quotes.
7. Use case-insensitive filtering:
   LOWER(column) LIKE LOWER('%value%')
8. Use explicit column selection instead of * when possible.

--------------------------------------------------
BUSINESS LOGIC RULES
--------------------------------------------------
1. Ticket uniqueness:
   - Use ROW_NUMBER() OVER (PARTITION BY "RequestID") when deduplication is required.
   - Always return the latest record using:
     ORDER BY "HistoricalStatusChangeDate" DESC, "HistoricalStatusChangeTime" DESC

2. Status handling:
   - Use "Historical Status - Status To" for filtering current/latest status.
   - Ticket lifecycle:
     'To be defined' → ... → 'Closed' / 'Discarded' / 'Suspended'

3. Priority mapping:
   - High priority → 'P2 - High'
   - Medium priority → 'P3 - Normal'
   - Low priority → 'P4 - Low'

4. Person queries:
   - Default to "RequestResourceAssignedToName"

5. Date filtering:
   - Always use "HistoricalStatusChangeDate"
   - "today" → CURRENT_DATE

--------------------------------------------------
SLA CALCULATION RULES
--------------------------------------------------
1. Resolution time:
   - Use:
     SECONDS_BETWEEN(
       TO_TIMESTAMP("ReqCreationDate" || ' ' || "CreationTime"),
       TO_TIMESTAMP("HistoricalStatusChangeDate" || ' ' || "HistoricalStatusChangeTime")
     ) / 3600

2. SLA thresholds:
   - P1 - Critical → 4 hours
   - P2 - High → 9 hours
   - P3 - Normal → 45 hours
   - P4 - Low → 90 hours

3. SLA breach:
   - Compare resolution time with threshold
   - Return status as:
     'Breached' or 'Within SLA'

--------------------------------------------------
ANALYSIS RULES
--------------------------------------------------
If user asks for analysis:
- Include:
  - Total tickets
  - Breached vs non-breached
  - Status breakdown (Closed, In Progress, etc.)
  - Group by priority, assignee, or date if relevant

--------------------------------------------------
OUTPUT RULES
--------------------------------------------------
- Return ONLY SQL query
- No explanations
- No comments
- No markdown
"""

async def run_sql_query(conn, query: str):
    try:
        

        cursor = conn.cursor()

        if cursor is not None:
            connection_log = {
                        "module_name": "Bainocular",
                        "program_name": "free_text_analysis.py",
                        "user": "",
                        "log_type": "S",
                        "content": f"Connection to database established successfully: run_sql_query()"
                }

            resp = await add_log(connection_log)
            print(f"Logging_Status: {resp}")

            cursor.execute(query)
            rows = cursor.fetchall()
            if rows is not None:
                fetch_log = {
                        "module_name": "Bainocular",
                        "program_name": "free_text_analysis.py",
                        "user": "",
                        "log_type": "S",
                        "content": f"Data Fetched Successfully: run_sql_query()"
                }

                resp = await add_log(fetch_log)
                print(f"Logging_Status: {resp}")
                columns = [desc[0] for desc in cursor.description]

                cursor.close()
                conn.close()

                return{
                    "columns": columns,
                    "rows": rows
                }
            else:
                fetch_log = {
                        "module_name": "Bainocular",
                        "program_name": "free_text_analysis.py",
                        "user": "",
                        "log_type": "E",
                        "content": f"Empty data fetched: run_sql_query()"
                }

                resp = await add_log(fetch_log)
                print(f"Logging_Status: {resp}")
                return {
                    
                    "columns": [],
                    "rows": []
                }
        else:
            connection_log = {
                        "module_name": "Bainocular",
                        "program_name": "free_text_analysis.py",
                        "user": "",
                        "log_type": "E",
                        "content": f"Connection to database Failed: run_sql_query()"
                }

            resp = await add_log(connection_log)
            print(f"Logging_Status: {resp}")
            return {
                "columns": [],
                "rows": []
            }
    except Exception as e:
        err_log = {
                        "module_name": "Bainocular",
                        "program_name": "free_text_analysis.py",
                        "user": "",
                        "log_type": "E",
                        "content": f"Empty data fetched: run_sql_query()"
                }

        resp = await add_log(err_log)
        print(f"Logging_Status: {resp}")
        return{
            "error": str(e)
        }

async def generate_sql(user_input):
    
    client = OpenAI(api_key=ConfigParams.openai_api_key)

    if client is not None:
        genai_log = {
                        "module_name": "Bainocular",
                        "program_name": "free_text_analysis.py",
                        "user": "",
                        "log_type": "S",
                        "content": f"Connection with LLM established Successfully: generate_sql()"
                }

        resp = await add_log(genai_log)
        print(f"Logging_Status: {resp}")
        
    try:
        response = client.chat.completions.create(
            model="gpt-5-nano",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_input}
            ]
        )


        content = response.choices[0].message.content
        if content is not None:
            result_log = {
                    "module_name": "Bainocular",
                    "program_name": "free_text_analysis.py",
                    "user": "",
                    "log_type": "S",
                    "content": f"LLM response received successfully: {content} - generate_sql()"
                }

            resp = await add_log(result_log)
            print(f"Logging_Status: {resp}")
            
            return content.strip()
        else:
            result_log = {
                    "module_name": "Bainocular",
                    "program_name": "free_text_analysis.py",
                    "user": "",
                    "log_type": "S",
                    "content": f"Empty LLM response: {content} - generate_sql()"
                }

            resp = await add_log(result_log)
            print(f"Logging_Status: {resp}")
            return "Error: Empty response from model."
    except Exception as e:
        result_log = {
                    "module_name": "Bainocular",
                    "program_name": "free_text_analysis.py",
                    "user": "",
                    "log_type": "E",
                    "content": f"Error Ocurred in Processing User Query - generate_sql()"
                }

        resp = await add_log(result_log)
        print(f"Logging_Status: {resp}")
        return f"Error: {str(e)}"

   

async def replica_chatbot(conn, user_input):
    try:
        #step-1: Generate SQL
        if user_input is not None:
            result_log = {
                    "module_name": "Bainocular",
                    "program_name": "free_text_analysis.py",
                    "user": "",
                    "log_type": "S",
                    "content": f"User input received: replica_chatbot()"
                }

            resp = await add_log(result_log)
            print(f"Logging_Status: {resp}")
            sql_query = await generate_sql(user_input)
            if sql_query is not None:
                result_log = {
                    "module_name": "Bainocular",
                    "program_name": "free_text_analysis.py",
                    "user": "",
                    "log_type": "S",
                    "content": f"Successfully generated SQL query: replica_chatbot()"
                }

                resp = await add_log(result_log)
                print(f"Logging_Status: {resp}")
                print(f"sql query generated: {sql_query}")
            print(f"sql query generated by LLM: {sql_query}")
            if not sql_query or "error" in sql_query.lower():

                result_log = {
                    "module_name": "Bainocular",
                    "program_name": "free_text_analysis.py",
                    "user": "",
                    "log_type": "E",
                    "content": f"Failed to generate valid SQL: replica_chatbot()"
                }

                resp = await add_log(result_log)
                print(f"Logging_Status: {resp}")
                return {"error": "Failed to generate valid SQL", "data": None}

            # step-2: Execute SQL
            result = await run_sql_query(conn, sql_query)
            if result is not None:
                result_log = {
                    "module_name": "Bainocular",
                    "program_name": "free_text_analysis.py",
                    "user": "",
                    "log_type": "S",
                    "content": f"data fetched successfully from run_sql_query(): {result} replica_chatbot()"
                }

                resp = await add_log(result_log)
                print(f"Logging_Status: {resp}")
            print(f"Result: {result}")
            print(type(result))

            if not isinstance(result, dict):
                result_log = {
                    "module_name": "Bainocular",
                    "program_name": "free_text_analysis.py",
                    "user": "",
                    "log_type": "E",
                    "content": f"Invalid response format: replica_chatbot()"
                }

                resp = await add_log(result_log)
                print(f"Logging_Status: {resp}")
                return {"error": "Invalid DB response format", "data": None}
            
            if result.get("error"):
                result_log = {
                    "module_name": "Bainocular",
                    "program_name": "free_text_analysis.py",
                    "user": "",
                    "log_type": "E",
                    "content": f"Error occured in response: replica_chatbot()"
                }

                resp = await add_log(result_log)
                print(f"Logging_Status: {resp}")
                return {"error": result["error"], "data": None}

            rows = result.get("rows", [])
            columns = result.get("columns", [])
            #step-3: Format response 
            # if "error" in result:
            #     return f"Error executing query: {result['error']}"

            formatted = [
                    dict(zip(columns, row))
                    for row in rows
                ]

            return {"error": None, "data": formatted}
        else:
            result_log = {
                    "module_name": "Bainocular",
                    "program_name": "free_text_analysis.py",
                    "user": "",
                    "log_type": "E",
                    "content": f"No User input received: replica_chatbot()"
                }

            resp = await add_log(result_log)
            print(f"Logging_Status: {resp}")
            return {"error": "Empty User Response", "data": None}
    except Exception as e:

        error_log = {
                    "module_name": "Bainocular",
                    "program_name": "free_text_analysis.py",
                    "user": "",
                    "log_type": "E",
                    "content": f"Error occured in processing user query:{e} replica_chatbot()"
                }

        resp = await add_log(error_log)
        print(f"Logging_Status: {resp}")
        return {"error": str(e), "data": None}



async def insert_table_replica_values():
    """
    -- -- SELECT "Req. Creation Date" AS "Created Date", "Creation Time", "Request - ID" AS "Ticket ID", "Request - Priority Description" AS "Ticket Priority", "Macro Area - Name" AS "Department", "Request - User Name" AS "Created By", "Request - Resource Assigned To - Name" AS "Assigned To", "Request - Text Request" AS "Problem Description", "Request - Text Answer" AS "Solution Comments" FROM "bainocular_sla_data" WHERE "Historical Status - Status To" = 'Solved';
    -- CREATE TABLE bainocular_enquirer (
    --     ticket_id VARCHAR(20),
    --     created_date VARCHAR(20),
    --     created_time VARCHAR(20),
    --     created_by VARCHAR(50),
    --     ticket_priority VARCHAR(20),
    --     department VARCHAR(20),
    --     ticket_assigned_to VARCHAR(20),
    --     problem_description VARCHAR(5000),
    --     solution_comments VARCHAR(5000)
    -- );
    SELECT COUNT(*) FROM bainocular_enquirer;
    -- ALTER TABLE bainocular_enquirer ALTER (ticket_assigned_to VARCHAR(50));
    -- DELETE FROM bainocular_enquirer;
    """
    connection = dbapi.connect(
        address = HanaCreds.db_host,
        port = int(HanaCreds.db_port),
        user = HanaCreds.db_user,
        password = HanaCreds.db_password,
    )
    cursor = connection.cursor()
    # sql_query = f'SELECT * FROM "{HanaCreds.table_name}";'
    sql_query = f'''SELECT "Req. Creation Date" AS "Created Date", "Creation Time", "Request - ID" AS "Ticket ID", "Request - Priority Description" AS "Ticket Priority", "Macro Area - Name" AS "Department", "Request - User Name" AS "Created By", "Request - Resource Assigned To - Name" AS "Assigned To", "Request - Text Request" AS "Problem Description", "Request - Text Answer" AS "Solution Comments" FROM "{HanaCreds.table_name}" WHERE "Historical Status - Status To" = 'Solved';'''
    cursor.execute(sql_query)
    rows = cursor.fetchall()
    description = cursor.description or []
   
    columns = [str(col[0]) for col in description]

    df = pd.DataFrame(rows, columns=pd.Index(columns))
    if len(df) > 0:
        cursor.execute('''
                DELETE FROM "BAINODEVHDB"."BAINOCULAR_ENQUIRER";
                ''')
    print(df["Ticket ID"].head(20))
    data = [
        (
            row['Ticket ID'],
            row['Created Date'],
            row['Creation Time'],
            row['Created By'],
            row['Ticket Priority'],
            row['Department'],
            row['Assigned To'],
            row['Problem Description'],
            row['Solution Comments']
        )
        for _, row in df.iterrows()
    ]
    
    cursor.executemany("""
            INSERT INTO bainocular_enquirer (
                ticket_id, created_date, created_time, created_by,
                ticket_priority, department, ticket_assigned_to,
                problem_description, solution_comments
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", data)
        
    connection.commit()
    cursor.close()
    connection.close()

    return {"msg": "Insertion successful..."}
    
async def get_database_to_file(upload_directory):
    connection = dbapi.connect(
        address = HanaCreds.db_host,
        port=int(HanaCreds.db_port),
        user=HanaCreds.db_user,
        password=HanaCreds.db_password
    )

    cursor = connection.cursor()
    
    if cursor is not None:

        connection_log = {
                    "module_name": "Bainocular",
                    "program_name": "free_text_analysis.py",
                    "user": "",
                    "log_type": "S",
                    "content": f"Connection establishment success: get_database_to_file()"
            }
        
        resp = await add_log(connection_log)
        print(f"Logging_Status: {resp}")
        
        sql_query = f'SELECT * FROM "{HanaCreds.table_name}";'

        cursor.execute(sql_query)
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]

        if rows is not None:

            fetch_log = {
                        "module_name": "Bainocular",
                        "program_name": "free_text_analysis.py",
                        "user": "",
                        "log_type": "S",
                        "content": f"Data fetched successfully: get_database_to_file()"
                }
            
            resp = await add_log(fetch_log)
            print(f"Logging_Status: {resp}")

            try:
                df = pd.DataFrame(rows, columns=cast(Any, cols))

                file_path = os.path.join(upload_directory, "output.csv")
                df.to_csv(file_path, index=False)

                csv_text = df[df["Historical Status - Status To"] == "Closed"].head(20).to_csv(index=False)
                print(f"Excel File saved in upload folder: {csv_text}")

                df1 = pd.read_csv(file_path)
                print(df1.head())

                return csv_text
            except Exception as e:
                err_log =  {
                        "module_name": "Bainocular",
                        "program_name": "free_text_analysis.py",
                        "user": "",
                        "log_type": "E",
                        "content": f"Error Occured: get_database_to_file()"
                }
                
                resp = await add_log(err_log)
                print(f"Logging_Status: {resp}")
                return None
        else:
            fetch_log = {
                        "module_name": "Bainocular",
                        "program_name": "free_text_analysis.py",
                        "user": "",
                        "log_type": "W",
                        "content": f"No data fetched: get_database_to_file()"
                }
            resp = await add_log(fetch_log)
            print(f"Logging_Status: {resp}")
            return None
    else:
        connection_log = {
                    "module_name": "Bainocular",
                    "program_name": "free_text_analysis.py",
                    "user": "",
                    "log_type": "E",
                    "content": f"Something went wrong in connection establishment: get_database_to_file()"
            }
        
        resp = await add_log(connection_log)
        print(f"Logging_Status: {resp}")
        

async def process_llm_with_file(user_query, excel_file_path):

    try:
        client = OpenAI(api_key=os.environ.get("OPEN_API_KEY"))

        if client is not None:

            ai_log = {
                    "module_name": "Bainocular",
                    "program_name": "free_text_analysis.py",
                    "user": "",
                    "log_type": "S",
                    "content": f"connection with LLMestablished: process_llm_with_file()"
            }

            resp = await add_log(ai_log)
            print(f"Logging_Status: {resp}")


            messages = [
                {"role": "system", "content": "You are answering questions using the CSV data"},
                {"role": "user", "content": f"CSV DATA: {excel_file_path}.  Question: {user_query}"}
            ]

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=cast(Any, messages)
            )
            
            result = response.choices[0].message.content
            if result is not None:
                
                result_log = {
                    "module_name": "Bainocular",
                    "program_name": "free_text_analysis.py",
                    "user": "",
                    "log_type": "S",
                    "content": f"LLM response received successfully: {result} - process_llm_with_file()"
                }

                resp = await add_log(result_log)
                print(f"Logging_Status: {resp}")
                return result
            else:
                result_log = {
                    "module_name": "Bainocular",
                    "program_name": "free_text_analysis.py",
                    "user": "",
                    "log_type": "S",
                    "content": f"Empty LLM response received: {result} - process_llm_with_file()"
                }

                resp = await add_log(result_log)
                print(f"Logging_Status: {resp}")
                return None
    except Exception as e:
        err_log = {
                    "module_name": "Bainocular",
                    "program_name": "free_text_analysis.py",
                    "user": "",
                    "log_type": "S",
                    "content": f"Error occured in LLM processing: process_llm_with_file()"
                }
        resp = await add_log(err_log)
        print(f"Logging_Status: {resp}")
        return None

async def get_classified_ticket_database():
    connection = dbapi.connect(
        address = HanaCreds.db_host,
        port=HanaCreds.db_port,
        user=HanaCreds.db_user,
        password=HanaCreds.db_password
    )

    cursor = connection.cursor()
    if cursor is not None:
        connection_log = {
                    "module_name": "Bainocular",
                    "program_name": "free_text_analysis.py",
                    "user": "",
                    "log_type": "S",
                    "content": f"Connection establishment success: get_classified_ticket_database()"
            }

        resp = await add_log(connection_log)
        print(f"Logging_Status: {resp}")

        sql_query = f'''SELECT "Request - ID", "Request - Priority Description", "Macro Area - Name", "Request - Subject description" FROM "{HanaCreds.table_name}" WHERE "Historical Status - Status To" = 'Solved';'''
        cursor.execute(sql_query)
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]

        if rows is not None:

            fetch_log = connection_log = {
                    "module_name": "Bainocular",
                    "program_name": "free_text_analysis.py",
                    "user": "",
                    "log_type": "S",
                    "content": f"Fetched Data successfully: get_classified_ticket_database()"
            }

            resp = await add_log(fetch_log)
            print(f"Logging_Status: {resp}")
            try:
                df = pd.DataFrame(rows, columns=cast(Any, columns))
                print("Raw Dataframe: ", df)

                base = df['Request - Subject description'].str.split(':', n=1).str[0]

                df['department'] = base.str.split('_').str[0]
                df['area'] = base.str.split('_').str[1]
                print(df['department'])
                print(df['area'])

                #Replace missing values with others
                df[['department', 'area']] = df[['department', 'area']].fillna('OTHERS')

                #If no underscore at all -> force both to others
                mask = ~base.str.contains('_', na=False)
                df.loc[mask, ['department', 'area']] = 'OTHERS'

                result = {}

                #Group by department
                for dept, group in df.groupby('department'):
                    result[dept] = {
                        "count": len(group),
                        "area": group['area'].value_counts().to_dict()
                    }

                print(result)
                return result
            except Exception as e:
                error_log = {
                    "module_name": "Bainocular",
                    "program_name": "free_text_analysis.py",
                    "user": "",
                    "log_type": "S",
                    "content": f"Fetched Data successfully: get_classified_ticket_database()"
                }

                resp = await add_log(error_log)
                print(f"Logging_Status: {resp}")
                return {}


        else:
            fetch_log = connection_log = {
                    "module_name": "Bainocular",
                    "program_name": "free_text_analysis.py",
                    "user": "",
                    "log_type": "W",
                    "content": f"Empty records fetched: get_classified_ticket_database()"
            }

            resp = await add_log(fetch_log)
            print(f"Logging_Status: {resp}")
    else:
        connection_log = {
                    "module_name": "Bainocular",
                    "program_name": "free_text_analysis.py",
                    "user": "",
                    "log_type": "E",
                    "content": f"Connection database Failure: get_classified_ticket_database()"
            }

        resp = await add_log(connection_log)
        print(f"Logging_Status: {resp}")


   