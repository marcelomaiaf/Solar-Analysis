import datetime
import json
import os

from cryptography.fernet import Fernet
from airflow.sdk import dag, task
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator

key = os.getenv("CREDENTIALS_ENCRYPTION_KEY")
sql_query = """
            select
                p.name,
                v.vendor,
                p.timezone,
                p.capacity_kwp,
                p.location,
                p.address,
                p.latitude,
                p.longitude,
                p.azimuth_deg,
                p.tilt_deg,
                v.plant_id,
                v.vendor_account_id,
                v.vendor_plant_id,
                v.vendor_device_id,
                v.vendor_timezone,
                a.auth_type,
                a.credentials_encrypted
            from plants p
            join vendor_plant_links v
                on p.id = v.plant_id
            join vendor_accounts a
                on p.tenant_id = a.tenant_id
            and a.vendor = v.vendor
            where v.vendor = 'weg';
            """ # reduzir o número de campos puxados para o stritamente necessário
def decrypt(encrypted_text,key):
    key = Fernet(key)
    decrypt = key.decrypt(encrypted_text)
    return json.loads(decrypt)

def row_as_dict(cursor):
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns,row)) for row in cursor.fetchall()]

@dag(
    dag_id="weg_analysis",
    start_date=datetime.datetime(2026, 1, 1),
    schedule="@daily",
    catchup=False,
    tags=["example"],
)

def weg_analysis():   
    #task 1: puxar dados cadastrais da usina
    get_plant_data = SQLExecuteQueryOperator(
        task_id="get_plant_data",
        conn_id="railway_postgres",
        sql=sql_query,
        handler=row_as_dict,
    )

    @task
    def get_telemetry(plants):
        for i in plants:
            print(i)

    #task 2: puxar dados de telemetria da usina
    get_telemetry(get_plant_data.output)

weg_analysis()
#task 3: puxar dados climáticos
#task 4: calcular geração esperada
#task 5: Identificar se está dentro do intervalo esperado de geração
#task 6: estimar perda em kwh e financeira
#task 7: relatório da LLM