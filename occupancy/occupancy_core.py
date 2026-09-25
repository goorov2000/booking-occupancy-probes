# -*- coding: utf-8 -*-
"""Ядро /glamping-occupancy: модель снапшота, метрики занятости, дифф, реестры.

Чистая библиотека: никакой сети, только диск и арифметика. Разведчик (тикет 05)
пишет рецепты, пробники (тикеты 02-04) читают рецепты и пишут снапшоты через
write_snapshot, сборщик сводки (тикет 06) читает снапшоты и зовёт aggregate/diff.

SCHEMA v1 — главный шов фичи. Тикеты 02-06 строятся на этих структурах;
менять их можно только через комментарий в issues/01-core-snapshot-metrics.md.
=============================================================================

(а) Реестр рецептов — ОДИН файл recipes.json:
    {
      "<username>": {
        "site": "https://...",                   # сайт объекта
        "engine": "travelline" | "bnovo" | "bronirui" | "litepms"
                | "aggregator-<платформа>"       # напр. aggregator-ostrovok
                | "custom-<движок>",             # добавлено тикетом 05: модуль
                                                 # есть, но движок не поддержан
                                                 # v1 (напр. custom-livewire);
                                                 # такой рецепт живёт только со
                                                 # status=broken+broken_reason,
                                                 # диспетчер пробников честно
                                                 # даёт insufficient_data
        "status": "ok" | "broken" | "no_module" | "aggregator",
        "request": {                             # слепок запроса виджета
          "url_template": "https://...{date_from}...{date_to}...",
          "method": "GET" | "POST",
          "params": {...},                       # тело/квери-параметры
          "headers": {...},
          "date_substitution": "<правило подстановки дат, строка-договорённость
                                 между разведчиком и пробником движка>"
        },
        "discovered_at": "<ISO8601>",
        "notes": "<свободный текст разведчика>",
        "source_urls": ["<откуда снят рецепт>", ...],
        "broken_reason": "<ПОЧЕМУ status=broken; добавлено тикетом 02:
                          пробник, поймавший смену схемы ответа или
                          4xx/антибот, пишет причину сюда (см.
                          probes.mark_recipe_broken) — разведчик тикета 05
                          читает её при переразведке. У живых рецептов поля
                          нет>",
        "invalid_entry": <необязательное, ставит load_recipes 04.09: запись
                          реестра была НЕ словарём (правка руки, склейка
                          файлов, полуготовый черновик). Исходное значение
                          сохранено здесь, сама запись подменена заготовкой
                          status=broken — иначе одна кривая строка роняла
                          весь прогон; см. quarantine_broken_entries>,
        "refusals": {"count": N, "since": "<ISO дня первого отказа>",
                     "last_day": "<ISO дня последнего отказа>",
                     "last_reason": "..."}
                        # добавлено 04.09 (тикет 03): 403/429/челлендж — это
                        # «нас не пустили», а не «схема сменилась». Рецепт
                        # ломается, когда отказ повторился REFUSAL_BREAK_DAYS
                        # суток подряд (note_refusal); успешный съём поле
                        # убирает (clear_refusals)
      }, ...
    }

(б) Снапшот прогона — каталог
    agent-runtime/research/glamping/occupancy/snapshots/<YYYY-MM-DD-HHMM>/:
    - run.json: {"schema_version": 1, "run_id": "<имя каталога>",
                 "started_at": "<ISO8601>", "targets": ["<username>", ...],
                 "planned_at": "<ISO8601, необязательно>",
                 "finished_at": "<ISO8601, дописывается close_snapshot>",
                 "objects_written": <сколько объектов реально снято>}
      run.json пишется В НАЧАЛЕ прогона (open_snapshot) со СПИСКОМ ПЛАНА,
      объекты дописываются по одному (append_object), итог — в конце
      (close_snapshot): обрыв прогона оставляет на диске всё, что успело
      сняться, а не ноль (тикет 02).
    - <username>.json (файл на объект):
      {
        "username": "...",
        "site": "https://...",
        "engine": "<как в рецепте>",
        "source_kind": "module" | "aggregator_quota" | "none",
            # aggregator_quota: цифры агрегатора = квота, не полная загрузка;
            # в сводке никогда не смешиваются с модульными без пометки
        "granularity": "per_unit" | "aggregate",
        "units": {
          "<имя юнита/категории>" | "__aggregate__": {
            "<YYYY-MM-DD>": {"state": "free" | "busy" | "sales_not_open"
                                     | "unknown",
                             "price": <число, опционально>,
                             "units_total": <сколько ФИЗИЧЕСКИХ домиков в этом
                                             юните продаётся в эту ночь>,
                             "units_free": <сколько из них свободно>}
          }, ...
        },
            # units_total/units_free — пара, добавленная 15.08 (вопрос заказчика
            # «все ли домики отсматривает»): у TravelLine и Bnovo юнит — это
            # ТИП размещения, и типов вида «А-фрейм ×3» полно. Без фонда
            # клетка бинарна, и день типа считается занятым, только когда
            # продан ПОСЛЕДНИЙ домик, — занижение (замер на одном объекте TL 16-29.08:
            # 24% по типам против >=30,4% по домикам). Поля необязательные:
            # где движок фонд не отдаёт, клетка весит один домик, как раньше,
            # и сводка честно помечает такой объект «считано по типам».
        "checked_at": "<ISO8601>",
        "status": "ok" | "partial" | "insufficient_data",
        "reason": "<почему partial/insufficient_data, иначе пустая строка>",
        "source_urls": ["<эндпоинты/страницы, откуда сетка>", ...],
        # происхождение снимка (тикет 10, все четыре поля НЕОБЯЗАТЕЛЬНЫ —
        # снапшоты до 04.09.2026 их не несут и читаются как раньше):
        "probe_version": "<движок@версия модуля пробника>",
        "recipe_hash": "<sha256 нормализованного request рецепта>",
        "run_planned_at": "<ISO8601 планового времени прогона>",
        "gap_days": <на сколько суток прогон отстал от планового времени>,
        "refusal": {"reason": "...", "status": <HTTP>, "at": "<ISO8601>"}
            # необязательное: хост нас не пустил (403/429/челлендж). Рецепт
            # от одного такого снимка НЕ ломается — счётчик суток ведёт
            # note_refusal, решение принимает CLI
      }
    Агрегации из сетки НЕ хранятся — всегда считаются заново (aggregate/diff).
    Fixture-режим CLI принимает тот же объект без checked_at/status/reason —
    они дозаполняются при записи.

(в) targets.json — целевой список, правится руками:
    [{"username": "...", "site": "https://...", "priority": <число; в самом
      файле порядок строк = важность, на него и опирайся: _note файла и эта
      строка схемы разошлись в знаке ещё в волне 2>,
      "reference": <необязательное; true = объект-ОРИЕНТИР, а не кандидат в
      клиенты. Снимается наравне со всеми, но в сводке помечен «референс»,
      стоит последним и не входит ни в скоринг, ни в знаменатель покрытия
      «снято N из M целей». Такие строки кладутся В КОНЕЦ файла — build_rows
      дополнительно уводит их в хвост стабильной сортировкой, чтобы порядок
      кандидатов не сдвинулся. Добавлено 16.08 (Pine River: 180 домиков,
      отель другого масштаба — сравнивать его с глэмпингом на 5 домиков
      как равную строку нельзя)>,
      "title": <необязательное; человеческое имя объекта для телеграма
      («Pine River» вместо pineriver_hotel). Без него берётся username>,
      "notify": <необязательное; true|"daily" — после каждого планового
      прогона слать в телеграм короткую сводку по объекту (текст —
      occupancy/tg_digest.py, транспорт — cf.notify); "changes" — только когда
      календарь двигался или снимок неполный. Поля нет = не уведомлять>}, ...]

Семантика метрик
----------------
- occupancy_pct = проданные домико-ночи / известные домико-ночи * 100.
  Клетка «юнит × дата» весит units_total домиков (по умолчанию 1), из них
  занято units_total - units_free (по умолчанию: busy -> 1, free -> 0).
  sales_not_open и unknown в знаменатель НЕ входят и показываются отдельными
  счётчиками. Два встречных смещения, оба называются вслух:
  * ВВЕРХ — занято и «закрыто владельцем вручную» по данным виджета
    неотличимы (рамка §Ж хэндоффа волны 2);
  * ВНИЗ — там, где фонд юнита неизвестен (units_total нет), тип с тремя
    одинаковыми домиками считается занятым только когда продан последний.
  Основу расчёта по объекту отдаёт unit_basis(): "unit" (весь фонд известен —
  честные домико-ночи), "type" (фонда нет нигде — старая метрика по типам),
  "mixed" (часть юнитов с фондом).
- Разрезы: все дни / будни / выходные. Пятница относится к ВЫХОДНЫМ
  (пт-сб-вс): брони загородных объектов живут уик-эндами с заезда в пятницу.
- Месяцы сводки — ключи "YYYY-MM" (в строке-сводке: авг/сен/окт 2026).
"""
from __future__ import annotations

import calendar
import contextlib
import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Пути и дефолты — единственное место (cli.py и build_summary.py импортируют
# отсюда; до ревью 14.08 копии в обоих скриптах уже начали расходиться риском)
# ---------------------------------------------------------------------------
# Витрина: модуль лежит в <repo>/occupancy/, а не в <repo>/.claude/skills/<skill>/scripts/,
# поэтому корень — на один уровень выше каталога файла (было parents[4]).
REPO_ROOT = Path(__file__).resolve().parents[1]
OCCUPANCY_ROOT = (REPO_ROOT / "agent-runtime" / "research" / "glamping"
                  / "occupancy")
DEFAULT_SNAPSHOT_ROOT = OCCUPANCY_ROOT / "snapshots"
DEFAULT_RECIPES = OCCUPANCY_ROOT / "recipes.json"
DEFAULT_TARGETS = OCCUPANCY_ROOT / "targets.json"
DEFAULT_OUT_MD = (REPO_ROOT / "docs" / "research"
                  / "2026-08-10-glamping-market" / "occupancy.md")
DEFAULT_OUT_HTML = OCCUPANCY_ROOT / "occupancy-artifact.html"
# Замок ЖИВОГО съёма — один на МАШИНУ, а не на каталог прогона (ревью
# wave3-core, 04.09.2026). Он защищает не файлы, а чужие хосты: пауза
# >= 1.2 с к одному хосту живёт внутри процесса (probes/_common), поэтому
# два одновременных процесса бьют один хост вдвое чаще правила, чем бы ни
# отличались их --recipes и --snapshot-root. Проверочный прогон уходит в
# песочницу с копией реестра и своим каталогом снапшотов — оба прежних замка
# на этом расходились с плановым, а цели и хосты у них те же самые.
# Путь переопределяется переменной среды: она нужна тестам, чтобы прогон
# набора не дрался за боевой замок с плановым прогоном 06:30.
HOST_LOCK_ENV = "GLAMPING_OCCUPANCY_HOST_LOCK"
DEFAULT_HOST_LOCK = OCCUPANCY_ROOT / ".probe-hosts.lock"
# DEPRECATED (04.09.2026). Календарная константа-дата-бомба: из неё считался
# конец горизонта запроса, и с 01.11.2026 прогон снимал бы НОЛЬ ночей
# (проверено запуском на 05.11). Оставлена ТОЛЬКО как значение по умолчанию
# для старых вызовов, которые ещё не перешли на summary_months(); новый код
# зовёт summary_months()/grid_horizon()/inventory_horizon().
DEFAULT_MONTHS = ["2026-08", "2026-09", "2026-10"]

# Скользящие горизонты (тикет 01 спеки поюнитной сезонности). Два разных:
# СЕТКА (докуда спрашиваем доступность) стоит одного-двух запросов на объект,
# а ФОНД («сколько домиков типа свободно») — запроса на КАЖДУЮ ночь, то есть
# почти всей цены прогона. Поэтому фонд снимается только на ближних ночах:
# ряд сезонности замораживает ночь в момент её наступления, и точный фонд
# нужен именно там.
DEFAULT_SUMMARY_MONTHS = 3      # сколько месяцев показывает сводка
DEFAULT_GRID_MONTHS = 12        # на сколько месяцев вперёд спрашиваем сетку
DEFAULT_INVENTORY_DAYS = 45     # на сколько ночей вперёд спрашиваем фонд

# Отказ хоста (403/429/челлендж) ломает рецепт не сразу, а когда повторился
# столько СУТОК подряд: разовый WAF-челлендж чужого хостера не должен слать
# рецепт на переразведку (см. note_refusal).
REFUSAL_BREAK_DAYS = 3

STATES = ("free", "busy", "sales_not_open", "unknown")
SOURCE_KINDS = ("module", "aggregator_quota", "none")
GRANULARITIES = ("per_unit", "aggregate")
OBJECT_STATUSES = ("ok", "partial", "insufficient_data")
RECIPE_STATUSES = ("ok", "broken", "no_module", "aggregator")
AGGREGATE_UNIT = "__aggregate__"

# Пятница — выходной: заезды загородных объектов живут уик-эндами пт-вс.
WEEKEND_WEEKDAYS = {4, 5, 6}  # пт, сб, вс (date.weekday(): пн=0)

MONTH_LABELS_RU = {
    1: "янв", 2: "фев", 3: "мар", 4: "апр", 5: "май", 6: "июн",
    7: "июл", 8: "авг", 9: "сен", 10: "окт", 11: "ноя", 12: "дек",
}


class RegistryError(Exception):
    """Реестр (recipes.json / targets.json) не читается: битый JSON, не та форма."""


class SnapshotError(Exception):
    """Снапшот не пишется/не читается: битые данные объекта или битый файл."""


class RunLockError(Exception):
    """Прогон уже идёт: файловая блокировка занята другим процессом."""


# ---------------------------------------------------------------------------
# Скользящие горизонты (всё считается от СЕГОДНЯ, ни одного года в коде)
# ---------------------------------------------------------------------------

def today() -> date:
    """Сегодня. Отдельная функция — единственный шов подмены даты в тестах."""
    return date.today()


def _today_or(value: Optional[date]) -> date:
    return value if value is not None else today()


def add_months(d: date, months: int) -> date:
    """Дата плюс months месяцев; день клампится на конец месяца (31.01 + 1 = 28.02)."""
    total = (d.year * 12 + d.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def month_key(d: date) -> str:
    """Дата -> ключ месяца 'YYYY-MM' (как в сетке и в сводке)."""
    return f"{d.year:04d}-{d.month:02d}"


def summary_months(today_: Optional[date] = None,
                   count: int = DEFAULT_SUMMARY_MONTHS) -> list[str]:
    """Скользящее окно месяцев сводки от текущего месяца: ['YYYY-MM', ...].

    Пришло на смену DEFAULT_MONTHS: сводка должна показывать текущий месяц и
    ближайшие, а не три конкретных месяца 2026 года.
    """
    base = _today_or(today_)
    return [month_key(add_months(base.replace(day=1), i)) for i in range(count)]


def grid_horizon(today_: Optional[date] = None,
                 months: int = DEFAULT_GRID_MONTHS) -> date:
    """Докуда спрашиваем ДОСТУПНОСТЬ: сегодня + months месяцев.

    Движок вправе отдать меньше (окно продаж закрыто) — что отдал, то и
    сохраняем; горизонт здесь — верхняя граница вопроса, а не обещание данных.
    """
    return add_months(_today_or(today_), months)


def inventory_horizon(today_: Optional[date] = None,
                      days: int = DEFAULT_INVENTORY_DAYS) -> date:
    """Докуда спрашиваем ФОНД типа: сегодня + days ночей (главный рычаг цены)."""
    return _today_or(today_) + timedelta(days=max(0, days))


# ---------------------------------------------------------------------------
# Метрики занятости
# ---------------------------------------------------------------------------

def day_class(d: date) -> str:
    """'weekend' для пт-сб-вс, иначе 'weekday' (пн-чт)."""
    return "weekend" if d.weekday() in WEEKEND_WEEKDAYS else "weekday"


def _pct(busy: int, known: int) -> Optional[float]:
    if known == 0:
        return None
    return round(100.0 * busy / known, 1)


def cell_basis(cell: dict) -> str:
    """Основание счёта клетки: 'unit' (фонд снят) или 'type' (фонда нет).

    Сравнивать между прогонами можно ТОЛЬКО клетки одного основания.
    Горизонт фонда ползёт каждые сутки (тикет 06), поэтому у одной и той же
    ночи вчера основания могло не быть, а сегодня оно есть — и клетка
    «1 из 1» против «3 из 3» несравнима ни по одному числу.
    """
    total = cell.get("units_total")
    free = cell.get("units_free")
    if (isinstance(total, int) and not isinstance(total, bool) and total > 0
            and isinstance(free, int) and not isinstance(free, bool)
            and 0 <= free <= total):
        return "unit"
    return "type"


def cell_units(cell: dict) -> tuple[int, int]:
    """Клетка -> (домиков всего, из них занято) для метрик.

    Фонд известен (units_total/units_free) — считаем по домикам; нет —
    клетка весит один домик со старой бинарной семантикой (busy -> занят).
    """
    if cell_basis(cell) == "unit":
        total = cell["units_total"]
        return total, total - cell["units_free"]
    return 1, 1 if cell.get("state") == "busy" else 0


def unit_capacity(cells: dict) -> Optional[int]:
    """Фонд юнита = максимум units_total по его клеткам; None — фонд не снят."""
    totals = [c["units_total"] for c in cells.values()
              if isinstance(c.get("units_total"), int)
              and not isinstance(c.get("units_total"), bool)
              and c["units_total"] > 0]
    return max(totals) if totals else None


def unit_basis(units: dict) -> dict:
    """Основа расчёта объекта: по домикам, по типам или вперемешку.

    {"basis": "unit" | "type" | "mixed", "units_with_capacity": N,
     "units_total": M, "homes": сколько домиков в объекте по снятому фонду
                             (юниты без фонда считаются за один домик)}
    """
    known = [u for u, cells in units.items() if unit_capacity(cells) is not None]
    total = len(units)
    homes = sum(unit_capacity(cells) or 1 for cells in units.values())
    if total and len(known) == total:
        basis = "unit"
    elif known:
        basis = "mixed"
    else:
        basis = "type"
    return {"basis": basis, "units_with_capacity": len(known),
            "units_total": total, "homes": homes}


def aggregate(units: dict, months: list[str]) -> list[dict]:
    """Метрики по месяцам из сетки units (см. SCHEMA (б)).

    Возвращает список по каждому месяцу из months:
      {"month": "YYYY-MM", "label": "авг 2026",
       "cuts": {"all"|"weekday"|"weekend": {"busy", "known", "pct"}},
       "unknown": N, "sales_not_open": N}
    busy/known — ДОМИКО-НОЧИ (клетка без снятого фонда весит один домик),
    pct = None, когда в разрезе нет ни одной известной клетки.
    Смещения метрики — в докстринге модуля.
    """
    per_month: dict[str, dict] = {}
    for month in months:
        year, mon = (int(x) for x in month.split("-"))
        per_month[month] = {
            "month": month,
            "label": f"{MONTH_LABELS_RU[mon]} {year}",
            "cuts": {c: {"busy": 0, "known": 0} for c in ("all", "weekday", "weekend")},
            "unknown": 0,
            "sales_not_open": 0,
        }
    for cells in units.values():
        for date_str, cell in cells.items():
            month = date_str[:7]
            m = per_month.get(month)
            if m is None:
                continue
            state = cell.get("state")
            if state == "unknown":
                m["unknown"] += 1
                continue
            if state == "sales_not_open":
                m["sales_not_open"] += 1
                continue
            d = date.fromisoformat(date_str)
            total, busy = cell_units(cell)
            for cut in ("all", day_class(d)):
                m["cuts"][cut]["known"] += total
                m["cuts"][cut]["busy"] += busy
    for m in per_month.values():
        for cut in m["cuts"].values():
            cut["pct"] = _pct(cut["busy"], cut["known"])
    return [per_month[month] for month in months]


def _fmt_pct(pct: Optional[float]) -> str:
    return "нет данных" if pct is None else f"{pct:.0f}%"


def basis_note(units: dict) -> str:
    """Человеческая пометка основы расчёта для сводки и stdout."""
    info = unit_basis(units)
    if not info["units_total"]:
        return ""
    if info["basis"] == "unit":
        return (f"считано по домикам: {info['homes']} "
                f"в {info['units_total']} типах")
    if info["basis"] == "mixed":
        return (f"по домикам частично: фонд снят у "
                f"{info['units_with_capacity']} из {info['units_total']} типов, "
                f"остальные считаны по типу — занижение")
    return ("считано по типам, фонд не снят: тип с несколькими одинаковыми "
            "домиками занят, только когда продан последний — занижение")


def summary_line(obj: dict, months: list[str]) -> str:
    """Человеческая строка сводки по объекту (одна строка, для stdout/сводки)."""
    metrics = aggregate(obj.get("units", {}), months)
    chunks = []
    for m in metrics:
        cuts = m["cuts"]
        if cuts["all"]["known"] == 0:
            chunks.append(f"{m['label']}: нет данных")
        else:
            chunks.append(
                f"{m['label']}: {_fmt_pct(cuts['all']['pct'])}"
                f" (будни {_fmt_pct(cuts['weekday']['pct'])},"
                f" вых {_fmt_pct(cuts['weekend']['pct'])})"
            )
    unknown_total = sum(m["unknown"] for m in metrics)
    sno_total = sum(m["sales_not_open"] for m in metrics)
    tail = []
    note = basis_note(obj.get("units", {}))
    if note:
        tail.append(note)
    tail.append(f"неизвестных дней: {unknown_total}")
    if sno_total:
        tail.append(f"продажи не открыты: {sno_total} дн.")
    if obj.get("source_kind") == "aggregator_quota":
        tail.append("квота агрегатора, не полная загрузка")
    status = obj.get("status", "")
    if status and status != "ok":
        reason = obj.get("reason") or ""
        tail.append(f"статус {status}" + (f" — {reason}" if reason else ""))
    return (
        f"{obj['username']} [{obj.get('engine', '?')}/{obj.get('source_kind', '?')}]"
        f" — занятость, оценка сверху: " + "; ".join(chunks)
        + "; " + "; ".join(tail)
    )


# ---------------------------------------------------------------------------
# Дифф двух снапшотов объекта
# ---------------------------------------------------------------------------

def diff_units(cur_units: dict, prev_units: dict, months: list[str]) -> dict:
    """Динамика между прогонами: сдвиг occupancy по месяцам + новые занятые даты.

    Возвращает:
      {"months": {"YYYY-MM": {"prev_pct", "cur_pct", "delta_pp"}},  # только
                              # месяцы, где хоть в одном прогоне есть данные
       "newly_busy": [{"unit", "date"}, ...],   # стало busy, а не было
       "newly_sold": [{"unit", "date", "prev_busy", "cur_busy", "total"}, ...],
          # в типе продали ЕЩЁ домик, но не последний: state остался free,
          # и до появления фонда такое движение было не видно вовсе
       "basis_changed": [{"unit", "date", "prev", "cur"}, ...]}
          # у ночи ПОЯВИЛОСЬ или ПРОПАЛО основание счёта (снятый фонд).
          # Это не продажа: горизонт фонда в 45 ночей ползёт каждые сутки,
          # и без этой ветки клетка «1 из 1» против «3 из 3» печаталась
          # человеку как «допродано домиков в типах» каждый день
    delta_pp — процентные пункты; None у prev/cur, когда данных не было.
    """
    cur_m = {m["month"]: m for m in aggregate(cur_units, months)}
    prev_m = {m["month"]: m for m in aggregate(prev_units, months)}
    month_shifts = {}
    for month in months:
        cur_pct = cur_m[month]["cuts"]["all"]["pct"]
        prev_pct = prev_m[month]["cuts"]["all"]["pct"]
        if cur_pct is None and prev_pct is None:
            continue
        delta = None
        if cur_pct is not None and prev_pct is not None:
            delta = round(cur_pct - prev_pct, 1)
        month_shifts[month] = {"prev_pct": prev_pct, "cur_pct": cur_pct,
                               "delta_pp": delta}
    newly_busy = []
    newly_sold = []
    basis_changed = []
    for unit, cells in cur_units.items():
        prev_cells = prev_units.get(unit, {})
        for date_str, cell in cells.items():
            # Только месяцы сводки: снапшоты разной глубины иначе дают
            # тысячи «новых занятых дат» там, где прошлый прогон просто не
            # доставал до этих месяцев (живой пример 15.08 — chekhovapi,
            # 1717 мнимых дат за ноябрь-март).
            if date_str[:7] not in months:
                continue
            prev_cell = prev_cells.get(date_str, {})
            prev_state = prev_cell.get("state")
            if cell.get("state") == "busy":
                if prev_state != "busy":
                    newly_busy.append({"unit": unit, "date": date_str})
                continue
            if cell.get("state") != "free" or prev_state != "free":
                continue
            if cell_basis(cell) != cell_basis(prev_cell):
                # Не продажа, а смена основания счёта — сравнивать нечего.
                basis_changed.append({"unit": unit, "date": date_str,
                                      "prev": cell_basis(prev_cell),
                                      "cur": cell_basis(cell)})
                continue
            total, busy = cell_units(cell)
            _, prev_busy = cell_units(prev_cell)
            if busy > prev_busy:
                newly_sold.append({"unit": unit, "date": date_str,
                                   "prev_busy": prev_busy, "cur_busy": busy,
                                   "total": total})
    newly_busy.sort(key=lambda e: (e["date"], e["unit"]))
    newly_sold.sort(key=lambda e: (e["date"], e["unit"]))
    basis_changed.sort(key=lambda e: (e["date"], e["unit"]))
    return {"months": month_shifts, "newly_busy": newly_busy,
            "newly_sold": newly_sold, "basis_changed": basis_changed}


def diff_line(diff: dict, prev_run_id: str, max_dates: int = 10) -> str:
    """Человеческая строка динамики против прогона prev_run_id."""
    parts = []
    for month, shift in diff["months"].items():
        year, mon = (int(x) for x in month.split("-"))
        label = f"{MONTH_LABELS_RU[mon]} {year}"
        if shift["delta_pp"] is None:
            parts.append(f"{label}: {_fmt_pct(shift['prev_pct'])} -> "
                         f"{_fmt_pct(shift['cur_pct'])}")
        else:
            parts.append(f"{label}: {shift['delta_pp']:+.1f}пп")
    newly = diff["newly_busy"]
    if newly:
        shown = [
            e["date"] if e["unit"] == AGGREGATE_UNIT else f"{e['date']} ({e['unit']})"
            for e in newly[:max_dates]
        ]
        rest = len(newly) - len(shown)
        dates_txt = ", ".join(shown) + (f" и ещё {rest}" if rest > 0 else "")
        parts.append(f"новых занятых дат: {len(newly)} — {dates_txt}")
    else:
        parts.append("новых занятых дат нет")
    sold = diff.get("newly_sold") or []
    if sold:
        shown = [f"{e['date']} ({e['unit']}: занято {e['cur_busy']} из "
                 f"{e['total']})" for e in sold[:max_dates]]
        rest = len(sold) - len(shown)
        parts.append(f"допродано домиков внутри типов: {len(sold)} — "
                     + ", ".join(shown) + (f" и ещё {rest}" if rest > 0 else ""))
    changed = diff.get("basis_changed") or []
    if changed:
        # Называем своим именем, а не прячем: горизонт фонда ползёт каждые
        # сутки, и человек обязан видеть, что цифра сдвинулась от способа
        # счёта, а не от броней.
        parts.append(f"сменилось основание счёта у {len(changed)} ноч. "
                     f"(у ночи появился или пропал снятый фонд) — это не "
                     f"продажи")
    return f"  динамика с прогона {prev_run_id}: " + "; ".join(parts)


# ---------------------------------------------------------------------------
# Снапшот прогона: запись и чтение
# ---------------------------------------------------------------------------

def validate_object(obj: dict) -> None:
    """Проверка объекта снапшота по SCHEMA (б); нарушение -> SnapshotError."""
    username = obj.get("username")
    if not username or not isinstance(username, str):
        raise SnapshotError("объект снапшота без username")
    if obj.get("source_kind") not in SOURCE_KINDS:
        raise SnapshotError(
            f"{username}: source_kind={obj.get('source_kind')!r}, "
            f"ожидается одно из {SOURCE_KINDS}")
    if obj.get("granularity") not in GRANULARITIES:
        raise SnapshotError(
            f"{username}: granularity={obj.get('granularity')!r}, "
            f"ожидается одно из {GRANULARITIES}")
    if obj.get("status") not in OBJECT_STATUSES:
        raise SnapshotError(
            f"{username}: status={obj.get('status')!r}, "
            f"ожидается одно из {OBJECT_STATUSES}")
    units = obj.get("units")
    if not isinstance(units, dict):
        raise SnapshotError(f"{username}: units должен быть словарём юнит -> даты")
    for unit, cells in units.items():
        if not isinstance(cells, dict):
            raise SnapshotError(f"{username}/{unit}: даты должны быть словарём")
        for date_str, cell in cells.items():
            try:
                date.fromisoformat(date_str)
            except ValueError:
                raise SnapshotError(
                    f"{username}/{unit}: дата {date_str!r} не YYYY-MM-DD") from None
            state = cell.get("state") if isinstance(cell, dict) else None
            if state not in STATES:
                raise SnapshotError(
                    f"{username}/{unit}/{date_str}: state={state!r}, "
                    f"ожидается одно из {STATES}")
            _validate_capacity(username, unit, date_str, cell, state)


def _validate_capacity(username: str, unit: str, date_str: str, cell: dict,
                       state: str) -> None:
    """Фонд клетки (units_total/units_free): пара, целые, free<=total, и
    согласие со state — иначе метрика молча разъедется с сеткой."""
    total = cell.get("units_total")
    free = cell.get("units_free")
    where = f"{username}/{unit}/{date_str}"
    if total is None and free is None:
        return
    if total is None or free is None:
        raise SnapshotError(
            f"{where}: units_total и units_free пишутся только парой "
            f"(units_total={total!r}, units_free={free!r})")
    for name, value in (("units_total", total), ("units_free", free)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise SnapshotError(
                f"{where}: {name}={value!r}, ожидается целое >= 0")
    if total == 0:
        raise SnapshotError(f"{where}: units_total=0 — юнит без домиков")
    if free > total:
        raise SnapshotError(
            f"{where}: свободных домиков больше, чем есть "
            f"({free} из {total})")
    if state == "busy" and free != 0:
        raise SnapshotError(
            f"{where}: state=busy, но свободно {free} домиков")
    if state == "free" and free == 0:
        raise SnapshotError(f"{where}: state=free при нуле свободных домиков")


def open_snapshot(root, run_id: str, run_meta: dict) -> Path:
    """Открыть каталог прогона и записать run.json ДО съёма. -> путь каталога.

    Почему в начале: до 04.09 снапшот писался одним куском в конце прогона
    (cli.py:129), и таймаут systemd на 50-й цели из 53 стоил ВСЕГО дня, а не
    одной цели. Ряд сезонности, ради которого всё делается, рвался целиком.
    Теперь run.json с планом целей лежит на диске с первой секунды, объекты
    дописываются по мере съёма (append_object), а итог дописывается в конце
    (close_snapshot) — оборванный прогон оставляет всё, что успел снять.
    """
    path = Path(root) / run_id
    path.mkdir(parents=True, exist_ok=True)
    run = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "started_at": run_meta.get("started_at"),
        "targets": run_meta.get("targets", []),
    }
    for key in ("planned_at", "deadline_at", "note"):
        if run_meta.get(key) is not None:
            run[key] = run_meta[key]
    _dump_json(path / "run.json", run)
    return path


def append_object(snap_dir, obj: dict) -> Path:
    """Записать объект в открытый снапшот СРАЗУ после съёма. -> путь файла."""
    validate_object(obj)
    path = Path(snap_dir) / f"{obj['username']}.json"
    _dump_json(path, obj)
    return path


def close_snapshot(snap_dir, stats: Optional[dict] = None) -> Path:
    """Дописать в run.json фактический итог прогона. -> путь run.json.

    Итог отделён от плана нарочно: расхождение «планировали 53, сняли 47»
    видно прямо в снапшоте, а не выводится задним числом из числа файлов.
    Поэтому objects_written считает СНЯТОЕ — то, что стоило хотя бы одного
    запроса. Файл на диске получает и цель, до которой очередь не дошла по
    дедлайну, и цель с рецептом broken, и цель с неподдержанным движком, и
    цель без рецепта: все они — честная строка с причиной, а не съём.

    Прогон закрывается ВСЕГДА, даже оборвавшись необработанной ошибкой (в
    stats тогда лежит error): незакрытый прогон слой сезонности в работу не
    берёт, и обрыв на середине стоил бы дня по всем целям сразу.
    """
    path = Path(snap_dir) / "run.json"
    run = _load_json(path, SnapshotError) if path.is_file() else {}
    if not isinstance(run, dict):
        raise SnapshotError(f"{path}: run.json должен быть словарём")
    run.update(stats or {})
    run.setdefault("finished_at", datetime.now().astimezone()
                   .isoformat(timespec="seconds"))
    _dump_json(path, run)
    return path


def write_snapshot(root, run_id: str, run_meta: dict, objects: list[dict]) -> Path:
    """Записать каталог прогона целиком (fixture-режим и тесты).

    Живой прогон пишет по частям (open/append/close) — здесь те же три вызова
    одной строкой. Проверка ВСЕХ объектов идёт до открытия каталога: битый
    объект не должен оставлять после себя полупустой снапшот.
    """
    for obj in objects:
        validate_object(obj)
    path = open_snapshot(root, run_id, run_meta)
    for obj in objects:
        append_object(path, obj)
    close_snapshot(path, {"objects_written": len(objects)})
    return path


def read_snapshot(path) -> dict:
    """Прочитать каталог прогона: {"run": run.json, "objects": {username: obj}}."""
    path = Path(path)
    run_file = path / "run.json"
    if not run_file.is_file():
        raise SnapshotError(f"{path}: нет run.json — это не каталог прогона")
    run = _load_json(run_file, SnapshotError)
    objects = {}
    for obj_file in sorted(path.glob("*.json")):
        if obj_file.name == "run.json":
            continue
        obj = _load_json(obj_file, SnapshotError)
        objects[obj.get("username", obj_file.stem)] = obj
    return {"run": run, "objects": objects}


def list_snapshots(root) -> list[str]:
    """Имена каталогов прогонов (YYYY-MM-DD-HHMM), отсортированные по времени."""
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir()
                  if p.is_dir() and (p / "run.json").is_file())


def find_previous_snapshot(root, run_id: str) -> Optional[Path]:
    """Последний прогон СТРОГО раньше run_id (имена сортируются как время)."""
    earlier = [name for name in list_snapshots(root) if name < run_id]
    return Path(root) / earlier[-1] if earlier else None


def run_id_day(run_id) -> Optional[date]:
    """Дата из имени прогона YYYY-MM-DD-HHMM. Не разобрали -> None."""
    if not isinstance(run_id, str) or len(run_id) < 10:
        return None
    try:
        return date.fromisoformat(run_id[:10])
    except ValueError:
        return None


def days_between_runs(prev_run_id, run_id) -> Optional[int]:
    """Сколько СУТОК ряд молчал между двумя прогонами. Не считается -> None.

    Дыра в ряду — единственный признак, по которому виден догон после
    простоя машины: gap_days его не ловит (см. его докстринг), а имена
    каталогов прогонов приходилось сравнивать глазами.
    """
    prev, cur = run_id_day(prev_run_id), run_id_day(run_id)
    if prev is None or cur is None:
        return None
    return (cur - prev).days


# ---------------------------------------------------------------------------
# Происхождение снимка: чем и по какому рецепту он снят (тикет 10)
# ---------------------------------------------------------------------------

def recipe_hash(recipe: Optional[dict]) -> Optional[str]:
    """sha256 нормализованного request рецепта; None — рецепта/запроса нет.

    Хэшируется ТОЛЬКО request (url_template, method, params, headers,
    date_substitution): меняется он — меняется смысл снятых цифр. Правка
    notes/discovered_at косметическая и хэш двигать не должна, иначе ряд
    сезонности будет пестреть мнимыми сменами метода съёма.
    """
    if not isinstance(recipe, dict):
        return None
    request = recipe.get("request")
    if not isinstance(request, dict) or not request:
        return None
    payload = json.dumps(request, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def gap_days(planned_at, started_at) -> Optional[int]:
    """На сколько СУТОК прогон отстал от планового времени (Р4 спеки).

    Ночь, снятая через сутки после планового момента, в рыночную медиану не
    идёт: её «реализованная занятость» снята не утром самой ночи. Даты берём
    календарные — важно не «на 26 часов», а «на день позже».

    Чего это поле НЕ ловит (ревью wave3-core): догон после простоя машины.
    Прогон Persistent=true, запущенный через двое суток в 12:00, получает
    плановый момент сегодняшнего утра и честный gap_days=0 — ночь снята
    сегодня, просто позже утра. Дыра в РЯДУ отвечает на другой вопрос и
    пишется отдельно: days_between_runs -> run.json (days_since_prev_run).
    """
    planned = _as_datetime(planned_at)
    started = _as_datetime(started_at)
    if planned is None or started is None:
        return None
    return (started.date() - planned.date()).days


def _as_datetime(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def stamp_provenance(obj: dict, *, recipe: Optional[dict] = None,
                     probe_version: Optional[str] = None,
                     run_planned_at: Optional[str] = None,
                     started_at=None) -> dict:
    """Проставить объекту снапшота происхождение (in-place, -> тот же объект).

    Поля необязательные: старые снапшоты без них читаются и агрегируются как
    раньше. Пишутся только те, что известны, — пустых ключей не заводим.
    """
    rhash = recipe_hash(recipe)
    if rhash:
        obj["recipe_hash"] = rhash
    if probe_version:
        obj["probe_version"] = probe_version
    if run_planned_at:
        obj["run_planned_at"] = run_planned_at
        gap = gap_days(run_planned_at, started_at or obj.get("checked_at"))
        if gap is not None:
            obj["gap_days"] = gap
    return obj


# ---------------------------------------------------------------------------
# Реестры: recipes.json и targets.json
# ---------------------------------------------------------------------------

def load_recipes(path) -> dict:
    """Реестр рецептов. Файла нет -> пустой реестр (первый прогон — норма).

    Битый ФАЙЛ (не JSON, не словарь) — по-прежнему RegistryError: читать
    нечего. Битая ОДНА ЗАПИСЬ — карантин, а не отказ: см.
    quarantine_broken_entries.
    """
    path = Path(path)
    if not path.exists():
        return {}
    recipes = _load_json(path, RegistryError)
    if not isinstance(recipes, dict):
        raise RegistryError(f"{path}: реестр рецептов должен быть словарём "
                            f"username -> рецепт, а не {type(recipes).__name__}")
    return quarantine_broken_entries(recipes, source=path)


def quarantine_broken_entries(recipes: dict, *, source=None, out=None) -> dict:
    """Запись-НЕ-словарь -> честная заготовка broken. -> новый реестр.

    Зачем (ревью wave4-core, 04.09.2026). Одна такая запись роняла ВЕСЬ
    плановый прогон голым traceback ещё до открытия снапшота: очередь
    таймера зовёт recipe.get("engine") снаружи своего except RegistryError
    (run_scheduled.py:187), а `mark_recipe_broken` пишет recipe["status"] в
    список. Ценой был весь день по всем целям — ни строки в Run Log, ни
    алерта, ни единого снятого объекта. После разведки 04.09 в реестре 86
    записей, 61 из них сделана машиной, то есть цена битой записи выросла
    втрое, а вероятность — вместе с ней.

    Почему карантин, а не отказ читать реестр: 85 живых целей не должны
    платить за одну кривую строку. Почему заготовка, а не выброс записи:
    прогон переписывает реестр ЦЕЛИКОМ (cli._account_recipe ->
    save_recipes), и молчаливое стирание чужой записи хуже самой записи —
    поэтому исходное значение остаётся внутри поля invalid_entry, а engine
    пустой уводит цель в ветку «нужна разведка», а не в съём.

    Ошибка не глотается: каждая запись названа в stderr (журнал systemd).
    """
    clean: dict = {}
    for username, recipe in recipes.items():
        if isinstance(recipe, dict):
            clean[username] = recipe
            continue
        reason = (f"запись реестра не словарь, а {type(recipe).__name__} — "
                  f"нужна разведка")
        clean[username] = {
            "site": "", "engine": "", "status": "broken",
            "broken_reason": reason, "source_urls": [],
            "invalid_entry": recipe,
        }
        print(f"предупреждение: {source or 'реестр рецептов'}: рецепт "
              f"{username!r} — {reason}", file=out or sys.stderr)
    return clean


def save_recipes(path, recipes: dict) -> None:
    _dump_json(Path(path), recipes)


def load_targets(path) -> list:
    """Целевой список. Файла нет -> ошибка: без targets прогон не имеет смысла."""
    path = Path(path)
    if not path.exists():
        raise RegistryError(f"{path}: файла targets нет — создайте список целей "
                            f"(SCHEMA (в) в occupancy_core.py)")
    targets = _load_json(path, RegistryError)
    if not isinstance(targets, list):
        raise RegistryError(f"{path}: targets должен быть списком объектов, "
                            f"а не {type(targets).__name__}")
    return targets


def save_targets(path, targets: list) -> None:
    _dump_json(Path(path), targets)


# ---------------------------------------------------------------------------
# Отказ хоста: считаем СУТКИ, а не запросы
# ---------------------------------------------------------------------------

def _iso_day(value) -> Optional[date]:
    """'YYYY-MM-DD' -> date; чужое или битое значение -> None."""
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def note_refusal(recipe: dict, reason: str, *, today_: Optional[date] = None,
                 break_after_days: int = REFUSAL_BREAK_DAYS) -> Optional[str]:
    """Отметить в рецепте «нас не пустили» (in-place). -> broken_reason | None.

    Один HTTP 403 ломал рецепт НАВСЕГДА, и при 53 целях ежедневного прогона
    временный WAF-челлендж чужого хостера выбивал бы рецепты пачками — каждый
    с ручной переразведкой. Считаем сутки: несколько отказов в один день —
    это одна попытка, а не три. Успешный съём обнуляет счётчик
    (clear_refusals).

    Сутки считаются ПОДРЯД, а не просто разные: разрыв череды начинает счёт
    заново. Иначе одиночный отказ раз в месяц за три месяца накопил бы порог
    и сломал живой рецепт, а сообщение при этом утверждало бы «подряд».
    Одним clear_refusals эту дыру не закрыть: в сутки, когда объект вообще не
    снимался (дедлайн прогона, пауза, вывод цели из очереди), нет ни отказа,
    ни успеха — счётчик просто замирал и продолжался со следующего отказа.
    """
    today_day = _today_or(today_)
    day = today_day.isoformat()
    state = recipe.get("refusals")
    if not isinstance(state, dict) or not state.get("count"):
        state = {"count": 1, "since": day, "last_day": day}
    else:
        previous = _iso_day(state.get("last_day"))
        if previous == today_day:
            state = dict(state)              # несколько отказов за одни сутки
        elif previous is not None and (today_day - previous).days == 1:
            state = dict(state)
            state["count"] = int(state.get("count", 0)) + 1
            # Начало череды берём не позже предыдущего отказа: у рецепта,
            # правленного руками, since может не быть вовсе.
            state.setdefault("since", state["last_day"])
            state["last_day"] = day
        else:
            state = {"count": 1, "since": day, "last_day": day}
    state["last_reason"] = reason
    # Рецепт правят и руками: без since сообщение о поломке падало бы
    # KeyError-ом ровно в тот момент, когда его надо прочитать.
    state.setdefault("since", state.get("last_day") or day)
    recipe["refusals"] = state
    if state["count"] >= break_after_days:
        return (f"нас не пускают {state['count']} суток подряд "
                f"(с {state['since']}): {reason}")
    return None


def clear_refusals(recipe: dict) -> bool:
    """Успешный съём: забыть счётчик отказов. -> было ли что забывать."""
    return recipe.pop("refusals", None) is not None


# ---------------------------------------------------------------------------
# Блокировка прогона: два одновременных прогона портят реестр и снапшот
# ---------------------------------------------------------------------------

def host_lock_path() -> Path:
    """Путь замка живого съёма — ОДНОГО на машину (переопределяется средой).

    От путей прогона он не зависит нарочно: защищаются чужие хосты, а не
    каталог. Два процесса с разными --recipes и --snapshot-root — это всё
    равно два потока запросов к одному ru-ibe.tlintegration.ru, между
    которыми паузы 1.2 с нет вовсе.
    """
    raw = os.environ.get(HOST_LOCK_ENV)
    return Path(raw).expanduser() if raw else DEFAULT_HOST_LOCK


@contextlib.contextmanager
def run_lock(path, note: str = "второй одновременный запуск испортил бы "
                               "реестр и снапшот"):
    """Эксклюзивная файловая блокировка прогона; занята -> RunLockError.

    Без неё второй запуск (оператор руками поверх таймера) пишет в тот же
    recipes.json и тот же каталог снапшота. Блокировка неблокирующая: ждать
    чужой прогон бессмысленно, честный отказ понятнее зависшего процесса.

    Замок ставится на КАЖДЫЙ защищаемый ресурс отдельно, а не на один
    «прогон вообще»: реестр рецептов общий у любых двух прогонов, а каталог
    снапшотов у каждого свой (--snapshot-root). Пока замок был только на
    каталоге, замер во временный корень проходил мимо него и переписывал
    recipes.json параллельно с плановым прогоном (cli._registry_lock_path).
    Третий ресурс — ЧУЖИЕ ХОСТЫ (host_lock_path): он один на машину, потому
    что паузу к хосту процессы не делят.

    note объясняет ЧЕМУ помешал бы второй запуск: у каждого ресурса цена
    своя, и «испортил бы реестр» вместо «бил бы чужой хост вдвое чаще» — это
    ошибочный диагноз в журнале ночного прогона.
    """
    # Портируемость витрины: боевой контур живёт на Linux (flock), но набор
    # тестов должен запускаться и на Windows — там та же неблокирующая
    # эксклюзивная блокировка делается через msvcrt.locking на первом байте.
    # Семантика одна: второй захват того же файла (даже в этом процессе) падает.
    try:
        import fcntl
    except ImportError:  # Windows
        fcntl = None
        import msvcrt

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RunLockError(
                f"{path}: прогон уже идёт (блокировка занята) — "
                f"{note}") from None
        try:
            yield path
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            else:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()


# ---------------------------------------------------------------------------
# JSON с внятными ошибками
# ---------------------------------------------------------------------------

def load_json(path, error_cls: type = SnapshotError):
    """Прочитать JSON-файл; не читается/битый -> error_cls с внятным текстом."""
    return _load_json(Path(path), error_cls)


def _load_json(path: Path, error_cls: type):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise error_cls(f"{path}: не читается ({e})") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise error_cls(f"{path}: некорректный JSON — {e}") from None


def _dump_json(path: Path, data) -> None:
    """Записать JSON АТОМАРНО: временный файл рядом + os.replace.

    Прогон дописывает recipes.json и run.json посреди работы; обрыв на
    середине write_text оставлял бы обрезанный JSON, то есть съеденный
    реестр рецептов. os.replace на одной ФС атомарен — читатель видит либо
    старый файл целиком, либо новый целиком.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
