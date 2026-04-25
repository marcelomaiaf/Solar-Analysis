import datetime
from airflow.sdk import DAG
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator

with DAG(
    dag_id="telemtry_dag",
    start_date=datetime.datetime(2026, 1, 1),
    schedule="@daily",
    catchup=False
):
    get_plant_data = SQLExecuteQueryOperator(
        task_id="get_plant_data",
        conn_id="railway_postgres",
        sql="SELECT * FROM plants",
    )

    get_plant_data


#task 1: puxar dados cadastrais da usina [V]

#task 2: puxar dados de telemetria da usina
#task 3: puxar dados climáticos
#task 4: calcular geração esperada
#task 5: Identificar se está dentro do intervalo esperado de geração
#task 6: estimar perda em kwh e financeira
#task 7: relatório da LLM