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

# Clean up corrupted migration revision before starting
# This preserves all DAG history and metadata while fixing the alembic issue
python - <<'PY'
import os
import sys
from sqlalchemy import create_engine, text

db_url = os.environ.get("AIRFLOW__DATABASE__SQL_ALCHEMY_CONN", "sqlite:////opt/airflow/data/airflow.db")

try:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        # Check if alembic_version table exists
        result = conn.execute(text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'alembic_version')"
        ))
        table_exists = result.scalar()
        
        if table_exists:
            # Delete the corrupted revision
            conn.execute(text("DELETE FROM alembic_version WHERE version_num = 'a1b2c3d4e5f6'"))
            conn.commit()
            print("✓ Cleaned up corrupted migration revision 'a1b2c3d4e5f6'")
        else:
            print("✓ alembic_version table does not exist yet (fresh database)")
except Exception as e:
    print(f"⚠ Could not clean migration table: {e}")
    print("  This is OK - Airflow will handle it during initialization")
PY

# Standalone bootstraps the metadata DB and starts the web UI + scheduler.
# Explicit host/port settings above ensure Railway can reach the service.
exec /entrypoint airflow standalone

