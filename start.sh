#!/bin/bash
set -e

echo "Starting SMM PRIME Production Server..."

if [ -f ".venv/bin/python" ]; then
    PYTHON_BIN=".venv/bin/python"
else
    PYTHON_BIN="python3"
fi

$PYTHON_BIN run.py --host 0.0.0.0 --port 8000
