#!/usr/bin/env bash
# Starts the backend, which also serves the built cockpit on http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")/../backend"
exec .venv/bin/python -m app
