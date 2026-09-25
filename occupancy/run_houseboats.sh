#!/usr/bin/env bash
# Weekly houseboat cohort: exact unit decisions, machine calendars and separate
# blank day/night measurement sheets. No outgoing messages to operators.
set -euo pipefail
# Корень репо — от расположения скрипта (occupancy/ -> 1 уровень вверх), как REPO_ROOT
# в run_scheduled.py; жёсткий путь мешал бы переносу контура (аудит 14.09.2026).
REPO=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
cd "$REPO"
PY=$REPO/.venv/bin/python
S=$REPO/occupancy
ROOT=$REPO/agent-runtime/research/houseboats/occupancy
STARTED=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)
DRY=()
if [[ "${1:-}" == --dry-run ]]; then DRY=(--dry-run); shift; fi
[[ "$#" -eq 0 ]] || { echo 'usage: run_houseboats.sh [--dry-run]' >&2; exit 2; }
test -s "$ROOT/targets.json" && test -s "$ROOT/cohort-source.json" || { echo 'Нет активного поюнитного отбора: сначала rebuild.py apply' >&2; exit 2; }
# Same lock as the cohort applicator. Stable inode; never unlink lock files.
exec 9>"$ROOT/.tracking.lock"
flock -n 9 || { echo 'Контур хаусботов уже занят сбором или заменой отбора' >&2; exit 2; }
DRIVER=$($PY -c 'import json,sys; print(json.load(open(sys.argv[1]))["scheduled_script"])' "$ROOT/cohort-source.json")
set +e
$PY "$DRIVER" \
  --targets "$ROOT/targets.json" --recipes "$ROOT/recipes.json" \
  --snapshot-root "$ROOT/snapshots" --ledger-dir "$ROOT/ledger" \
  --out "$ROOT/occupancy.md" --out-html "$ROOT/occupancy.html" \
  --backup-dir "$ROOT/backup" \
  --planned-time "${PLANNED_TIME:-04:00}" --deadline-min 60 --no-notify "${DRY[@]}"
RC=$?
set -e
if [[ ${#DRY[@]} -gt 0 ]]; then exit "$RC"; fi
# Existing completed sheets are never overwritten. No calls, sending or ingest.
BUILDER=$($PY -c 'import json,sys; print(json.load(open(sys.argv[1]))["measurement_script"])' "$ROOT/cohort-source.json")
$PY "$BUILDER" measurements --targets "$ROOT/targets.json" --out-dir "$ROOT/calls" || RC=1
STATUS=success; [ "$RC" -eq 0 ] || STATUS=failed; [ "$RC" -eq 1 ] && STATUS=insufficient_data
# Строка в журнале прогонов внешнего контура (`cf` в витрину не входит) — best-effort.
$PY -m cf log-run --agent houseboat-occupancy-weekly --status "$STATUS" --trigger scheduled \
  --started-at "$STARTED" --input "$ROOT/targets.json" \
  --outputs "$ROOT/snapshots" "$ROOT/occupancy.md" "$ROOT/calls" \
  --errors "код прогона $RC" || true
exit "$RC"
