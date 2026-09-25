#!/usr/bin/env bash
# Ежедневная публикация двух страниц по ПОСТОЯННЫМ ссылкам после планового
# прогона. Зачем отдельный юнит: таймер cf-occupancy держит свежими данные и
# html на диске, но инструмент публикации есть только у агентской сессии —
# systemd им не владеет. Поэтому здесь поднимается headless-сессия Claude Code
# с одной задачей и жёстким запретом на всё остальное.
#
# Предохранители:
#  - не публикуем, если сегодняшний прогон не закрыт (run.json без finished_at):
#    страница со вчерашними цифрами лучше страницы с половиной сегодняшних;
#  - бинарь агента берётся из CLAUDE_BIN, fallback — PATH (`command -v claude`).
set -euo pipefail
# Корень репо — от расположения скрипта (occupancy/ -> 1 уровень вверх), как REPO_ROOT
# в run_scheduled.py; жёсткий путь мешал бы переносу контура (аудит 14.09.2026).
REPO=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
cd "$REPO"

TODAY=${PUBLISH_DATE:-$(date +%Y-%m-%d)}   # PUBLISH_DATE — только для ручной проверки
LAST=$(ls -1d agent-runtime/research/glamping/occupancy/snapshots/${TODAY}-* 2>/dev/null | tail -1 || true)
if [ -z "$LAST" ] || ! .venv/bin/python -c "import json,sys; d=json.load(open('$LAST/run.json')); sys.exit(0 if d.get('finished_at') else 1)"; then
  echo "публикация пропущена: сегодняшний прогон не найден или не закрыт ($LAST)" >&2
  exit 0
fi

# Бинарь агента: CLAUDE_BIN из окружения, иначе первый `claude` в PATH.
CLAUDE=${CLAUDE_BIN:-}
[ -x "${CLAUDE:-}" ] || CLAUDE=$(command -v claude)

read -r -d '' PROMPT <<'EOF' || true
Ты выполняешь ОДНУ техническую задачу и больше ничего: обновить две уже
опубликованные страницы по их постоянным ссылкам. Не читай другие файлы, не
запускай команды, не правь код, не создавай новых артефактов, не пиши в память.

Пары «файл -> метафайл с url»:
1. agent-runtime/research/glamping/occupancy/units-artifact.html ->
   docs/research/2026-08-10-glamping-market/units-artifact.json
2. agent-runtime/research/glamping/occupancy/occupancy-artifact.html ->
   docs/research/2026-08-10-glamping-market/occupancy-artifact.json

Для каждой пары: прочитай url из метафайла; инструментом Artifact сделай
action=read по этому url (публикация в непрочитанный артефакт отказывается);
затем опубликуй файл с параметром url (тот же адрес, без favicon, без нового
артефакта). Если файла нет или публикация отказана — не обходи, просто скажи.
Ответь двумя строками вида «units: обновлено <url>» / «summary: обновлено <url>»
или «...: НЕ обновлено — причина».
EOF

echo "публикация страниц: $(date +%H:%M:%S), снапшот $LAST, бинарь $CLAUDE"
timeout 600 "$CLAUDE" -p "$PROMPT" --output-format text --max-turns 12 < /dev/null
