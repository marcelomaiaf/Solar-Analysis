FROM apache/airflow:3.2.0-python3.12

COPY --chmod=0755 docker-entrypoint.sh /opt/airflow/railway-entrypoint.sh
COPY dags/ /opt/airflow/dags/

ENTRYPOINT ["/opt/airflow/railway-entrypoint.sh"]
