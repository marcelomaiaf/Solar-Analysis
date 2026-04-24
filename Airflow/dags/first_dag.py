import datetime

from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import dag


@dag(start_date=datetime.datetime(2021, 1, 1), schedule="@daily", catchup=False)
def generate_dag():
    EmptyOperator(task_id="task")


generate_dag()
