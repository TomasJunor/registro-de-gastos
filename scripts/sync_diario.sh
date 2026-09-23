#!/usr/bin/env bash
# Corre la sincronización diaria y deja log. Pensado para cron / launchd / systemd.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/logs
LOG="data/logs/sync_$(date +%Y-%m-%d).log"
PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3
{
  echo "=== $(date '+%F %T') ==="
  "$PY" -m gastos sync "$@"
  "$PY" -m gastos exportar --meses 12 --salida data/reportes/gastos.xlsx
} >>"$LOG" 2>&1
