#!/usr/bin/env bash
set -euo pipefail

export AIRFLOW__CORE__EXECUTOR="${AIRFLOW__CORE__EXECUTOR:-SequentialExecutor}"
export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="${AIRFLOW__DATABASE__SQL_ALCHEMY_CONN:-sqlite:////opt/airflow/data/airflow.db}"
export AIRFLOW__CORE__LOAD_EXAMPLES="${AIRFLOW__CORE__LOAD_EXAMPLES:-False}"
export AIRFLOW__API__HOST="${AIRFLOW__API__HOST:-0.0.0.0}"
export AIRFLOW__API__PORT="${AIRFLOW__API__PORT:-${PORT:-8080}}"

mkdir -p /opt/airflow/data

if [[ -n "${_AIRFLOW_WWW_USER_USERNAME:-}" && -n "${_AIRFLOW_WWW_USER_PASSWORD:-}" ]]; then
  python - <<'PY'
import json
import os
from pathlib import Path

passwords_file = Path(os.environ.get(
    "AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE",
    "/opt/airflow/simple_auth_manager_passwords.json.generated",
))
passwords_file.write_text(
    json.dumps({os.environ["_AIRFLOW_WWW_USER_USERNAME"]: os.environ["_AIRFLOW_WWW_USER_PASSWORD"]})
    + "\n"
)
PY
fi

# Standalone bootstraps the metadata DB and starts the web UI + scheduler.
# Explicit host/port settings above ensure Railway can reach the service.
exec /entrypoint airflow standalone
