# Apache Airflow

Deploy Apache Airflow on Railway.

## Files in this template

- `Dockerfile` uses official `apache/airflow` image.
- `docker-entrypoint.sh` starts Airflow in standalone mode on Railway `$PORT`.
- `railway.toml` configures health check and restart policy.

## Environment variables

```bash
AIRFLOW_UID=50000
AIRFLOW__CORE__LOAD_EXAMPLES=False
_AIRFLOW_WWW_USER_USERNAME=admin
_AIRFLOW_WWW_USER_PASSWORD=replace-with-strong-password
```

## Persistent storage

Attach a Railway volume and mount to:

- `/opt/airflow/data`

## Notes

- Standalone mode is great for evaluation and light workloads.
- For production, split webserver, scheduler, worker, and use PostgreSQL + Celery/Kubernetes executor.
