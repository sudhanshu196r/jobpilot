#!/usr/bin/env bash
# Start JobPilot (macOS / Linux). Creates a virtualenv on first run.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip >/dev/null
fi
.venv/bin/pip install -q -r requirements.txt
exec .venv/bin/python -m app
