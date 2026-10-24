#!/usr/bin/env bash
set -e
uvicorn metals_research_daily_entrypoint:app --host 0.0.0.0 --port "${PORT:-8000}"
