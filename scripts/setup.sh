#!/usr/bin/env bash
# One-time setup on macOS/Linux: Python environment, packages, frontend build, .env
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"

# Vite and its bundler need Node.js 20.19+ or 22.12+
if ! command -v node >/dev/null 2>&1; then
  echo "Node.js was not found. Install the LTS version from https://nodejs.org and run this script again." >&2
  exit 1
fi
if ! node -e 'const [a, b] = process.versions.node.split(".").map(Number); process.exit((a === 20 && b >= 19) || (a === 22 && b >= 12) || a >= 23 ? 0 : 1)'; then
  echo "Node.js $(node --version) is too old for the frontend, which needs 22.12 or newer. Install the LTS version from https://nodejs.org and run this script again." >&2
  exit 1
fi
echo "Node.js $(node --version)"

echo "== Backend =="
cd "$root/backend"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-dev.txt
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created backend/.env: set COCKPIT_TOKEN and, for the AI voice, OPENAI_API_KEY"
fi

echo "== Frontend =="
cd "$root/frontend"
npm install
npm run build

echo "Setup done. Start the cockpit with ./scripts/run.sh"
