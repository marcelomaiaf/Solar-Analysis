import datetime
from airflow.sdk import DAG
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator

sql_query = """
            select name,vendor,timezone, 
                   capacity_kwp,location,address,
                   latitude, longitude,azimuth_deg,tilt_deg	, 
                   vendor_plant_id,vendor_device_id,vendor_timezone, vendor_metadata_json 
            from plants p join vendor_plant_links v on p.id = v.plant_id where vendor = 'weg'
            """

with DAG(
    dag_id="telemtry_dag",
    start_date=datetime.datetime(2026, 1, 1),
    schedule="@daily",
    catchup=False
):
    get_plant_data = SQLExecuteQueryOperator(
        task_id="get_plant_data",
        conn_id="railway_postgres",
        sql=sql_query,
    )

    get_plant_data


#task 1: puxar dados cadastrais da usina [V]

#task 2: puxar dados de telemetria da usina
#task 3: puxar dados climáticos
#task 4: calcular geração esperada
#task 5: Identificar se está dentro do intervalo esperado de geração
#task 6: estimar perda em kwh e financeira
#task 7: relatório da LLM