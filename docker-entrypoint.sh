#!/usr/bin/env bash
set -euo pipefail

export AIRFLOW__CORE__EXECUTOR="${AIRFLOW__CORE__EXECUTOR:-SequentialExecutor}"
export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="${AIRFLOW__DATABASE__SQL_ALCHEMY_CONN:-sqlite:////opt/airflow/data/airflow.db}"
export AIRFLOW__CORE__LOAD_EXAMPLES="${AIRFLOW__CORE__LOAD_EXAMPLES:-False}"
export AIRFLOW__WEBSERVER__WEB_SERVER_HOST="${AIRFLOW__WEBSERVER__WEB_SERVER_HOST:-0.0.0.0}"
export AIRFLOW__WEBSERVER__WEB_SERVER_PORT="${AIRFLOW__WEBSERVER__WEB_SERVER_PORT:-${PORT:-8080}}"

mkdir -p /opt/airflow/data

# Standalone bootstraps the metadata DB and starts the web UI + scheduler.
# Explicit host/port settings above ensure Railway can reach the service.
exec /entrypoint airflow standalone
