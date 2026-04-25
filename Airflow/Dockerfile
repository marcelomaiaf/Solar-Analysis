FROM apache/airflow:3.2.0-python3.12

USER airflow
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt


COPY --chmod=0755 docker-entrypoint.sh /opt/airflow/railway-entrypoint.sh
COPY dags/ /opt/airflow/dags/

ENTRYPOINT ["/opt/airflow/railway-entrypoint.sh"]
