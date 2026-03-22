FROM apache/airflow:2.10.5-python3.12

COPY docker-entrypoint.sh /opt/airflow/railway-entrypoint.sh
RUN chmod +x /opt/airflow/railway-entrypoint.sh

CMD ["/opt/airflow/railway-entrypoint.sh"]
