#!/bin/sh
set -e

echo "[msbn-api] Running database migrations..."
alembic upgrade head

echo "[msbn-api] Starting MSBN Transcript Verification API..."
exec uvicorn main:app --host 0.0.0.0 --port 8000
