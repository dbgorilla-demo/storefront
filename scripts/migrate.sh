#!/usr/bin/env sh
# Migration entrypoint used by the Helm pre-install/pre-upgrade Job.
# DATABASE_URL is injected from the CNPG-issued app secret. This is
# infrastructure code — the one place a DSN is read for schema management.
set -eu

echo "[migrate] waiting for database..."
python - <<'PY'
import os, time, sys
import psycopg
dsn = os.environ["DATABASE_URL"]
for attempt in range(60):
    try:
        with psycopg.connect(dsn, connect_timeout=3):
            print("[migrate] database reachable")
            sys.exit(0)
    except Exception as e:
        print(f"[migrate] not ready ({attempt}): {e}")
        time.sleep(2)
sys.exit("[migrate] database never became reachable")
PY

echo "[migrate] running alembic upgrade head"
alembic upgrade head
echo "[migrate] done"
