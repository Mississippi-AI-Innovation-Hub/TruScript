#!/bin/sh
set -e

echo "[msbn-api] Running database migrations..."
alembic upgrade head

echo "[msbn-api] Starting MSBN Transcript Verification API..."
# Use --reload in development so code changes are picked up without rebuilding
if [ "${APP_ENV:-development}" = "development" ]; then
  exec uvicorn main:app --host 0.0.0.0 --port 8000 --reload
else
  exec uvicorn main:app --host 0.0.0.0 --port 8000
fi
