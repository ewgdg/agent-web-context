#!/usr/bin/env bash

# Wait for the desktop environment to be ready
/usr/bin/desktop_ready

echo "Starting FastAPI application..."

# Start the FastAPI application in foreground
/app/.venv/bin/uvicorn src.agent_web_context.main:app --host=0.0.0.0 --port=8000
