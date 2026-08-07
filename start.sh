#!/usr/bin/env bash
set -e
exec uvicorn app_postgres_runtime:app --host 0.0.0.0 --port "${PORT:-8000}"
