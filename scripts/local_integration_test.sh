#!/usr/bin/env bash
set -euo pipefail

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [[ -z "${GROUPME_TOKEN:-}" || -z "${GROUP_ID:-}" ]]; then
  echo "GROUPME_TOKEN and GROUP_ID must be set for local integration testing." >&2
  exit 2
fi

python scripts/groupme_to_ics.py --dry-run --verbose
python scripts/groupme_to_ics.py --output /tmp/calendar.ics --verbose

echo "Local integration test completed in .venv"
