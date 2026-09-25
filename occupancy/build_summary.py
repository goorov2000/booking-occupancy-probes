# -*- coding: utf-8 -*-
"""Сборка человеческой сводки загрузки (тикет 06): снапшоты -> occupancy.md + html.

Вход: история снапшотов (agent-runtime/.../occupancy/snapshots/), реестр
рецептов и targets.json. Для каждой цели берётся её последний снимок С
ДАННЫМИ по всей истории (прогоны бывают узкими — объект мог сниматься не в
последнем прогоне, а сегодняшний отказ чужого хоста не должен стирать
вчерашние цифры), динамика — против предыдущего снимка с данными. Сегодняшний
провал и возраст данных показываются отдельными пометками, а не пустотой.

Выход:
  - markdown-сводка (дефолт docs/research/2026-08-10-glamping-market/occupancy.md):
    шапка «как читать», таблица по целям, досье человеческим текстом, сырьё;
  - html той же сводки для артефакт-страницы (дефолт
    agent-runtime/.../occupancy/occupancy-artifact.html) — публикует агент по
    стабильному url из docs/research/.../occupancy-artifact.json.

Запуск:
  .venv/bin/python occupancy/build_summary.py
Опции: --snapshot-root, --recipes, --targets, --months, --out, --out-html.
Месяцы по умолчанию — скользящее окно от текущего месяца (core.summary_months),
а не календарная константа: с ней сводка с 01.11.2026 показала бы «нет данных»
по всем объектам.
"""
from __future__ import annotations

import argparse
import calendar
import sys
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

import occupancy_core as core

ENGINE_LABELS = {
    "travelline": "TravelLine",
    "bnovo": "Bnovo",
    "bronirui": "Бронируй Онлайн",
    "litepms": "LitePMS",
    "homereserve": "HomeReserve",
    "uhotels": "UHotels",
    # Пробники, подключённые 08.09.2026. sutochno — АГРЕГАТОР со своим
    # пробником: имя движка без приставки aggregator- (его снимает таймер,
    # а не агент), но снимок всё равно несёт source_kind aggregator_quota,
    # и «квота» в колонке «Источник» ставится по нему, а не по имени.
    "bookonline24": "BookOnline24",
    "frontdesk24": "Frontdesk24",
    "rc-bookings": "RC Bookings",
    "sutochno": "Суточно.ру",
    "aggregator-ostrovok": "Островок",
    "aggregator-sutochno": "Суточно.ру",
    "aggregator-yandex-travel": "Яндекс.Путешествия",
}

MONTH_FULL_RU = {
    1: "январь", 2: "февраль", 3: "март", 4: "апрель", 5: "май", 6: "июнь",
    7: "июль", 8: "август", 9: "сентябрь", 10: "октябрь", 11: "ноябрь",
    12: "декабрь",
}

# Предложный падеж («в сентябре») для связного текста досье.
MONTH_PREP_RU = {
    1: "январе", 2: "феврале", 3: "марте", 4: "апреле", 5: "мае", 6: "июне",
    7: "июле", 8: "августе", 9: "сентябре", 10: "октябре", 11: "ноябре",
    12: "декабре",
}

# Родительный падеж («до 31 октября») для заголовка календарной ленты.
MONTH_GEN_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября",
    12: "декабря",
}


def _engine_label(engine: str) -> str:
    return ENGINE_LABELS.get(engine or "", engine or "неизвестный движок")


# Роль объекта живёт ОТДЕЛЬНО от колонки «Источник»: та описывает способ
# замера (модуль/квота, по домикам или по типам), и мешать с ней «кандидат
# или ориентир» нельзя — читатель перестанет понимать, что чем меряно.
REFERENCE_MARK = "референс"


def _row_name(row: dict) -> str:
    """Имя объекта для markdown: у референса — с пометкой роли."""
    return row["username"] + (f" — {REFERENCE_MARK}" if row.get("reference")
                              else "")


def _month_full(month: str) -> str:
    return MONTH_FULL_RU[int(month.split("-")[1])]


# ---------------------------------------------------------------------------
# История снапшотов и модель строк
# ---------------------------------------------------------------------------

# Статусы снимка, у которого есть цифры. Всё остальное («рецепт сломан»,
# «403 от хоста») — честная запись о провале, а не данные.
DATA_STATUSES = ("ok", "partial")

# Сколько снимков С ДАННЫМИ нужно сводке на цель: последний (цифры в таблице)
# и предыдущий (динамика к нему). Больше сводка не читает ничего.
HISTORY_DEPTH = 2

# Потолок разбора для цели, у которой снимка С ДАННЫМИ не находится вовсе
# (ревью волны 2). Условие остановки «набрать depth снимков с данными» на
# такой цели не выполняется никогда: 04.09 shale_aframe стоил 24 разбора из
# 71, и это число равно длине его истории — через год 365 на каждую вечно
# сломанную цель. Считаются СВОИ снимки цели, а не каталоги прогонов:
# редкую цель (квота агрегатора снимается агентом раз в две-три недели)
# потолок в 60 её снимков отпускает больше чем на три года назад, а цель,
# которую таймер снимает ежедневно, — на два месяца. Потолок обязан быть
# заметно выше глубины «цель снялась один раз и с тех пор падает» (тикет
# 09, 29 провалов подряд): единственные живые цифры экономия памяти стирать
# не имеет права, а к 60 провалам они уже два месяца как помечены «данные
# устарели» (порог 7 суток), и честное «нет данных» вернее.
NO_DATA_SCAN_LIMIT = 60


def load_history(snapshot_root, depth: int | None = None) -> dict:
    """username -> [(run_id, объект снапшота), ...] в хронологическом порядке.

    depth=None — вся история целиком (прежнее поведение; нужно тому, кто
    строит ряд по всем снимкам). depth=N — по каждой цели остаются N её
    последних снимков ЛЮБОГО статуса плюс N последних снимков С ДАННЫМИ:
    ровно то, из чего сводка берёт цифры, динамику и признак «сегодня не
    снялось». Промежуточные провалы в такой список не попадают — сводка их
    и не читает.

    «С данными» здесь означает ровно то же, что и в build_rows
    (_is_data_snapshot): статус ok/partial И хоть одна проверенная ночь.
    Считать по одному статусу нельзя: снимок, где правило окна продаж
    перевело весь горизонт в unknown, статус имеет, а цифр не даёт — два
    таких подряд съедали бы квоту, и динамика теряла бы прошлый живой
    снимок.

    Цена этой строгости замерена на живой истории 04.09 (38 прогонов):
    разборов json 71 -> 109, время 0.31 -> 0.30 с, снимков в памяти 49 в
    обоих случаях. Лишние 38 разборов — это ok_reka и wood_glamp: у них ВСЯ
    история (21 снимок из 21) без единой проверенной ночи, и до потолка
    NO_DATA_SCAN_LIMIT их приходится досматривать. Память при этом не растёт:
    промежуточные провалы разбираются и выбрасываются.

    Зачем ограничение (тикет 09): прежний проход разбирал В ПАМЯТЬ ВСЕ
    снапшоты ради двух последних снимков на цель — 465 объекто-снимков и
    ~120 МБ RSS на 38 прогонах, и это линейно растёт с каждым днём слежки.
    Каталоги перебираются с конца, а json объекта разбирается ТОЛЬКО пока он
    цели ещё нужен: список файлов каталога стоит одного listdir, разбор —
    мегабайтов. Перебор при этом доходит до начала истории намеренно: у
    цели, которую таймер пропускает (квота агрегатора снимается агентом
    браузером), последний живой снимок может быть трёхнедельной давности, и
    остановка «через N каталогов» стёрла бы её из сводки молча.
    """
    root = Path(snapshot_root)
    run_ids = core.list_snapshots(root)
    if depth is None:
        history: dict[str, list] = {}
        for run_id in run_ids:
            snap = core.read_snapshot(root / run_id)
            for username, obj in snap["objects"].items():
                history.setdefault(username, []).append((run_id, obj))
        return history
    picked: dict[str, dict] = {}
    # имя файла -> [всего снимков взято, из них с данными]; ключ файловый,
    # потому что решение «открывать ли json» принимается ДО его разбора
    # (append_object пишет файл как <username>.json).
    taken: dict[str, list[int]] = {}
    for run_id in reversed(run_ids):
        for obj_file in sorted((root / run_id).glob("*.json")):
            if obj_file.name == "run.json":
                continue
            counts = taken.setdefault(obj_file.stem, [0, 0])
            if counts[0] >= depth and (counts[1] >= depth
                                       or counts[0] >= NO_DATA_SCAN_LIMIT):
                continue
            obj = core.load_json(obj_file, core.SnapshotError)
            counts[0] += 1
            is_data = _is_data_snapshot(obj)
            if is_data:
                counts[1] += 1
            elif counts[0] > depth:
                # Промежуточный провал дальше depth последних снимков сводке
                # не нужен ни для цифр, ни для пометки «сегодня не снялось».
                # Разобрать его пришлось (статус читается только из json), а
                # держать в памяти — нет: иначе цель, которая падает месяцами,
                # тянет в память всю свою историю до потолка NO_DATA_SCAN_LIMIT.
                continue
            username = obj.get("username") or obj_file.stem
            picked.setdefault(username, {})[run_id] = obj
    return {username: sorted(runs.items())
            for username, runs in picked.items()}


def _hours_between(prev_iso, cur_iso):
    try:
        prev = datetime.fromisoformat(prev_iso)
        cur = datetime.fromisoformat(cur_iso)
    except (TypeError, ValueError):
        return None
    return round((cur - prev).total_seconds() / 3600, 1)


def _fmt_ago(hours) -> str:
    if hours is None:
        return "прошлого прогона"
    if hours < 1:
        return f"{max(1, int(round(hours * 60)))} мин"
    if hours < 48:
        return f"{hours:g} ч"
    return f"{hours / 24:.0f} дн"


def _has_known_cells(obj: dict) -> bool:
    """Есть ли в сетке хоть одна реально проверенная клетка (free/busy)."""
    return any(cell.get("state") in ("free", "busy")
               for cells in obj.get("units", {}).values()
               for cell in cells.values())


def _is_data_snapshot(obj: dict) -> bool:
    """Снимок, из которого сводке есть что показать.

    Статуса мало (ревью волны 2): ветка «весь горизонт» правила окна продаж
    переводит ВСЕ клетки в unknown и понижает ok до partial — статус
    остаётся «с данными», а известных ночей ноль, и такой снимок молча
    затирал вчерашние проценты. Это ровно дефект тикета 08, только по
    второму каналу, поэтому проверяются оба признака.
    """
    return obj.get("status") in DATA_STATUSES and _has_known_cells(obj)


def _blank_snapshot_reason(obj: dict) -> str:
    """Причина для снимка, который статус имеет, а проверенных ночей — нет.

    У такого снимка поле reason обычно пустое (движок ответил, отказа не
    было), и дежурное «причина не записана» тут врёт: причина видна по самой
    сетке.
    """
    if obj.get("reason"):
        return obj["reason"]
    if not _is_data_snapshot(obj) and obj.get("status") in DATA_STATUSES:
        states = {cell.get("state")
                  for cells in obj.get("units", {}).values()
                  for cell in cells.values()}
        if "sales_not_open" in states:
            return "весь горизонт помечен «продажи не открыты»"
        return "в снимке нет ни одной проверенной ночи"
    return "причина не записана"


# Порог протухания (тикет 08): снимку больше стольких суток — в сводке стоит
# явная пометка. Семь взято как «неделя»: цель, которую таймер снимает
# ежедневно, за неделю обязана сняться хоть раз, а цель, которую снимает
# агент браузером (квота агрегатора), за неделю успевает устареть по смыслу —
# 04.09 таких было две, glamping_pod_nebom (14.08) и hobbitland.ru (15.08).
STALE_AFTER_DAYS = 7


def _iso_date(value):
    """ISO-строка (дата или дата-время) -> date; мусор -> None."""
    try:
        return datetime.fromisoformat(value).date()
    except (TypeError, ValueError):
        return None


def _age_days(checked_at, today_) -> int | None:
    """Возраст снимка в сутках на дату today_; дата не читается -> None."""
    day = _iso_date(checked_at)
    return None if day is None else (today_ - day).days


def _days_word(n: int) -> str:
    """«1 день / 2 дня / 5 дней» — иначе пометка возраста читается как машинная."""
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "дня"
    return "дней"


def _failure_note(failure: dict, today_) -> str:
    """Пометка «последний прогон цель не снял» для таблицы и досье.

    Дата названа прямо: «сегодня» врать нельзя, когда прогона сегодня не
    было вовсе (машина стояла, цель выпала по дедлайну).
    """
    reason = failure.get("reason") or "причина не записана"
    day = _iso_date(failure.get("checked_at"))
    if day is None:
        return f"последний прогон не снялся: {reason}"
    if day == today_:
        return f"сегодня не снялось: {reason}"
    return f"не снялось {day.strftime('%d.%m')}: {reason}"


def _stale_note(stale_days) -> str:
    """Пометка возраста: только когда снимок старше порога, иначе пусто."""
    if stale_days is None or stale_days <= STALE_AFTER_DAYS:
        return ""
    return (f"данные устарели: последний снимок {stale_days} "
            f"{_days_word(stale_days)} назад")


def _fmt_day(checked_at) -> str:
    """«14.08» — дата снимка внутри предложения досье."""
    day = _iso_date(checked_at)
    return day.strftime("%d.%m") if day else "—"


def _month_inventory_gap(units: dict, month: str, inventory_until) -> bool:
    """Есть ли в месяце ночи, посчитанные БЕЗ фонда (тикет 06).

    Это не то же самое, что «движок не отдал фонд» (метка «по типам»): здесь
    фонд на эти ночи вообще НЕ СПРАШИВАЛИ — горизонт фонда ближний (45
    ночей), потому что запрос фонда стоит одного обращения на каждую ночь.
    Занижение такое же, а причина другая, и читатель обязан их различать.

    Правка по ревью волны 2: прежнее условие ловило только месяц ЦЕЛИКОМ за
    границей, поэтому месяц, покрытый фондом наполовину, шёл в отчёт без
    единой оговорки. Завтрашний прогон (today=05.09, фонд до 20.10) — ровно
    этот случай: ночи 21–31.10 остаются без фонда, а на живом снапшоте
    04.09 это у двух TL-объектов окт 42.9% -> 39.5% и 15.1% -> 14.4%.
    Помечаем по КЛЕТКАМ: занижена ровно та ночь, которая посчитана без
    фонда, — плюс прежнее «месяц целиком за границей» для месяцев, где
    клеток нет вовсе.
    """
    until = _iso_date(inventory_until)
    if until is None:
        return False
    year, mon = (int(x) for x in month.split("-"))
    if date(year, mon, 1) > until:
        return True
    for cells in units.values():
        for day, cell in cells.items():
            if not day.startswith(month):
                continue
            if cell.get("state") not in ("free", "busy"):
                continue          # в процент идут только проверенные ночи
            checked = _iso_date(day)
            if checked is not None and checked > until:
                return True
    return False


def _nights_word(n: int) -> str:
    """Дательный падеж для «по N ноч…»: 1/21/31 — ночи, остальное — ночам."""
    return "ночи" if n % 10 == 1 and n % 100 != 11 else "ночам"


def _days_in_horizon(month: str, checked_at) -> int:
    """Сколько дат месяца попадает в горизонт снимка (от даты снятия)."""
    year, mon = (int(x) for x in month.split("-"))
    last_day = calendar.monthrange(year, mon)[1]
    try:
        checked = datetime.fromisoformat(checked_at).date()
    except (TypeError, ValueError):
        return last_day
    if (year, mon) < (checked.year, checked.month):
        return 0
    if (year, mon) == (checked.year, checked.month):
        return last_day - checked.day + 1
    return last_day


def _month_sample(units: dict, month: str, checked_at) -> dict:
    """Размер выборки месяца: сколько дат проверено против дней в горизонте.

    checked — даты, где хоть у одного юнита известное состояние (free/busy/
    sales_not_open); pct_nights — даты, вошедшие в процент (free/busy).
    Оговорку «по N ночам» задаёт pct_nights, а не checked (см. _sample_note):
    ночь «продажи не открыты» в знаменатель занятости не входит, и считать
    её проверенной для этой оговорки — значит молчать ровно там, где процент
    посчитан по одной ночи из тридцати одной.
    """
    checked_dates = set()
    pct_dates = set()
    for cells in units.values():
        for d, cell in cells.items():
            if not d.startswith(month):
                continue
            state = cell.get("state")
            if state in ("free", "busy"):
                checked_dates.add(d)
                pct_dates.add(d)
            elif state == "sales_not_open":
                checked_dates.add(d)
    return {"checked": len(checked_dates), "pct_nights": len(pct_dates),
            "expected": _days_in_horizon(month, checked_at)}


def _sample_note(sample) -> str:
    """«по N ночам», когда процент посчитан не по всем дням горизонта месяца.

    Находка ревью 14.08: «сен 0%» glamping_pod_nebom был посчитан по 3 ночам
    квоты агрегатора — без оговорки такой ноль читается как пустой месяц.

    Правка по ревью волны 2: считаются ночи, вошедшие в ПРОЦЕНТ (free/busy),
    а не «проверенные» вообще. Прежнее условие держало ночь «продажи не
    открыты» за проверенную, и после разметки стены (30+ таких ночей разом)
    процент по ОДНОЙ ночи из 31 печатался без единой оговорки: один объект,
    октябрь — «50% (будни 50%)» вместо вчерашних 98.7%.
    """
    if not sample:
        return ""
    n = sample["pct_nights"]
    if not 0 < n < sample["expected"]:
        return ""
    return f"по {n} {_nights_word(n)}"


def _one_line(text) -> str:
    """Чужой текст в одну строку: переводы строки и табы схлопнуты в пробел.

    Причина отказа приходит от чужого движка (bronirui отдаёт `message` как
    есть) и попадает и в таблицу, и в предложение досье; многострочный текст
    рвёт и то, и другое.
    """
    return " ".join(str(text).split())


def _missing_label(recipe) -> str:
    if recipe is None:
        return "ещё не снимался: рецепта нет — нужна разведка"
    if recipe.get("status") == "broken":
        reason = _one_line(recipe.get("broken_reason")
                           or "причина не записана")
        return f"ещё не снимался: рецепт сломан — {reason}"
    return "ещё не снимался в прогонах"


def _basis_label(units: dict) -> str:
    """Хвост метки источника: по домикам объект посчитан или по типам.

    Правка 15.08: у TravelLine и Bnovo юнит — это ТИП размещения, а «А-фрейм
    ×3» на площадке обычное дело. Где фонд типа снят, занятость считается по
    домико-ночам; где нет — тип занят только когда продан последний домик,
    и цифра занижена. Читатель обязан видеть, что именно перед ним.
    """
    info = core.unit_basis(units)
    if not info["units_total"]:
        return ""
    if info["basis"] == "unit":
        return f", по домикам: {info['homes']} в {info['units_total']} типах"
    if info["basis"] == "mixed":
        return (f", по домикам частично: фонд снят у "
                f"{info['units_with_capacity']} из {info['units_total']} "
                f"типов, остальные по типу — занижение")
    return ", по типам (фонд не снят): занижение там, где домиков в типе несколько"


def _source_label(obj, recipe, failure_note: str = "",
                  stale_note: str = "") -> str:
    engine = _engine_label(obj.get("engine"))
    if obj.get("status") == "insufficient_data":
        reason = obj.get("reason") or "причина не записана"
        return f"нет данных: {reason}"
    if obj.get("source_kind") == "aggregator_quota":
        label = f"квота агрегатора ({engine})"
    elif obj.get("granularity") == "per_unit":
        label = f"модуль, поюнитно ({engine})"
        label += _basis_label(obj.get("units", {}))
    else:
        label = f"модуль, агрегат ({engine})"
        # Тикет 02: агрегат красит день занятым, только когда проданы ВСЕ
        # юниты, — его процент занижает загрузку (оценка СНИЗУ, в отличие
        # от поюнитной оценки сверху). Помечаем лишь строки с реальными
        # данными: у пустых/sales_not_open сравнивать нечего.
        if _has_known_cells(obj):
            label += " — агрегат: оценка снизу"
    if obj.get("status") == "partial":
        reason = obj.get("reason") or ""
        label += f" — частично: {reason}" if reason else " — частично"
    # Провал последнего прогона и возраст данных — ОТДЕЛЬНЫЕ пометки, а не
    # замена цифр: цифры снятые, просто снятые не сегодня (тикет 08).
    if failure_note:
        label += f" — {failure_note}"
    if stale_note:
        label += f" — {stale_note}"
    return label


def build_rows(targets: list, recipes: dict, history: dict,
               months: list[str], today_=None) -> list[dict]:
    """Модель строк сводки в порядке targets (порядок = важность, см. _note).

    Цели с reference=true (объект-ориентир, а не кандидат в клиенты) уезжают
    в хвост стабильной сортировкой: порядок кандидатов = рейтинг волны 2, и
    вставленный в середину референс сдвинул бы его на строку.

    today_ — «сегодня» для пометок возраста и провала (шов подмены даты в
    тестах); не передан — берётся системная дата, как и было.
    """
    day_today = today_ if today_ is not None else core.today()
    rows = []
    ordered = sorted(targets, key=lambda t: bool(isinstance(t, dict)
                                                 and t.get("reference")))
    for target in ordered:
        if not isinstance(target, dict) or not target.get("username"):
            continue
        username = target["username"]
        recipe = recipes.get(username)
        row = {
            "username": username,
            "site": target.get("site") or (recipe or {}).get("site", ""),
            "priority": target.get("priority"),
            # Флаг кладётся в БАЗОВЫЙ словарь, а не в row.update ветки с
            # данными: иначе строка «ещё не снимался» осталась бы без
            # пометки и до первого снимка референс выглядел бы кандидатом.
            "reference": bool(target.get("reference")),
        }
        entries = history.get(username, [])
        if not entries:
            row.update(status="missing", reason="", metrics=None,
                       dynamics=None, quota=False, run_id=None,
                       checked_at=None, engine=(recipe or {}).get("engine", ""),
                       granularity=None, sample=None, aggregate_floor=False,
                       units={}, source_label=_missing_label(recipe),
                       last_failure=None, stale_days=None, failure_note="",
                       stale_note="", inventory_until=None, inventory_gap={})
            rows.append(row)
            continue
        # Цифры берём из последнего снимка С ДАННЫМИ, а не из последнего
        # вообще (тикет 08): цель с рецептом знакомого движка идёт в прогон
        # всегда, и её сегодняшний insufficient_data стирал вчерашнюю
        # загрузку. Пустой снимок при этом не выбрасывается — он становится
        # отдельной пометкой «сегодня не снялось: причина».
        with_data = [e for e in entries if _is_data_snapshot(e[1])]
        failure = None
        if with_data:
            run_id, obj = with_data[-1]
            previous = with_data[-2] if len(with_data) > 1 else None
            if entries[-1][0] != run_id:
                failure = entries[-1]
        else:
            # Живых цифр нет вовсе — честное «нет данных» с причиной, как и
            # было (ровно случай shale_aframe: все 24 снимка пустые).
            run_id, obj = entries[-1]
            previous = entries[-2] if len(entries) > 1 else None
        metrics = core.aggregate(obj.get("units", {}), months)
        sample = {m: _month_sample(obj.get("units", {}), m,
                                   obj.get("checked_at"))
                  for m in months}
        inventory_until = obj.get("inventory_until")
        inventory_gap = {m: _month_inventory_gap(obj.get("units", {}), m,
                                                 inventory_until)
                         for m in months}
        dynamics = None
        if previous is not None:
            prev_run_id, prev_obj = previous
            d = core.diff_units(obj.get("units", {}),
                                prev_obj.get("units", {}), months)
            # Месяцы, у которых между снимками сменилось ОСНОВАНИЕ счёта:
            # часть ночей была посчитана с фондом, а стала без него (или
            # наоборот). Сдвиг процента в таком месяце — методический, и без
            # оговорки читатель прочтёт его как отток броней (приёмка
            # тикета 06). Флаг ставится только на переходе: пока граница
            # фонда одинаково режет оба снимка, оговорки нет.
            prev_gap = {m: _month_inventory_gap(prev_obj.get("units", {}), m,
                                                prev_obj.get("inventory_until"))
                        for m in months}
            dynamics = {
                "prev_run_id": prev_run_id,
                "hours": _hours_between(prev_obj.get("checked_at"),
                                        obj.get("checked_at")),
                "months": d["months"],
                "newly_busy": d["newly_busy"],
                "newly_sold": d.get("newly_sold", []),
                "gap_changed": [m for m in months
                                if inventory_gap[m] != prev_gap[m]],
            }
        last_failure = None
        failure_note = ""
        if failure is not None:
            fail_run_id, fail_obj = failure
            last_failure = {
                "run_id": fail_run_id,
                "checked_at": fail_obj.get("checked_at"),
                "reason": _blank_snapshot_reason(fail_obj),
                "status": fail_obj.get("status"),
            }
            failure_note = _failure_note(last_failure, day_today)
        stale_days = _age_days(obj.get("checked_at"), day_today)
        # Возраст помечается только там, где есть ЧТО стареть: у строки
        # «нет данных» цифр нет вовсе, и вторая пометка про их свежесть
        # только шумит.
        stale_note = _stale_note(stale_days) if with_data else ""
        row.update(status=obj.get("status"), reason=obj.get("reason", ""),
                   metrics=metrics, dynamics=dynamics,
                   quota=obj.get("source_kind") == "aggregator_quota",
                   run_id=run_id, checked_at=obj.get("checked_at"),
                   engine=obj.get("engine"),
                   granularity=obj.get("granularity"), sample=sample,
                   aggregate_floor=(obj.get("granularity") == "aggregate"
                                    and _has_known_cells(obj)),
                   units=obj.get("units", {}),
                   site=obj.get("site") or row["site"],
                   last_failure=last_failure, stale_days=stale_days,
                   failure_note=failure_note, stale_note=stale_note,
                   inventory_until=inventory_until,
                   inventory_gap=inventory_gap,
                   source_label=_source_label(obj, recipe, failure_note,
                                              stale_note))
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Клетки таблицы и досье (общие для md и html)
# ---------------------------------------------------------------------------

def _month_cell(m: dict, sample=None, inventory_gap: bool = False) -> str:
    cuts = m["cuts"]
    if cuts["all"]["known"] == 0:
        if m["sales_not_open"]:
            return "продажи не открыты"
        return "нет данных"
    text = f"{cuts['all']['pct']:.0f}%"
    detail = []
    if cuts["weekday"]["pct"] is not None:
        detail.append(f"будни {cuts['weekday']['pct']:.0f}%")
    if cuts["weekend"]["pct"] is not None:
        detail.append(f"вых {cuts['weekend']['pct']:.0f}%")
    note = _sample_note(sample)
    if note:
        detail.append(note)
    if inventory_gap:
        detail.append("фонд не снимался")
    if detail:
        text += " (" + ", ".join(detail) + ")"
    if m["sales_not_open"]:
        text += ", часть дней продажи не открыты"
    return text


def _dynamics_cell(row: dict) -> str:
    if row["status"] in ("missing", "insufficient_data"):
        return "—"  # динамики нет там, где нет данных
    dyn = row["dynamics"]
    if dyn is None:
        return "первый снимок"
    parts = []
    gap_changed = dyn.get("gap_changed") or []
    for month, shift in dyn["months"].items():
        delta = shift["delta_pp"]
        if delta is None or delta == 0:
            continue
        text = (f"{core.MONTH_LABELS_RU[int(month.split('-')[1])]} "
                f"{delta:+.1f} пп")
        # Сдвиг там, где сменилась граница фонда, — не брони, а смена
        # основания счёта. Без этой оговорки первый прогон с ближним
        # горизонтом фонда отдаёт «окт -3.4 пп» как отток (ревью волны 2).
        if month in gap_changed:
            text += " (сменилась граница фонда, не брони)"
        parts.append(text)
    newly = len(dyn["newly_busy"])
    sold = len(dyn.get("newly_sold") or [])
    if not parts and not newly and not sold:
        return f"без изменений за {_fmt_ago(dyn['hours'])}"
    if newly:
        parts.append(f"новых занятых дат: {newly}")
    if sold:
        # движение внутри типа: продали ещё домик, но не последний —
        # до появления фонда типов такие продажи были не видны вовсе
        parts.append(f"допродано домиков в типах: {sold}")
    return "; ".join(parts)


def _fmt_generated(generated_at) -> str:
    """«14.08.2026, 22:16» вместо ISO-строки: подвал читает человек."""
    try:
        dt = datetime.fromisoformat(generated_at)
    except (TypeError, ValueError):
        return str(generated_at)
    return dt.strftime("%d.%m.%Y, %H:%M")


def _fmt_checked(checked_at) -> str:
    try:
        dt = datetime.fromisoformat(checked_at)
    except (TypeError, ValueError):
        return "—"
    return dt.strftime("%d.%m %H:%M")


def _metric(row: dict, month: str):
    for m in row["metrics"] or []:
        if m["month"] == month:
            return m
    return None


# Слова досье про месяцы после первого. «Осень» — только когда это реально
# сен-ноя (ревью 14.08: при параметризуемом --months жёсткая «осень» врала).
_TAIL_AUTUMN = {
    "intro": "Дальше по осени: ",
    "none": "По осени данных нет.",
    "empty": "Осень практически пустая — прямой кандидат под оффер "
             "«заполним осень».",
    "half": "Осень заполнена меньше чем наполовину — есть заметные дыры "
            "под дозаполнение.",
    "sold": "Осень уже неплохо продана — оффер «заполним осень» здесь "
            "слабее, разговор скорее про донабор в дыры.",
}
_TAIL_GENERIC = {
    "intro": "Дальше по горизонту: ",
    "none": "Дальше по горизонту данных нет.",
    "empty": "Дальше по горизонту практически пусто — прямой кандидат под "
             "оффер «заполним свободные даты».",
    "half": "Дальше по горизонту заполнено меньше чем наполовину — есть "
            "заметные дыры под дозаполнение.",
    "sold": "Дальше по горизонту продано уже неплохо — оффер дозаполнения "
            "здесь слабее, разговор скорее про донабор в дыры.",
}


# Вердикт про оффер у объекта-ориентира — прямое самопротиворечие: строка
# помечена «не кандидат» и тут же названа кандидатом под оффер. Поэтому у
# референса вердикт заменяется ролью, а всё остальное досье (проценты,
# будни/выходные, пометки честности, динамика) остаётся как у всех: он
# снимается наравне, отличается только тем, зачем на него смотрят.
REFERENCE_VERDICT = ("Это референс, а не кандидат: объект снимается ради "
                     "сравнения, оффер к нему не относится.")


def _tail_wording(months: list[str]) -> dict:
    """Формулировки про months[1:]: осенние — только для сен/окт/ноя."""
    tail = months[1:]
    if tail and all(int(m.split("-")[1]) in (9, 10, 11) for m in tail):
        return _TAIL_AUTUMN
    return _TAIL_GENERIC


def _dossier_note(row: dict, month: str) -> str:
    """«(по N ночам)» для предложения досье, когда месяц проверен не весь."""
    note = _sample_note((row.get("sample") or {}).get(month))
    return f" ({note})" if note else ""


def _gap_months(row: dict, months: list[str]) -> list[str]:
    """Месяцы, на которые фонд не спрашивался вовсе (горизонт фонда ближе)."""
    gap = row.get("inventory_gap") or {}
    return [m for m in months if gap.get(m)]


def _honesty_sentences(row: dict, months: list[str]) -> list[str]:
    """Оговорки, которые меняют чтение цифр: горизонт фонда, провал
    последнего прогона, возраст данных. Все три говорят одно — цифры в
    строке настоящие, но снятые не про то и не тогда, что читатель решит
    по умолчанию."""
    out = []
    gap = _gap_months(row, months)
    if gap:
        out.append(
            f"Фонд типов дальше {_fmt_day(row.get('inventory_until'))} не "
            f"снимался: за {', '.join(_month_full(m) for m in gap)} проценты "
            f"занижены там, где домиков в типе несколько.")
    if row.get("failure_note"):
        note = row["failure_note"]
        out.append(f"{note[0].upper()}{note[1:]}; в таблице цифры последнего "
                   f"удачного снимка от {_fmt_day(row.get('checked_at'))}.")
    if row.get("stale_note"):
        days = row["stale_days"]
        out.append(f"Данные устарели: последнему удачному снимку {days} "
                   f"{_days_word(days)}.")
    return out


def dossier(row: dict, months: list[str]) -> str:
    """Досье объекта: 2–4 связных предложения человеческим текстом."""
    if row["status"] == "missing":
        detail = row["source_label"].split(": ", 1)[-1]
        return f"Объект ещё ни разу не снимался: {detail}."
    if row["status"] == "insufficient_data":
        return (f"Данных нет: {row['reason'] or 'причина не записана'}. "
                f"Для оффера это само по себе факт: измеримого онлайн-канала "
                f"продаж, по которому можно проверить загрузку, у объекта "
                f"сейчас не видно.")
    words = _tail_wording(months)
    sentences = []
    first = _metric(row, months[0])
    if first is not None:
        cuts = first["cuts"]
        name = _month_full(months[0]).capitalize()
        if cuts["all"]["known"] == 0 and first["sales_not_open"]:
            sentences.append(f"{name}: онлайн-продажи не открыты — загрузку "
                             f"по виджету не видно.")
        elif cuts["all"]["known"] == 0:
            sentences.append(f"{name}: данных нет.")
        else:
            text = f"{name} занят на {cuts['all']['pct']:.0f}%"
            if (cuts["weekday"]["pct"] is not None
                    and cuts["weekend"]["pct"] is not None):
                text += (f" — будни {cuts['weekday']['pct']:.0f}%, "
                         f"выходные {cuts['weekend']['pct']:.0f}%")
            sentences.append(text + _dossier_note(row, months[0]) + ".")
    tail_metrics = [(_metric(row, m), m) for m in months[1:]]
    tail_pcts = []
    tail_parts = []
    sno_only = False
    for m, month in tail_metrics:
        if m is None:
            continue
        if m["cuts"]["all"]["known"] == 0:
            if m["sales_not_open"]:
                tail_parts.append(f"{_month_full(month)} — продажи не открыты")
                sno_only = True
            continue
        pct = m["cuts"]["all"]["pct"]
        tail_pcts.append(pct)
        tail_parts.append(f"{_month_full(month)} — {pct:.0f}%"
                          + _dossier_note(row, month))
    if tail_parts:
        sentences.append(words["intro"] + ", ".join(tail_parts) + ".")
    if tail_pcts:
        avg = sum(tail_pcts) / len(tail_pcts)
        if row.get("reference"):
            sentences.append(REFERENCE_VERDICT)
        elif avg < 20:
            sentences.append(words["empty"])
        elif avg < 50:
            sentences.append(words["half"])
        else:
            sentences.append(words["sold"])
        for m, month in tail_metrics:
            if m is None or m["cuts"]["all"]["known"] == 0:
                continue
            wd, we = m["cuts"]["weekday"]["pct"], m["cuts"]["weekend"]["pct"]
            if wd is not None and we is not None and we - wd >= 25:
                prep = MONTH_PREP_RU[int(month.split("-")[1])]
                sentences.append(
                    f"Брони живут выходными: в {prep} будни "
                    f"{wd:.0f}% против выходных {we:.0f}% — простаивают "
                    f"именно будни.")
                break
    elif not sno_only and first is not None and not tail_parts:
        sentences.append(words["none"])
    if row["quota"]:
        sentences.append(f"Цифры — квота агрегатора "
                         f"({_engine_label(row['engine'])}): владелец отдаёт "
                         f"туда часть фонда, полной загрузки здесь не видно.")
    if row.get("aggregate_floor"):
        sentences.append("Сетка агрегатная: день здесь занят, только когда "
                         "проданы все юниты сразу, поэтому проценты — "
                         "оценка снизу, реальная загрузка может быть выше.")
    if row["status"] == "partial" and row["reason"]:
        sentences.append(f"Снимок неполный: {row['reason']}.")
    sentences += _honesty_sentences(row, months)
    dyn = row["dynamics"]
    if dyn is not None:
        cell = _dynamics_cell(row)
        if cell.startswith("без изменений"):
            sentences.append(f"С прошлого снимка ({_fmt_ago(dyn['hours'])} "
                             f"назад) изменений нет.")
        else:
            sentences.append(f"С прошлого снимка ({_fmt_ago(dyn['hours'])} "
                             f"назад): {cell}.")
    return " ".join(sentences)


def dossier_short(row: dict, months: list[str]) -> str:
    """Досье для html-страницы: одно-два предложения о том, чего НЕТ в
    строке таблицы, — где дыра и что это значит для оффера.

    Проценты не пересказываются: они стоят в той же строке выше (правка
    14.08 по фидбеку оператора «один факт — один способ показа»). Пометки
    честности, которые меняют чтение цифр (квота, агрегат, неполный
    снимок), остаются короткой третьей фразой. md-сводка продолжает нести
    ПОЛНОЕ досье (`dossier`): там это единственный текст об объекте.
    """
    if row["status"] == "missing":
        detail = row["source_label"].split(": ", 1)[-1]
        return f"Ещё ни разу не снимался: {detail}."
    if row["status"] == "insufficient_data":
        return (f"Измеримого онлайн-канала продаж, по которому видно "
                f"загрузку, у объекта нет: "
                f"{row['reason'] or 'причина не записана'}. Для оффера это "
                f"само по себе факт.")
    known = [(m, x) for m, x in ((m, _metric(row, m)) for m in months)
             if x is not None and x["cuts"]["all"]["known"]]
    if not known:
        if any(x is not None and x["sales_not_open"]
               for x in (_metric(row, m) for m in months)):
            return ("Онлайн-продажи на горизонте ещё не открыты — загрузку "
                    "по виджету не видно.")
        return "Проверенных ночей на горизонте нет — судить пока не по чему."
    sentences = []
    thin_month, thin = min(known, key=lambda p: p[1]["cuts"]["all"]["pct"])
    if thin["cuts"]["all"]["pct"] >= 70:
        hole = "Свободных ночей на горизонте почти не осталось"
    else:
        hole = ("Больше всего свободных ночей в "
                f"{MONTH_PREP_RU[int(thin_month.split('-')[1])]}")
    for _, metric in known:
        wd, we = metric["cuts"]["weekday"]["pct"], metric["cuts"]["weekend"]["pct"]
        if wd is not None and we is not None and we - wd >= 25:
            hole += "; простаивают будни — брони живут выходными"
            break
    sentences.append(hole + ".")
    words = _tail_wording(months)
    tail = [x["cuts"]["all"]["pct"] for m, x in known if m != months[0]]
    if row.get("reference"):
        sentences.append(REFERENCE_VERDICT)
    elif tail:
        avg = sum(tail) / len(tail)
        sentences.append(words["empty"] if avg < 20
                         else words["half"] if avg < 50 else words["sold"])
    else:
        sentences.append(words["none"])
    if row["quota"]:
        sentences.append("Видна только квота агрегатора — не весь фонд "
                         "объекта.")
    elif row.get("aggregate_floor"):
        sentences.append("Сетка агрегатная: реальная загрузка может быть "
                         "выше показанной.")
    elif row["status"] == "partial" and row["reason"]:
        sentences.append(f"Снимок неполный: {row['reason']}.")
    # Провал прогона и возраст данных не «одна из оговорок на выбор», а
    # отдельный факт: без него читатель решит, что цифры снялись сегодня.
    if _gap_months(row, months):
        sentences.append("Фонд типов на дальние месяцы не снимался — там "
                         "проценты занижены.")
    if row.get("failure_note"):
        note = row["failure_note"]
        sentences.append(f"{note[0].upper()}{note[1:]}; цифры от "
                         f"{_fmt_day(row.get('checked_at'))}.")
    if row.get("stale_note"):
        sentences.append(f"Данные устарели: снимку {row['stale_days']} "
                         f"{_days_word(row['stale_days'])}.")
    return " ".join(sentences)


# ---------------------------------------------------------------------------
# Рендер markdown
# ---------------------------------------------------------------------------

HOW_TO_READ = (
    "Что это: снимок текущей загрузки глэмпингов-кандидатов (плюс объекты с "
    "пометкой «референс» — не кандидаты, а ориентиры для сравнения), снятый "
    "напрямую "
    "из модулей бронирования на их сайтах (а где модуля нет — с агрегатора). "
    "Поюнитные проценты — оценка сверху: занятая дата и дата, закрытая "
    "владельцем вручную, в виджете неотличимы, поэтому реальная выручка "
    "может быть ниже показанной занятости. Считается загрузка ДОМИКОВ: в "
    "одном глэмпинге бывает несколько одинаковых домиков под одним "
    "названием, и там, где помечено «по домикам», инструмент знает, сколько "
    "их и сколько из них продано. Пометка «по типам» означает обратное: "
    "фонд снять не удалось, и день типа считается занятым, только когда "
    "продан последний домик, — такая цифра занижена. Строки с пометкой "
    "«агрегат — оценка снизу» считаны по общему календарю объекта: там день "
    "занят, только когда проданы все юниты сразу, и реальная загрузка может "
    "быть выше. Строки с пометкой «квота агрегатора» показывают только ту "
    "часть домиков, которую владелец отдал агрегатору, — это не полная "
    "загрузка объекта. Там, где данные снять не удалось, стоит честная "
    "строка «нет данных» с причиной."
)

TABLE_NOTE = (
    "Клетка месяца: общая занятость и рядом отдельно будни (пн–чт) и "
    "выходные (пт–вс; пятница — выходной: заезды живут уик-эндами). "
    "«Продажи не открыты» — владелец ещё не открыл онлайн-продажи на месяц: "
    "такие дни в процент не входят. Пометка «по N ночам» — процент месяца "
    "посчитан по стольким ночам, а не по всему месяцу: остальные ночи либо "
    "не проверены, либо не в продаже, и пустым месяц из такого процента не "
    "следует. Пометка «фонд "
    "не снимался» — на этот месяц фонд типов вообще не спрашивался (он "
    "снимается только на ближних ночах, потому что стоит запроса на каждую "
    "ночь): это не отказ движка, а граница вопроса, и процент там занижен "
    "так же, как «по типам»; приписка «сменилась граница фонда, не брони» в "
    "колонке «Динамика» означает, что месяц посчитан на другом основании, "
    "чем в прошлом снимке, — сдвиг процента там методический, а не про "
    "продажи. Динамика — "
    "сдвиг занятости к предыдущему снимку этого же объекта, в процентных "
    "пунктах (пп); в снимке от 15 августа часть сдвигов — это переход на "
    "поштучный счёт домиков, а не новые брони: там, где в типе стоит "
    "несколько одинаковых домиков, прежняя цифра была занижена (у "
    "«Заповедника» +23 пп именно поэтому). Порядок строк — по важности "
    "кандидата (скоринг волны 2). Строка с пометкой «референс» — не кандидат "
    "в клиенты, а ориентир для сравнения: объект другого масштаба, который "
    "снимается наравне со всеми, но в скоринг не входит и стоит последним."
)

# Шапка html-страницы (редакция 14.08, фидбек «передушил с графикой»): до
# цифр читатель проходит три предложения, а не два раздела методики. Полный
# текст «как читать» не потерян — рамки источников стоят под заголовком
# таблицы (SOURCE_NOTE), а md-сводка несёт HOW_TO_READ целиком.
HTML_LEDE = (
    "Снимок текущей загрузки глэмпингов-кандидатов, снятый напрямую из "
    "модулей бронирования на их сайтах, а где модуля нет — с агрегатора. "
    "Строки с пометкой «референс» — не кандидаты, а ориентиры для сравнения. "
    "Проценты — оценка сверху: занятая дата и дата, закрытая владельцем "
    "вручную, в виджете неотличимы, поэтому реальная выручка может быть "
    "ниже показанной занятости."
)

SOURCE_NOTE = (
    "Пометка «по домикам» означает, что инструмент знает, сколько одинаковых "
    "домиков в каждом типе и сколько из них продано; «по типам» — что фонд "
    "снять не удалось и день типа считается занятым только когда продан "
    "последний домик, то есть цифра занижена. Строки с пометкой «агрегат: "
    "оценка снизу» считаны по общему календарю объекта: там день занят, "
    "только когда проданы все юниты сразу, и реальная загрузка может быть "
    "выше. Строки с пометкой «квота агрегатора» показывают только ту часть "
    "домиков, которую владелец отдал агрегатору, — это не полная загрузка "
    "объекта."
)

# Методика и проверки честности — доработка 14.08 по фидбеку оператора:
# читатель сводки (руководитель, не технарь) должен понять механизм и
# суметь проверить цифры сам. Числа проверок — из pilot-report.md и
# комментариев тикетов 02–07, не менять без пересверки с документами.

HOW_IT_WORKS_TITLE = "Как это работает"

HOW_IT_WORKS = (
    "Каждый глэмпинг продаёт ночи через модуль бронирования на своём "
    "сайте. Когда гость выбирает даты, модуль спрашивает у системы "
    "бронирования, какие ночи свободны и почём. Инструмент читает ровно "
    "те же ответы — те же данные, что видит гость в окне бронирования, "
    "только сразу по всем датам и всем домикам.",
    "Отдельным шагом инструмент спрашивает у системы бронирования, сколько "
    "номеров каждого типа свободно на каждую ночь. Это важно, потому что "
    "одинаковых домиков в типе часто несколько: без этого шага «А-фрейм» с "
    "тремя домиками выглядел бы свободным, пока не продан последний, и "
    "загрузка занижалась бы. Там, где система такой счётчик не отдаёт, "
    "в сводке стоит пометка «по типам» — цифра занижена, и это видно.",
    "Один раз на объект агент-разведчик находит сайт, модуль и систему "
    "бронирования (TravelLine, Bnovo, «Бронируй Онлайн», LitePMS) и "
    "записывает «рецепт» — откуда и как читать календарь. Дальше снимок "
    "делает скрипт строго по рецепту — одинаково каждый раз, без человека "
    "и без ИИ, поэтому быстро и воспроизводимо.",
    "Для объектов без модуля на сайте берётся календарь с Островка — "
    "всегда с пометкой «квота агрегатора»: это только та часть домиков, "
    "которую владелец отдал площадке, а не вся загрузка.",
    "Каждый снимок сохраняется с датой. Сравнение снимков показывает, "
    "заполняется ли календарь и с какой скоростью.",
    "Чего инструмент не делает: не бронирует, не отправляет формы, не "
    "обходит защиты сайтов. Если данные снять не удалось — в сводке "
    "честно стоит «нет данных» с причиной, а не догадка.",
)

TRUST_TITLE = "Почему цифрам можно доверять"

TRUST_DATE_NOTE = "Проверки от 14.08.2026, поштучный учёт домиков — от 15.08.2026."

TRUST_CHECKS = (
    # Витрина: имена объектов пилота заменены на обезличенные подписи
    # (движок), числа проверки метода сохранены.
    "Сверка с независимым ручным осмотром 11 августа — тогда календари "
    "просматривали в браузере вручную, глазами. Объект A (Bnovo): август "
    "48% против 47%. Объект B (Bnovo): сентябрь 26,7% против ~27% — заняты те же "
    "самые пары пятница–суббота (4–5, 11–12, 18–19 и 25–26 сентября). "
    "Объект C (Bnovo): август — те же три даты (14, 15 и 27 августа). "
    "Объект D (LitePMS): сентябрь дата в дату (домик 1 — 10%, домик 2 — 0%). "
    "Расхождения объяснимы поимённо бронями за прошедшие три дня: у "
    "объекта E («Бронируй Онлайн») 66,7% против 55,6% — добавились ровно две ночи, "
    "23 и 27 августа. Важно: эта сверка делалась 14 августа, когда счёт шёл "
    "по типам домиков; после перехода на поштучный учёт (15 августа) те же "
    "объекты показывают выше — у объекта A август 52% вместо 48%, у "
    "объекта F (TL) 51% вместо 28%.",
    "Воспроизводимость: повторные прогоны в тот же вечер дают идентичные "
    "сетки — ноль расхождений по клеткам у объектов A, D и E.",
    "Перекрёстная проверка цен: одна и та же ночь у объекта G на "
    "Островке совпала между двумя независимыми заходами копейка в "
    "копейку по обеим категориям.",
    "Живая динамика ловится и подтверждается: у объекта B между снимками "
    "с разницей в час появились четыре новые занятые даты (одна категория, "
    "будни 7–10 сентября) — следующий прогон показал их же.",
    "Проверка руками за минуту — доступна каждому: возьмите любой объект "
    "и дату из таблицы, откройте сайт объекта и выберите эти даты в окне "
    "бронирования — увидите то же самое (свободно/занято и цену). У "
    "инструмента нет другого источника, кроме этого же окна.",
    "Инструмент честен о границах метода: «занято» и «закрыто владельцем» "
    "в модулях неотличимы, поэтому все поюнитные проценты — оценка "
    "сверху; агрегатные (когда система показывает объект одним календарём "
    "целиком) — оценка снизу, и они помечены; квота агрегатора — не вся "
    "загрузка; «продажи не открыты» занятостью не считается.",
    "Несколько одинаковых домиков в одном типе учитываются поштучно. "
    "Проверка 15.08 на объекте H (TL Integration): две недели 16–29 августа по типам "
    "давали 24% занятости, по домикам — не меньше 30,4%; один тип "
    "стоит в двух экземплярах (7% против 25%), другой "
    "— минимум в трёх, и по типам он показывал 0% занятости "
    "за две недели, хотя продавался четыре ночи подряд. Там, где счётчик "
    "свободных номеров система не отдаёт, строка помечена «по типам» — "
    "такие проценты по-прежнему занижены, и это видно в таблице.",
    "Метод точнее ручного осмотра: агрегатные календари ручной проверки "
    "занижали занятость по объяснимой причине — TravelLine красит день "
    "занятым, только когда проданы все типы домиков сразу. Пример: у "
    "объекта I (TL) ручной осмотр видел 0% августа, поюнитный съём — 46% "
    "(в выходные 21–22 августа свободны только нежилые позиции, "
    "все лоджи проданы). То же: объект J (TL) 71% "
    "вместо 0%, объект H 31% вместо 0%.",
    "Всё сырьё хранится: каждый процент разложим до конкретных ночей и "
    "ответов систем бронирования — датированные снимки лежат в "
    "agent-runtime, любой вывод можно перепроверить.",
)

TABLE_TITLE = "Загрузка по объектам"


def _md_cell(text) -> str:
    """Текст в клетку markdown-таблицы.

    Вертикальная черта закрывает клетку, перевод строки — всю таблицу, а
    текст сюда приходит чужой: сообщение движка живёт в `broken_reason`
    рецепта и вклеивается в подпись источника. Волна 1 закрыла этот же
    дефект у `reason` СНАПШОТА (нормализация в пробнике), сюда он приехал
    вторым каналом (ревью волны 2).
    """
    return _one_line(text).replace("|", "\\|")


def render_markdown(rows: list[dict], months: list[str], meta: dict) -> str:
    month_headers = [
        f"{core.MONTH_LABELS_RU[int(m.split('-')[1])]} {m.split('-')[0]}"
        for m in months]
    lines = [
        "# Загрузка глэмпингов — снимок перед интервью",
        "",
        HOW_TO_READ,
        "",
        f"## {HOW_IT_WORKS_TITLE}",
        "",
    ]
    for para in HOW_IT_WORKS:
        lines += [para, ""]
    lines += [f"## {TRUST_TITLE}", "", TRUST_DATE_NOTE, ""]
    for i, check in enumerate(TRUST_CHECKS, 1):
        lines += [f"{i}. {check}", ""]
    lines += [
        f"## {TABLE_TITLE}",
        "",
        TABLE_NOTE,
        "",
        "| Объект | " + " | ".join(month_headers)
        + " | Динамика | Снят | Источник |",
        "|---|" + "---|" * (len(months) + 3),
    ]
    for row in rows:
        if row["metrics"] is None or row["status"] == "insufficient_data":
            cells = ["—"] * len(months)
        else:
            cells = [_month_cell(_metric(row, m),
                                 (row.get("sample") or {}).get(m),
                                 (row.get("inventory_gap") or {}).get(m, False))
                     for m in months]
        cells += [_dynamics_cell(row), _fmt_checked(row["checked_at"]),
                  row["source_label"]]
        lines.append(f"| {_md_cell(_row_name(row))} | "
                     + " | ".join(_md_cell(c) for c in cells) + " |")
    lines += ["", "## Досье по объектам", ""]
    for row in rows:
        title = _row_name(row)
        if row.get("site"):
            title += f" — {row['site']}"
        lines += [f"### {title}", "", dossier(row, months), ""]
    lines += [
        "---",
        "",
        f"Последний прогон: {meta['run_id']}; сводка собрана "
        f"{meta['generated_at']}. Сырьё (датированные JSON-снапшоты по "
        f"объектам): `{meta['snapshot_root']}`.",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Рендер html (артефакт-страница)
# ---------------------------------------------------------------------------

_CSS = """
:root {
  --bg: #F7F6F1; --surface: #FFFFFF; --ink: #232720; --muted: #6C7263;
  --accent: #3E6B4A; --line: #E1E2D8;
  --ok-bg: #E9F0E6; --ok-ink: #2F5A3C;
  --warn-bg: #F5EBD6; --warn-ink: #7C5514;
  --bad-bg: #F3E3DE; --bad-ink: #8A4335;
  /* Единственная графика страницы — календарная лента. Рампа занятости
     приглушена на тон против редакции 14.08 (фидбек «передушил с
     графикой»): страница — документ с одной иллюстрацией, а не пульт.
     Монотонность L и шаг ΔL >= 0.06 сохранены (валидатор dataviz). */
  --occ1: #D5E0CD; --occ2: #B7C6B0; --occ3: #8FA588; --occ4: #647A5F;
  /* «нет данных» держится тёплым нейтральным (без зелени): приглушённая
     рампа подошла к нему вплотную, и первая ступень занятости читалась
     как пустая клетка. */
  --cellring: #A9B2A2; --nodata: #EAE8DF; --wkband: #E7EADB;
  /* штриховка «продажи не открыты»: отличима формой, но тише данных */
  --hatch-bg: #F7F1E2; --hatch-ink: #CBB588;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #191B16; --surface: #21241D; --ink: #E6E7DD; --muted: #9BA18F;
    --accent: #8AB795; --line: #343830;
    --ok-bg: #28351F; --ok-ink: #A9CDA0;
    --warn-bg: #3A3220; --warn-ink: #DDB36A;
    --bad-bg: #3B2823; --bad-ink: #D69382;
    --occ1: #313D30; --occ2: #45503F; --occ3: #63715B; --occ4: #8B9B82;
    --cellring: #5F6A5B; --nodata: #232520; --wkband: #262A22;
    --hatch-bg: #2F2C1F; --hatch-ink: #8A7444;
  }
}
:root[data-theme="dark"] {
  --bg: #191B16; --surface: #21241D; --ink: #E6E7DD; --muted: #9BA18F;
  --accent: #8AB795; --line: #343830;
  --ok-bg: #28351F; --ok-ink: #A9CDA0;
  --warn-bg: #3A3220; --warn-ink: #DDB36A;
  --bad-bg: #3B2823; --bad-ink: #D69382;
  --occ1: #313D30; --occ2: #45503F; --occ3: #63715B; --occ4: #8B9B82;
  --cellring: #5F6A5B; --nodata: #232520; --wkband: #262A22;
  --hatch-bg: #2F2C1F; --hatch-ink: #8A7444;
}
body {
  background: var(--bg); color: var(--ink);
  font: 16px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  margin: 0; padding: 2.5rem 1.25rem 4rem;
}
/* Широкая колонка нужна графике: календарная лента объекта в горизонте
   до конца октября — 1112px, при 62rem она резалась на любой ширине окна
   и каждая из 17 карточек уезжала в собственный горизонтальный скролл.
   Текстовые блоки остаются узкими (max-width 46rem) — читаемость строки. */
main { max-width: 74rem; margin: 0 auto; }
h1, h2, h3 { font-family: Georgia, "Times New Roman", serif; line-height: 1.25;
             text-wrap: balance; }
h1 { font-size: 1.9rem; margin: 0 0 .4rem; }
h2 { font-size: 1.35rem; margin: 2.5rem 0 .8rem; }
h3 { font-size: 1.02rem; margin: 0 0 .15rem; }
h3 a { color: var(--accent); font-weight: normal; font-size: .85rem;
       text-decoration: underline; text-decoration-thickness: 1px;
       text-underline-offset: .15em; }
a:focus-visible, .strip-wrap:focus-visible, .table-wrap:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 2px; }
.lede { color: var(--muted); max-width: 46rem; margin: 0 0 1rem; }
.note { color: var(--muted); font-size: .88rem; max-width: 46rem;
        margin: 0 0 1rem; }
/* Разбор клетки и пометок источника — сноской ПОД таблицей: между шапкой и
   цифрами читатель не должен продираться через абзац (фидбек 14.08). */
.tablenote { font-size: .82rem; margin: .7rem 0 0; }
.method { display: flex; flex-direction: column; gap: .7rem; }
.method p { max-width: 46rem; margin: 0; }
/* Проверки честности — плотный нумерованный список: содержание оператор
   заказывал целиком, но занимать полстраницы отступами оно не должно. */
ol.checks { max-width: 46rem; margin: 0; padding-left: 1.4rem;
            font-size: .92rem; }
ol.checks li { margin: 0 0 .45rem; padding-left: .1rem; }
ol.checks li::marker { color: var(--muted); }
.table-wrap { --scrollbg: var(--surface); overflow-x: auto;
              border: 1px solid var(--line); border-radius: 8px; }
/* Лента живёт прямо на фоне страницы — без карточки и рамки: клетки сами
   по себе рисунок, коробка вокруг них была лишней декорацией. */
.strip-wrap { --scrollbg: var(--bg); overflow-x: auto; }
/* Подсказка «есть что прокрутить»: тень у края видна, только пока за краем
   что-то осталось (слои-крышки привязаны к контенту, тени — к рамке). */
.table-wrap, .strip-wrap {
  background:
    linear-gradient(to right, var(--scrollbg) 45%, transparent)
      left center / 2.5rem 100% no-repeat local,
    linear-gradient(to left, var(--scrollbg) 45%, transparent)
      right center / 2.5rem 100% no-repeat local,
    radial-gradient(farthest-side at 0 50%, var(--cellring), transparent)
      left center / .7rem 100% no-repeat scroll,
    radial-gradient(farthest-side at 100% 50%, var(--cellring), transparent)
      right center / .7rem 100% no-repeat scroll,
    var(--scrollbg); }
table { border-collapse: collapse; width: 100%; min-width: 56rem;
        font-size: .88rem; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: .55rem .7rem; vertical-align: top;
         border-top: 1px solid var(--line); }
thead th { border-top: none; color: var(--muted); font-weight: 600;
           font-size: .78rem; text-transform: uppercase;
           letter-spacing: .04em; white-space: nowrap; }
/* Имя объекта не уезжает при боковой прокрутке таблицы (узкие экраны):
   без него на 360px строка теряет подпись и цифры не к чему отнести.
   Границы — тенями: при border-collapse рамки прилипшей ячейки остаются
   на месте и рвут сетку. */
td.obj, thead th:first-child { position: sticky; left: 0; z-index: 1;
         background: var(--surface); box-shadow: inset 0 1px 0 var(--line),
         1px 0 0 var(--line); }
thead th:first-child { box-shadow: 1px 0 0 var(--line); }
td.obj { font-weight: 600; white-space: nowrap; }
/* Расшифровка среза переносится ТОЛЬКО между смысловыми кусками: без
   этого узкая колонка рвала их по слову («продажи / не / открыты»). */
td .cut { display: block; color: var(--muted); font-size: .8rem; }
.np { white-space: nowrap; }
td.dyn, td.when { color: var(--muted); }
td.when { white-space: nowrap; }
/* Колонка источника несёт длинную причину — ей нужна ширина, иначе
   свободное место забирают короткие колонки процентов. */
td.src, thead th:last-child { min-width: 15rem; }
td.src .srcnote { display: block; color: var(--muted); font-size: .78rem;
                  margin-top: .2rem; }
/* Радиус — под однострочную пилюлю; на длинной подписи «лоза» с круглыми
   торцами резала текст, поэтому многострочная становится скруглённым
   прямоугольником. Длинная причина живёт отдельной строкой .srcnote. */
.pill { display: inline-block; padding: .1rem .5rem; border-radius: .7rem;
        font-size: .78rem; white-space: normal; }
.pill.module { background: var(--ok-bg); color: var(--ok-ink); }
.pill.quota { background: var(--warn-bg); color: var(--warn-ink); }
.pill.none { background: var(--bad-bg); color: var(--bad-ink); }
/* Роль объекта — не оценка: референс не «хорошо» и не «плохо», поэтому
   нейтральные токены, а не зелёный/жёлтый/красный тона источника. */
.pill.ref { background: var(--line); color: var(--muted); margin-left: .4rem; }
.dossiers { display: flex; flex-direction: column; gap: 1rem; }
.dossier p { max-width: 46rem; margin: 0; }
.dossier .site { color: var(--muted); font-size: .85rem; }
footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--line);
         color: var(--muted); font-size: .85rem; }
footer code { font-size: .8rem; overflow-wrap: anywhere; }
/* --- единственная графика: легенда состояний (одна строка на страницу) --- */
.legend { display: flex; flex-wrap: wrap; gap: .3rem 1rem;
          align-items: center; font-size: .8rem; color: var(--muted);
          margin: 0 0 .9rem; }
.legend .lg { display: inline-flex; align-items: center; gap: .3rem; }
.legend .ramp { display: inline-flex; gap: 2px; }
/* box-sizing — чтобы плашка с рамкой не была крупнее плашек без рамки;
   рамка на «нет данных» и «выходных» обязательна: без неё эти два тона
   на фоне страницы не видно вовсе (светлая тема). */
.sw { display: inline-block; box-sizing: border-box; width: .9rem;
      height: .9rem; border-radius: 2px; flex: none; }
.sw.r1 { background: var(--occ1); } .sw.r2 { background: var(--occ2); }
.sw.r3 { background: var(--occ3); } .sw.r4 { background: var(--occ4); }
.sw.free { background: var(--surface); border: 1px solid var(--cellring); }
.sw.nd { background: var(--nodata); border: 1px solid var(--line); }
.sw.wk { background: var(--wkband); border: 1px solid var(--line); }
/* --- единственная графика: лента объекта (без карточки и рамок) --- */
.cal { display: flex; flex-direction: column; gap: 1.15rem; }
.objhead { display: flex; flex-wrap: wrap; gap: .2rem .6rem;
           align-items: baseline; margin: 0 0 .25rem; }
.objhead .objname { font-weight: 600; }
.strip { display: block; max-width: none; }
.strip text { font: 600 10px system-ui, -apple-system, "Segoe UI", Roboto,
              sans-serif; fill: var(--muted); }
.strip text.dlab { font-weight: 400; font-size: 9px; }
.strip .wkband { fill: var(--wkband); }
.strip .msep { stroke: var(--cellring); stroke-width: 1; }
.strip .c-free { fill: var(--surface); stroke: var(--cellring);
                 stroke-width: 1; }
/* «Нет данных» — тихая клетка, но с видимой границей: без неё в тёмной
   теме лента объекта без данных читается как пустое сломанное место.
   Контур вполсилы — тише свободной ночи, у той он полный. */
.strip .c-nd { fill: var(--nodata); stroke: var(--cellring);
               stroke-opacity: .45; stroke-width: 1; }
.strip .c-sno { fill: url(#hatch-sno); }
.strip .c-b1 { fill: var(--occ1); } .strip .c-b2 { fill: var(--occ2); }
.strip .c-b3 { fill: var(--occ3); } .strip .c-b4 { fill: var(--occ4); }
.objhead .srcnote { color: var(--muted); font-size: .78rem;
                    flex-basis: 100%; margin: 0; }
/* Печать: контейнеры не режут ленту (на бумаге прокрутить нельзя),
   блоки объектов и строки не рвутся между страницами. */
@media print {
  body { background: #fff; color: #000; padding: 0; }
  main { max-width: none; }
  .strip-wrap, .table-wrap { overflow: visible; background: none; }
  .strip { width: 100%; height: auto; }
  table { min-width: 0; font-size: .7rem; }
  td.src, thead th:last-child { min-width: 0; }
  td.obj, thead th:first-child { position: static; box-shadow: none; }
  td.obj { white-space: normal; }
  th, td { padding: .3rem .4rem; }
  .np { white-space: normal; }
  .calobj, .dossier, tr, ol.checks li { break-inside: avoid; }
  h2, h3 { break-after: avoid; }
}
"""


def _month_cell_html(m: dict, sample=None, inventory_gap: bool = False) -> str:
    cuts = m["cuts"]
    if cuts["all"]["known"] == 0:
        if m["sales_not_open"]:
            return '<span class="cut np">продажи не открыты</span>'
        return '<span class="cut np">нет данных</span>'
    text = f"<strong>{cuts['all']['pct']:.0f}%</strong>"
    detail = []
    if cuts["weekday"]["pct"] is not None:
        detail.append(f"будни {cuts['weekday']['pct']:.0f}%")
    if cuts["weekend"]["pct"] is not None:
        detail.append(f"вых {cuts['weekend']['pct']:.0f}%")
    note = _sample_note(sample)
    if note:
        detail.append(note)
    if inventory_gap:
        detail.append("фонд не снимался")
    if m["sales_not_open"]:
        tail = "часть дней не в продаже"  # длинная фраза, ей перенос можно
    else:
        tail = ""
    if detail or tail:
        # Короткие куски неразрывны: перенос допустим только между ними.
        parts = "".join(
            f'<span class="np">{escape(part)}'
            f'{"," if i < len(detail) - 1 or tail else ""}</span> '
            for i, part in enumerate(detail))
        text += f'<span class="cut">{(parts + escape(tail)).rstrip()}</span>'
    return text


def _pill(row: dict) -> str:
    """Пилюля источника: короткая «шапка» подписи, длинная причина — отдельной
    приглушённой строкой. Причина внутри пилюли превращала её в многострочную
    «лозу», круглые торцы которой резали текст (ревью 14.08)."""
    head, _, tail = row["source_label"].partition(" — ")
    if row["status"] in ("insufficient_data", "missing"):
        kind = "none"
    elif row["quota"]:
        kind = "quota"
    else:
        kind = "module"
    pill = f'<span class="pill {kind}">{escape(head)}</span>'
    if tail:
        pill += f'<span class="srcnote">{escape(tail)}</span>'
    return pill


def _ref_badge(row: dict) -> str:
    """Бейдж роли объекта в html. Класс намеренно НЕ chip/tile/bar: их
    отсутствие на странице держат тесты редакции 14.08 («передушил с
    графикой»), и бейдж роли — не повод их будить."""
    return (f'<span class="pill ref">{REFERENCE_MARK}</span>'
            if row.get("reference") else "")


# ---------------------------------------------------------------------------
# Единственная графика html (редакция 14.08 по фидбеку «передушил с
# графикой»): календарная лента объекта. Один факт — один способ показа:
# проценты живут в таблице, лента показывает то, чего в таблице нет, —
# распределение свободных ночей по датам. Сводные плитки, месячные столбики
# будни/выходные и цветные дельта-чипы сняты: плитки считали величину,
# которую никто не заказывал, столбики дублировали колонки таблицы, а
# динамика читается словами в её колонке «Динамика».
# Всё инлайн (SVG + CSS-токены страницы), внешних библиотек нет — CSP
# артефактов запрещает. Правила — скилл dataviz: занятость это sequential-
# рампа одного тона, «продажи не открыты» и «нет данных» отличимы формой
# (штриховка 45° / тихая клетка), не только цветом; легенда одна на
# страницу; широкая графика скроллится в своём контейнере.
# ---------------------------------------------------------------------------

# Геометрия ленты: клетка (ширина/высота/зазор 2px — surface gap по dataviz),
# полосы подписей месяцев сверху и номеров дней снизу.
_CW, _CH, _CGAP = 12, 22, 2
_TOP, _BOT, _PAD = 16, 13, 4

# Инлайновый defs со штриховкой 45° для «продажи не открыты» (не только цвет).
SVG_DEFS = (
    '<svg width="0" height="0" style="position:absolute" aria-hidden="true">'
    '<defs><pattern id="hatch-sno" width="6" height="6"'
    ' patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
    '<rect width="6" height="6" fill="var(--hatch-bg)"/>'
    '<line x1="1" y1="0" x2="1" y2="6" stroke="var(--hatch-ink)"'
    ' stroke-width="1.1"/></pattern></defs></svg>')


def _data_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["status"] in ("ok", "partial")]


def _strip_bounds(data_rows, months):
    """Общий диапазон лент: от самой ранней проверенной даты до конца
    последнего месяца горизонта. Общий — чтобы месяцы совпадали по
    вертикали и дыры сравнивались между объектами одним взглядом."""
    y0, m0 = (int(x) for x in months[0].split("-"))
    y1, m1 = (int(x) for x in months[-1].split("-"))
    lo = date(y0, m0, 1)
    hi = date(y1, m1, calendar.monthrange(y1, m1)[1])
    starts = []
    for row in data_rows:
        for cells in (row.get("units") or {}).values():
            for d in cells:
                try:
                    day = date.fromisoformat(d)
                except ValueError:
                    continue
                if lo <= day <= hi:
                    starts.append(day)
    if not starts:
        return None
    return min(starts), hi


def _day_cell(units: dict, iso: str):
    """(busy, known, sales_not_open) по всем юнитам на дату iso.

    Считаем домико-ночи тем же ядром, что и проценты (core.cell_units):
    иначе лента красила бы тип из трёх домиков как один и расходилась бы
    с цифрой в таблице.
    """
    busy = known = sno = 0
    for cells in units.values():
        cell = cells.get(iso)
        state = cell.get("state") if isinstance(cell, dict) else None
        if state in ("free", "busy"):
            total, cell_busy = core.cell_units(cell)
            known += total
            busy += cell_busy
        elif state == "sales_not_open":
            sno += 1
    return busy, known, sno


def _strip_svg(row: dict, start: date, end: date) -> str:
    """Календарная лента объекта: клетка = ночь, заливка = доля занятых
    юнитов (4 ступени рампы), тултип-title на каждой клетке."""
    days = (end - start).days + 1
    pitch = _CW + _CGAP
    width = _PAD * 2 + days * pitch - _CGAP
    height = _TOP + _CH + _BOT
    units = row.get("units") or {}
    bands = []
    cells = []
    seps = []
    labels = []
    run_start = None  # начало текущего блока выходных (пт-вс), индекс дня
    for i in range(days + 1):
        d = start + timedelta(days=i)
        weekend = i < days and core.day_class(d) == "weekend"
        if weekend and run_start is None:
            run_start = i
        if not weekend and run_start is not None:
            x = _PAD + run_start * pitch
            w = (i - run_start) * pitch - _CGAP
            bands.append(f'<rect class="wkband" x="{x}" y="{_TOP - 3}"'
                         f' width="{w}" height="{_CH + 6}"/>')
            run_start = None
        if i == days:
            break
        x = _PAD + i * pitch
        if d.day == 1 and i > 0:
            # Линия идёт по зазору между клетками и рисуется ПОВЕРХ них
            # (иначе клетки закрашивают её и стык месяцев не виден).
            seps.append(f'<line class="msep" x1="{x - 1}" y1="1"'
                        f' x2="{x - 1}" y2="{height - 11}"/>')
        if i == 0 or d.day == 1:
            name = MONTH_FULL_RU[d.month]
            # Подпись последнего месяца не должна вылезать за viewBox.
            lx = min(x + 1, max(_PAD, width - _PAD - len(name) * 6))
            labels.append(f'<text x="{lx}" y="12">{escape(name)}</text>')
        if d.weekday() == 0:  # номер дня по понедельникам — ориентир
            labels.append(f'<text class="dlab" x="{x + _CW / 2:g}"'
                          f' y="{height - 3}" text-anchor="middle">'
                          f'{d.day}</text>')
        busy, known, sno = _day_cell(units, d.isoformat())
        dd = d.strftime("%d.%m")
        if known and busy:
            share = busy / known
            b = 1 + min(3, int(share * 4 - 1e-9))
            cls, tip = f"c-b{b}", f"{dd} — занято {busy} из {known}"
        elif known:
            cls, tip = "c-free", f"{dd} — свободно (занято 0 из {known})"
        elif sno:
            cls, tip = "c-sno", f"{dd} — продажи не открыты"
        else:
            cls, tip = "c-nd", f"{dd} — нет данных"
        cells.append(f'<rect class="{cls}" x="{x}" y="{_TOP}" width="{_CW}"'
                     f' height="{_CH}" rx="2"><title>{escape(tip)}</title>'
                     f'</rect>')
    return (f'<svg class="strip" viewBox="0 0 {width} {height}"'
            f' width="{width}" height="{height}" role="img"'
            f' aria-label="Календарь занятости: {escape(row["username"])}">'
            + "".join(bands + labels + cells + seps) + "</svg>")


def _legend_html() -> str:
    """Одна легенда на страницу, одной компактной строкой над лентами:
    только состояния клетки и пометка выходных — больше в ленте ничего
    закодировано и нет."""
    ramp = "".join(
        f'<span class="sw r{i}" title="занято {t} юнитов"></span>'
        for i, t in ((1, "до 25%"), (2, "26–50%"), (3, "51–75%"),
                     (4, "76–100%")))
    return (
        '<div class="legend">'
        f'<span class="lg"><span class="ramp">{ramp}</span>'
        ' занято — темнее значит больше домиков</span>'
        '<span class="lg"><span class="sw free"></span> свободно</span>'
        '<span class="lg"><svg class="sw" viewBox="0 0 14 14" width="14"'
        ' height="14" aria-hidden="true"><rect width="14" height="14" rx="2"'
        ' fill="url(#hatch-sno)"/></svg> продажи не открыты</span>'
        '<span class="lg"><span class="sw nd"></span> нет данных</span>'
        '<span class="lg"><span class="sw wk"></span> выходные'
        ' (пт–вс)</span>'
        '</div>')


def _calendar_html(rows: list[dict], months: list[str]) -> str:
    """Раздел «Календарь занятости»: на объект с данными — имя, пометка
    источника и одна лента. Ни карточек, ни рамок, ни столбиков: проценты
    стоят в таблице выше, лента отвечает только за распределение дыр."""
    data_rows = _data_rows(rows)
    bounds = _strip_bounds(data_rows, months)
    if bounds is None:
        return ""
    start, end = bounds
    title = (f"Календарь занятости до {end.day} "
             f"{MONTH_GEN_RU[end.month]} {end.year}")
    note = ("Клетка — одна ночь начиная с даты снимка. Чем темнее заливка, "
            "тем больше домиков занято в эту ночь; светлые клетки с "
            "контуром — свободные ночи, то есть дыры в загрузке. Подсказка "
            "на клетке показывает дату и счёт занятых домиков.")
    blocks = []
    for row in data_rows:
        # tabindex — прокручиваемая область должна доставаться с клавиатуры;
        # значения клеток продублированы таблицей выше.
        blocks.append(
            f'<section class="calobj">'
            f'<div class="objhead"><span class="objname">'
            f'{escape(row["username"])}</span>{_ref_badge(row)}{_pill(row)}'
            f'</div>'
            f'<div class="strip-wrap" tabindex="0" role="group"'
            f' aria-label="Лента занятости: {escape(row["username"])}">'
            f'{_strip_svg(row, start, end)}</div>'
            f'</section>')
    return (f'<h2>{escape(title)}</h2>\n<p class="note">{escape(note)}</p>\n'
            f'{_legend_html()}\n<div class="cal">{"".join(blocks)}</div>')


def _dynamics_html(row: dict) -> str:
    """Динамика в таблице — обычный текст (цветные чипы сняты 14.08), но с
    типографским минусом: находка визуального аудита переживает правку."""
    return escape(_dynamics_cell(row)).replace("-", "−")


def _coverage_line(rows: list[dict]) -> str:
    """Покрытие целей одной строкой шапки — вместо плитки-агрегата.

    Считаются только КАНДИДАТЫ: референс — не цель продаж, и в знаменателе
    «снято N из M целей списка» он тихо завышал бы масштаб работы. Эту строку
    руководитель читает первой.
    """
    candidates = [r for r in rows if not r.get("reference")]
    covered = sum(1 for r in candidates if r["status"] != "missing")
    return (f"Снято {covered} из {len(candidates)} целей списка; там, где "
            f"данные снять не удалось, в таблице стоит честная строка "
            f"«нет данных» с причиной.")


def render_html(rows: list[dict], months: list[str], meta: dict) -> str:
    month_headers = "".join(
        f"<th>{core.MONTH_LABELS_RU[int(m.split('-')[1])]} "
        f"{m.split('-')[0]}</th>" for m in months)
    body_rows = []
    for row in rows:
        if row["metrics"] is None or row["status"] == "insufficient_data":
            cells = "".join('<td><span class="cut">—</span></td>'
                            for _ in months)
        else:
            cells = "".join(
                f"<td>{_month_cell_html(_metric(row, m), (row.get('sample') or {}).get(m), (row.get('inventory_gap') or {}).get(m, False))}</td>"
                for m in months)
        body_rows.append(
            f'<tr><td class="obj">{escape(row["username"])}'
            f'{_ref_badge(row)}</td>{cells}'
            f'<td class="dyn">{_dynamics_html(row)}</td>'
            f'<td class="when">{escape(_fmt_checked(row["checked_at"]))}</td>'
            f'<td class="src">{_pill(row)}</td></tr>')
    dossiers = []
    for row in rows:
        site = ""
        if row.get("site"):
            url = escape(row["site"], quote=True)
            site = f' <a href="{url}">{escape(row["site"])}</a>'
        dossiers.append(
            f'<div class="dossier"><h3>{escape(row["username"])}'
            f'{_ref_badge(row)}{site}</h3>'
            f"<p>{escape(dossier_short(row, months))}</p></div>")
    method = "".join(f"<p>{escape(p)}</p>" for p in HOW_IT_WORKS)
    checks = "".join(f"<li>{escape(c)}</li>" for c in TRUST_CHECKS)
    # Порядок разделов (правка 14.08, вечер — указание оператора): методика и
    # проверки честности стоят ПЕРЕД цифрами. Читатель отчёта — руководитель,
    # который инструмент видит впервые: сначала механизм и основания доверять,
    # потом сами числа. Прежняя редакция («данные раньше методики») отменена.
    return f"""<meta charset="utf-8">
<title>Загрузка глэмпингов</title>
<style>{_CSS}</style>
<main>
{SVG_DEFS}
<h1>Загрузка глэмпингов</h1>
<p class="lede">{escape(HTML_LEDE)} {escape(_coverage_line(rows))}</p>
<h2>{escape(HOW_IT_WORKS_TITLE)}</h2>
<div class="method">{method}</div>
<h2>{escape(TRUST_TITLE)}</h2>
<p class="note">{escape(TRUST_DATE_NOTE)}</p>
<ol class="checks">{checks}</ol>
<h2>{escape(TABLE_TITLE)}</h2>
<div class="table-wrap" tabindex="0" role="region"
 aria-label="Таблица загрузки по объектам"><table>
<thead><tr><th>Объект</th>{month_headers}<th>Динамика</th><th>Снят</th>
<th>Источник</th></tr></thead>
<tbody>{"".join(body_rows)}</tbody>
</table></div>
<p class="note tablenote">{escape(TABLE_NOTE)} {escape(SOURCE_NOTE)}</p>
{_calendar_html(rows, months)}
<h2>Досье по объектам</h2>
<div class="dossiers">{"".join(dossiers)}</div>
<footer>Последний прогон: {escape(meta["run_id"])}; сводка собрана
{escape(_fmt_generated(meta["generated_at"]))}. Сырьё — датированные
JSON-снапшоты: <code>{escape(meta["snapshot_root"])}</code>.</footer>
</main>
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_summary",
        description="Собрать человеческую сводку загрузки из снапшотов "
                    "(md + html для артефакта).")
    parser.add_argument("--snapshot-root",
                        default=str(core.DEFAULT_SNAPSHOT_ROOT))
    parser.add_argument("--recipes", default=str(core.DEFAULT_RECIPES))
    parser.add_argument("--targets", default=str(core.DEFAULT_TARGETS))
    parser.add_argument("--months",
                        default=",".join(core.summary_months(core.today())),
                        help="месяцы сводки через запятую, YYYY-MM "
                             "(по умолчанию — скользящее окно от текущего "
                             "месяца)")
    parser.add_argument("--out", default=str(core.DEFAULT_OUT_MD),
                        help="куда писать markdown-сводку")
    parser.add_argument("--out-html", default=str(core.DEFAULT_OUT_HTML),
                        help="куда писать html для артефакта")
    args = parser.parse_args(argv)
    months = [m.strip() for m in args.months.split(",") if m.strip()]
    try:
        targets = core.load_targets(args.targets)
        recipes = core.load_recipes(args.recipes)
        runs = core.list_snapshots(args.snapshot_root)
        if not runs:
            print(f"ошибка: в {args.snapshot_root} нет ни одного снапшота — "
                  f"сначала прогон (cli.py --probe/--fixture)", file=sys.stderr)
            return 2
        history = load_history(args.snapshot_root, depth=HISTORY_DEPTH)
    except (core.RegistryError, core.SnapshotError) as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 2
    meta = {
        "run_id": runs[-1],
        "generated_at": datetime.now().astimezone().isoformat(
            timespec="seconds"),
        "snapshot_root": "agent-runtime/research/glamping/occupancy/snapshots/",
    }
    rows = build_rows(targets, recipes, history, months)
    out_md = Path(args.out)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_markdown(rows, months, meta), encoding="utf-8")
    out_html = Path(args.out_html)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(render_html(rows, months, meta), encoding="utf-8")
    # Считаем ТЕМИ ЖЕ целями, что и шапка артефакта (_coverage_line): иначе
    # один прогон печатает в консоль «целей 25», а на странице стоит
    # «Снято 24 из 24 целей списка», и одно из двух выглядит багом.
    candidates = [r for r in rows if not r.get("reference")]
    refs = len(rows) - len(candidates)
    with_data = sum(1 for r in candidates if r["status"] in DATA_STATUSES)
    no_data = sum(1 for r in candidates
                  if r["status"] in ("insufficient_data", "missing"))
    print(f"сводка: {out_md}")
    print(f"html артефакта: {out_html}")
    print(f"целей {len(candidates)}: с данными {with_data}, без данных "
          f"{no_data}" + (f"; референсов {refs}" if refs else "")
          + f"; последний прогон {meta['run_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
