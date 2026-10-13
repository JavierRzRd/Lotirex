#!/usr/bin/env bash
set -euo pipefail
LOTIREX_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$LOTIREX_ROOT"
exec "${LOTIREX_PYTHON:-$LOTIREX_ROOT/.venv/bin/python}" -m backend.cron --max-days 7
