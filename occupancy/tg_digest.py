# -*- coding: utf-8 -*-
"""Короткая сводка объекта в телеграм после планового прогона.

Зачем отдельный модуль: транспорт у завода общий (cf.notify.notify_telegram),
а ТЕКСТ у слежки свой — человеку в чат нужны не те же слова, что в таблице.
Здесь чистые функции без сети: собрать текст из объекта снапшота и его
предшественника. Отправку делает run_scheduled.py.

Кого уведомлять — поле "notify" цели в targets.json (SCHEMA (в) ядра):
    true | "daily"   — сообщение каждый прогон, даже когда ничего не сдвинулось
                       (молчание и «всё стоит» в чате выглядят одинаково);
    "changes"        — только когда календарь двигался или снимок неполный.
Отсутствие поля = не уведомлять.

Сообщение сознательно короткое: месяцы, разрез будни/выходные ближайшего
месяца, фонд, движение за сутки. Ссылку на артефакт НЕ кладём — страница
обновляется прогоном агента, а не таймером, и дата на ней разошлась бы с
датой в сообщении.
"""
from __future__ import annotations

from datetime import date

import build_summary  # только словарь падежей месяцев, без рендера
import occupancy_core as core

DAILY = ("daily", True)
ON_CHANGE = ("changes", "change")


def wants_notify(target: dict) -> str:
    """Режим уведомления цели: "daily" | "changes" | "" (не уведомлять)."""
    mode = (target or {}).get("notify")
    if mode in DAILY:
        return "daily"
    if mode in ON_CHANGE:
        return "changes"
    return ""


def title(target: dict, obj: dict) -> str:
    """Человеческое имя объекта: title цели, иначе username."""
    return str((target or {}).get("title")
               or (obj or {}).get("username") or "объект")


def _pct(value) -> str:
    return "нет данных" if value is None else f"{value:.0f}%"


def moved(diff: dict) -> bool:
    """Двигался ли календарь между снимками (а не только округление)."""
    if not diff:
        return False
    if diff.get("newly_busy") or diff.get("newly_sold"):
        return True
    return any(abs(s["delta_pp"]) >= 0.1
               for s in diff["months"].values()
               if s.get("delta_pp") is not None)


def _months_line(obj: dict, months: list[str]) -> str:
    parts = []
    for m in core.aggregate(obj["units"], months):
        parts.append(f"{m['label'].split()[0]} {_pct(m['cuts']['all']['pct'])}")
    return " · ".join(parts)


def _split_line(obj: dict, months: list[str]) -> str:
    """Будни против выходных по первому месяцу, где есть данные.

    Разрез стоит отдельной строкой не для красоты: у загородного объекта
    решают уик-энды, и общий процент месяца их прячет.
    """
    for m in core.aggregate(obj["units"], months):
        wd, we = m["cuts"]["weekday"]["pct"], m["cuts"]["weekend"]["pct"]
        if wd is None and we is None:
            continue
        gen = build_summary.MONTH_GEN_RU[int(m["month"].split("-")[1])]
        return f"Выходные {gen}: {_pct(we)}, будни: {_pct(wd)}"
    return ""


def _fund_line(obj: dict) -> str:
    basis = core.unit_basis(obj["units"])
    homes, types = basis.get("homes"), basis.get("units_total")
    if not homes or not types:
        return ""
    if basis.get("basis") != "unit":
        return f"Фонд: не меньше {homes} домиков в {types} категориях"
    return f"Фонд: {homes} домиков в {types} категориях"


def _period(gap_days) -> str:
    """Период между снимками словами: «за сутки» | «за 3 дня» | «с прошлого
    снимка», когда даты снимков неизвестны.

    Прямая речь про срок нужна потому, что сообщение уходит и по целям,
    которые таймер ПРОПУСКАЕТ (агрегатор, неразведанный рецепт): у них
    предыдущий снимок может быть трёхнедельным, и «за сутки» — выдумка.
    """
    if gap_days is None:
        return "с прошлого снимка"
    if gap_days <= 1:
        return "за сутки"
    return "за " + _plural(int(gap_days), "день", "дня", "дней")


def _capitalized(text: str) -> str:
    # Не .capitalize(): он ЗАГЛУШАЕТ остальную строку в нижний регистр.
    return text[0].upper() + text[1:] if text else text


def _change_line(diff: dict, gap_days=None) -> str:
    head = _capitalized(_period(gap_days))
    if diff is None:
        return "Это первый снимок — сравнивать не с чем."
    if not moved(diff):
        return f"{head} календарь не двигался."
    parts = []
    for month, shift in diff["months"].items():
        delta = shift.get("delta_pp")
        if delta is None or abs(delta) < 0.1:
            continue
        label = core.MONTH_LABELS_RU[int(month.split("-")[1])]
        # Запятая, а не точка: человеку в чат, а не в лог.
        parts.append(f"{label} {delta:+.1f}".replace(".", ",") + " пп")
    line = f"{head}: " + (", ".join(parts) if parts
                          else "проценты почти не сдвинулись")
    nights = len(diff.get("newly_busy") or [])
    sold = len(diff.get("newly_sold") or [])
    tail = []
    if nights:
        tail.append(f"занятых ночей стало больше на {nights}")
    if sold:
        tail.append(f"внутри типов допродали домиков: {sold}")
    if tail:
        # Не .capitalize(): он ЗАГЛУШАЕТ остальную строку в нижний регистр,
        # и имена категорий в хвосте поехали бы.
        line += ". " + _capitalized("; ".join(tail))
    return line + "."


def digest(target: dict, obj: dict, prev: dict, months: list[str],
           today: date, *, taken: date = None, gap_days=None) -> str:
    """Текст сообщения по объекту. Пустая строка = отправлять нечего.

    taken — день, которым снимок ДЕЙСТВИТЕЛЬНО снят (дата каталога прогона),
    gap_days — сколько суток между ним и предыдущим снимком. Без них
    сообщение датировалось днём прогона и любую дельту звало суточной: цель,
    которую таймер пропускает (агрегатор, неразведанный рецепт), каждый день
    слала владельцу трёхнедельную цифру под сегодняшней датой. Умолчание
    (None) значит «снимок сегодняшний, период неизвестен» — так зовут те, у
    кого другого снимка и не бывает.
    """
    mode = wants_notify(target)
    if not mode or not obj:
        return ""
    name = title(target, obj)
    head = f"{name} — снимок {(taken or today):%d.%m}"
    if taken and taken != today:
        # Возраст данных — половина смысла цифры: сводка о нём говорит
        # честно (колонка «Снят»), сообщение в чат обязано тоже.
        head += " (сегодня не снимался)"
    if obj.get("status") == "insufficient_data" or not obj.get("units"):
        # Молчать об этом нельзя: пропавший объект — единственная новость,
        # которая требует человека (переразведка), и она же самая тихая.
        return (f"{head}\n\nДанные снять не удалось: "
                f"{obj.get('reason') or 'причина не записана'}")
    diff = core.diff_units(obj["units"], prev["units"], months) if prev else None
    if mode == "changes" and diff is not None and not moved(diff) \
            and obj.get("status") == "ok":
        return ""
    lines = [head, "", "Занято: " + _months_line(obj, months)]
    split = _split_line(obj, months)
    if split:
        lines.append(split)
    fund = _fund_line(obj)
    if fund:
        lines.append(fund)
    lines += ["", _change_line(diff, gap_days)]
    if obj.get("status") == "partial" and obj.get("reason"):
        lines += ["", f"Снимок неполный: {obj['reason']}"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Итог прогона одним сообщением (тикет 16)
# ---------------------------------------------------------------------------

EMPTY_ROWS_SHOWN = 12
# Предел одной строки причины. Причина собирается пробником из групп отказов
# (probes/_common.one_line(limit=300) × collapse_failures) и доходит до
# двух килобайт: три такие строки — и сообщение упирается в предел Bot API,
# который режет ХВОСТ. Режем каждую причину сами и в известном месте.
REASON_LIMIT = 160


def _share(value) -> str:
    return f"{(value or 0.0) * 100:.0f}%"


def _plural(count: int, one: str, few: str, many: str) -> str:
    """Число со словом в правильном падеже: сообщение читает человек."""
    tail, tens = count % 10, count % 100
    if tail == 1 and tens != 11:
        word = one
    elif tail in (2, 3, 4) and tens not in (12, 13, 14):
        word = few
    else:
        word = many
    return f"{count} {word}"


def run_summary(report: dict, today: date) -> str:
    """Итог планового прогона ОДНИМ сообщением. Пустая строка — писать нечего.

    Раньше в чат уходило по сообщению на цель. Пока целей было три, это
    читалось; на 53 целях это шторм, в котором тонет единственная новость,
    требующая человека, — объекты, с которых данные не сняты. Поэтому итог
    один, а отдельные сообщения остаются только у целей с notify.

    Читает готовый отчёт (run_scheduled.run_report), а не снапшот: кто считает
    долю пустых, тот и решает код возврата, и считать её дважды нельзя — цифра
    в чате обязана совпасть с той, по которой сработал алерт.
    """
    if not report:
        return ""
    total = report.get("total") or 0
    head = f"Загрузка глэмпингов — прогон {today:%d.%m}"
    alert = report.get("alert") or ""
    lines = [head]
    if alert:
        # Алерт СРАЗУ после шапки, а не в конце. Транспорт режет сообщение по
        # пределу Bot API с хвоста, и при длинных причинах первой пропадала
        # ровно та строка, ради которой сообщение и городилось.
        lines += ["", f"Внимание: {alert}"]
    if not total:
        # Молчать нельзя: пустой снапшот — это отказ всего прогона, а не тишина.
        if not alert:
            lines += ["", "Снапшот прогона не прочитан — снятых целей в нём "
                          "нет. Нужен агент."]
    else:
        empty = report.get("empty") or 0
        lines += ["",
                  f"Снято {_plural(total, 'цель', 'цели', 'целей')}: "
                  f"полностью {report.get('ok') or 0}, "
                  f"частично {report.get('partial') or 0}, "
                  f"без данных {empty} ({_share(report.get('empty_share'))})."]
    skipped = report.get("skipped") or []
    if skipped:
        lines.append(f"Пропущено до пробника: {len(skipped)} — "
                     f"{_skip_breakdown(report)}.")
    late = report.get("late") or []
    if late:
        lines.append(f"Не дошла очередь до дедлайна: {len(late)}.")
    rows = report.get("empty_rows") or []
    if rows:
        lines += ["", "Без данных:"]
        for username, reason in rows[:EMPTY_ROWS_SHOWN]:
            lines.append(f"· {username} — "
                         f"{_short(reason) or 'причина не записана'}")
        if len(rows) > EMPTY_ROWS_SHOWN:
            # Хвост режем: предел Bot API 4096 знаков, а при отказе хоста
            # без данных остаются ВСЕ цели разом.
            lines.append(f"· и ещё {len(rows) - EMPTY_ROWS_SHOWN}")
    return "\n".join(lines)


#: Имена групп пропуска для чата. Держатся В ОДНОМ месте с прогоном
#: (run_scheduled.SKIP_LABELS) — импортировать оттуда нельзя, tg_digest
#: обязан оставаться чистым текстом без зависимости от прогона, поэтому
#: расхождение ловит тест test_skip_labels_match_the_run.
SKIP_LABELS = {
    "scout": "ждут разведки",
    "aggregator": "агрегаторы (ветка агента)",
    "no_module": "без онлайн-канала",
}
SKIP_ORDER = ("scout", "aggregator", "no_module")


def _skip_breakdown(report: dict) -> str:
    """Пропущенные ПО ГРУППАМ: где работа агента, а где вывод уже сделан.

    «Ждут разведки» печатается ВСЕГДА, даже нулём: это единственное число
    строки, которое требует человека, и «ждут разведки: 0» — новость, ради
    которой стоит потратить пять знаков. Остальные группы показываются
    только непустыми — нулевые агрегаторы в чате лишний шум.
    """
    groups = report.get("skipped_groups") or {}
    parts = []
    for name in SKIP_ORDER:
        count = int(groups.get(name) or 0)
        if count or name == "scout":
            parts.append(f"{SKIP_LABELS[name]} {count}")
    return ", ".join(parts) if parts else "разбор не записан"


def _short(reason) -> str:
    """Причина в одну обозримую строку: подробности — в снапшоте и в сводке."""
    reason = " ".join(str(reason or "").split())
    if len(reason) <= REASON_LIMIT:
        return reason
    return reason[:REASON_LIMIT - 1].rstrip() + "…"
