#!/usr/bin/env bash
set -e
uvicorn metals_daily_summary_entrypoint:app --host 0.0.0.0 --port "${PORT:-8000}"
