# -*- coding: utf-8 -*-
"""Пробник HomeReserve (движок realtycalendar): рецепт -> сетка по домикам.

Разведка 15.08.2026 (чистый HTTP, без браузера). Сайт объекта грузит
homereserve.ru/widget.js и зовёт window.homereserve.initWidgetSearch({token}),
а сам виджет ходит на ОДНУ базу — https://realtycalendar.ru/v2/widget/{token}/
— двумя POST-эндпоинтами с телом JSON и без авторизации:

    POST .../apartments {begin_date, end_date, guests, apartment_ids, page}
        -> {"apartments": [{id, title, min_stay, price, ...}, ...]}
    POST .../calendar   {begin_date, end_date, guests, apartment_id}
        -> {"calendar": [{date, available, price, min_stay,
                          closed_on_arrival, closed_on_departure}, ...]}

Это снимает прежний вердикт волны 2 «пустая SPA-оболочка, нужен браузер»:
данные отдаются обычным запросом, браузер не нужен ни разу.

Единица продажи здесь — КОНКРЕТНЫЙ ДОМИК (apartment), а не тип размещения,
поэтому фонд типов снимать нечем и не нужно: клетка всегда весит ровно один
домик, и объект считается поштучно по определению. Флаг inventory принимается
ради единообразия диспетчера и запросов не делает.

Семантика клетки, как и у остальных движков, — ОЦЕНКА СВЕРХУ: available=false
у движка означает «заезд в эту ночь начать нельзя», и туда попадают не только
проданные ночи, но и закрытые владельцем, и ночи короче минимального срока
(у обоих живых объектов min_stay = 2). Поэтому одиночная свободная ночь между
двумя бронями считается занятой — недоучёт дыр, а не выдумка загрузки.

Глубина бесплатна: календарь отдаёт ВЕСЬ запрошенный диапазон одним ответом
(проверка 15.08: 15.08.2026-31.03.2027 = 229 дней в одном ответе), поэтому
горизонт углубляется до общего DEEP_DATE_TO — сводка показывает свои месяцы,
остальное живёт в снапшоте.

Правила ошибок — единый каскад (транспорт probes/_common.py + правила
исхода probes/_outcome.py): 4xx или тело не той формы -> broken с причиной
(переразведка); сетевой сбой и 5xx с любым телом -> unknown-клетки, рецепт
жив. Один запрос — одна попытка, пауза >= 1.2 c между запросами к
хосту, ничего не бронируется и никакие формы не отправляются: оба эндпоинта
только читают.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Optional

from ._common import (AccessRefused, SchemaChanged,
                      _base_object as _common_base, _iso_dates,
                      deep_date_to, make_fetch as _make_fetch)
# Каскад и сборка объекта идут через правила ИСХОДА (probes/_outcome.py):
# 5xx рецепт не ломает никогда, а отказ хоста поверх уже снятой сетки не
# заводит счётчик суток. Транспорт про судьбу рецепта не знает.
from ._outcome import classify_response, finish as _finish

BASE = "https://realtycalendar.ru/v2/widget/{token}"
GUESTS = {"adults": 2, "children": []}
# Виджет просит по 30 домиков на страницу (q=30 в бандле homereserve.ru).
PAGE_SIZE = 30
MAX_PAGES = 10

_BROKEN_HINT = "похоже на смену API HomeReserve или антибот"

# Версия РАЗБОРА движка (поле probe_version снапшота). 2 — правка
# 04.09.2026: 403/429 читаются как отказ хоста и рецепт не ломают
# (тикет 03).
PROBE_VERSION = 2


def _body_text(fetch) -> str:
    """Тело последнего ответа fetch — им опознаётся страница-заслон.

    Импорт ленивый: пакет probes грузит этот модуль сам, и импорт на уровне
    модуля замкнул бы круг. Реализация одна на все движки — probes.body_text_of.
    """
    from . import body_text_of
    return body_text_of(fetch)


def parse_apartments(payload: object) -> list[tuple[str, str]]:
    """Ответ apartments -> [(id домика, имя), ...] в порядке выдачи.

    Имя берём из title; пустое имя — не поломка (у части объектов там адрес
    или пусто), домик получает подпись по id.
    """
    if not isinstance(payload, dict) or not isinstance(
            payload.get("apartments"), list):
        raise SchemaChanged(
            "в ответе нет списка apartments — схема HomeReserve сменилась")
    out: list[tuple[str, str]] = []
    for item in payload["apartments"]:
        if not isinstance(item, dict) or item.get("id") in (None, ""):
            raise SchemaChanged("запись apartments без id домика")
        apartment_id = str(item["id"])
        title = str(item.get("title") or "").strip()
        out.append((apartment_id, title or f"Домик {apartment_id}"))
    return out


def parse_calendar(payload: object, dates: list[str]) -> dict:
    """Ответ calendar -> клетки {дата: {"state", "price"?, фонд}}.

    available=true -> free (+цена, если движок её дал), false -> busy
    (оценка сверху, см. докстринг модуля). Даты, которых в ответе нет,
    остаются unknown и в знаменатель занятости не идут.
    """
    if not isinstance(payload, dict) or not isinstance(
            payload.get("calendar"), list):
        raise SchemaChanged(
            "в ответе нет списка calendar — схема HomeReserve сменилась")
    by_date: dict[str, dict] = {}
    for day in payload["calendar"]:
        if not isinstance(day, dict) or not day.get("date"):
            raise SchemaChanged("запись calendar без даты")
        available = day.get("available")
        if not isinstance(available, bool):
            raise SchemaChanged(
                f"calendar[{day['date']}].available не булево ({available!r})")
        cell = {"state": "free" if available else "busy",
                "units_total": 1, "units_free": 1 if available else 0}
        price = day.get("price")
        if available and isinstance(price, (int, float)) \
                and not isinstance(price, bool):
            cell["price"] = price
        by_date[str(day["date"])] = cell
    return {d: by_date.get(d, {"state": "unknown"}) for d in dates}


def _unit_names(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """[(id, имя)] -> {id: уникальное имя юнита} (дубли различаем id)."""
    seen: dict[str, int] = {}
    for _, name in pairs:
        seen[name] = seen.get(name, 0) + 1
    used: set[str] = set()
    names: dict[str, str] = {}
    for apartment_id, name in pairs:
        unit = name if seen[name] == 1 else f"{name} [{apartment_id}]"
        while unit in used:  # один и тот же id дважды в выдаче — не наша беда
            unit = f"{unit} [{apartment_id}]"
        used.add(unit)
        names[apartment_id] = unit
    return names


def _collect_apartments(base: str, headers: dict, fetch: Callable,
                        date_from: date, obj: dict) -> tuple[list, Optional[str],
                                                             Optional[str]]:
    """Справочник домиков постранично. -> (пары, broken | None, сбой | None).

    Отказ хоста (403/429/заслон) наружу уходит исключением AccessRefused:
    рецепт им не ломается, и решение принимает вызывающий.
    """
    url = f"{base}/apartments"
    payload_dates = {
        "begin_date": date_from.isoformat(),
        "end_date": (date_from + timedelta(days=1)).isoformat(),
    }
    pairs: list[tuple[str, str]] = []
    for page in range(1, MAX_PAGES + 1):
        payload = {**payload_dates, "guests": GUESTS, "apartment_ids": [],
                   "page": page}
        try:
            status, data = fetch(url, headers, payload)
        except AccessRefused:
            raise
        except OSError as e:
            return pairs, None, f"справочник домиков: сетевой сбой ({e})"
        if page == 1:
            obj["source_urls"].append(url)
        verdict = classify_response(status, data is not None,
                                    "справочник домиков", hint=_BROKEN_HINT,
                                    body_text=_body_text(fetch))
        if verdict.kind == "refused":
            raise AccessRefused(verdict.reason, status=status,
                                retry_after=verdict.retry_after)
        if verdict.kind == "broken":
            return pairs, verdict.reason, None
        if verdict.kind == "network":
            return pairs, None, verdict.reason
        try:
            page_pairs = parse_apartments(data)
        except SchemaChanged as e:
            return pairs, str(e), None
        pairs.extend(page_pairs)
        if len(page_pairs) < PAGE_SIZE:
            break
    return pairs, None, None


def probe(username: str, recipe: dict, date_from: date, date_to: date,
          fetch: Optional[Callable] = None, inventory: bool = True,
          inventory_date_to: Optional[date] = None):
    """Снять сетку по рецепту HomeReserve. -> (obj, broken_reason | None).

    inventory и inventory_date_to принимаются для единообразия диспетчера и
    запросов не добавляют: apartment движка — это уже конкретный домик,
    фонд клетки всегда 1 и известен на всю сетку (тикет 06).
    """
    if fetch is None:
        fetch = _make_fetch("post_json")
    request = recipe.get("request", {})
    params = request.get("params", {})
    headers = request.get("headers", {})
    obj = _base_object(username, recipe)
    token = params.get("token", "")
    if not token:
        return _finish(obj, [], "в рецепте нет params.token — нечем звать "
                                "виджет HomeReserve")
    base = BASE.format(token=token)

    try:
        pairs, broken, net_fail = _collect_apartments(base, headers, fetch,
                                                      date_from, obj)
    except AccessRefused as e:
        # Справочник — первый запрос объекта: не пустили здесь, не пустят и
        # в календарь. Рецепт жив, сутки отказа считает CLI.
        return _finish(obj, [], None, refused=e.reason,
                       refusal_status=e.status)
    if broken:
        return _finish(obj, [], broken)
    failures: list[str] = []
    if net_fail:
        failures.append(net_fail)
    wanted = [str(x) for x in (params.get("apartment_ids") or [])]
    if wanted:
        # Рецепт называет домики явно (их список разведчик снял с сайта) —
        # берём ровно их: у токена управляющей компании в выдаче бывают
        # чужие объекты. Домик из рецепта, пропавший из выдачи, — не молчим.
        known = dict(pairs)
        missing = [x for x in wanted if x not in known]
        if missing:
            failures.append("домиков из рецепта нет в выдаче движка: "
                            + ", ".join(missing))
        pairs = [(x, known[x]) for x in wanted if x in known]
    if not pairs:
        # Пустой справочник ломает рецепт ТОЛЬКО когда движок и правда
        # ответил: обрыв связи или 5xx на этом шаге — повод повторить
        # прогон, а не звать агента (ревью волны 3). Рецепт, помеченный
        # broken, в следующие прогоны не идёт вовсе, пока его не
        # переразведают руками, — а переразведка сети не чинит.
        return _finish(obj, failures, None if net_fail else
                       "движок не отдал ни одного домика — снимать нечего")

    refused: Optional[str] = None
    refusal_status: Optional[int] = None
    names = _unit_names(pairs)
    deep_to = deep_date_to(date_to)
    dates = _iso_dates(date_from, deep_to)
    # apartment — физический домик: фонд клетки известен без отдельного
    # запроса, то есть на всю глубину сетки (тикет 06).
    obj["inventory_until"] = deep_to.isoformat()
    obj["grid_until"] = deep_to.isoformat()
    url = f"{base}/calendar"
    obj["source_urls"].append(url)
    for apartment_id, _ in pairs:
        unit = names[apartment_id]
        payload = {"begin_date": date_from.isoformat(),
                   "end_date": deep_to.isoformat(),
                   "guests": GUESTS, "apartment_id": int(apartment_id)
                   if apartment_id.isdigit() else apartment_id}
        try:
            status, data = fetch(url, headers, payload)
        except AccessRefused as e:
            refused, refusal_status = e.reason, e.status
            obj["units"][unit] = {d: {"state": "unknown"} for d in dates}
            break
        except OSError as e:
            failures.append(f"{unit}: сетевой сбой ({e})")
            obj["units"][unit] = {d: {"state": "unknown"} for d in dates}
            continue
        verdict = classify_response(status, data is not None, unit,
                                    hint=_BROKEN_HINT,
                                    body_text=_body_text(fetch))
        if verdict.kind == "refused":
            # Клетки остаются unknown: отказ хоста — это «мы не знаем», а не
            # «всё занято». Остальные домики того же хоста не спрашиваем.
            refused, refusal_status = verdict.reason, status
            obj["units"][unit] = {d: {"state": "unknown"} for d in dates}
            break
        if verdict.kind == "broken":
            return _finish(obj, failures, verdict.reason)
        if verdict.kind == "network":
            failures.append(verdict.reason)
            obj["units"][unit] = {d: {"state": "unknown"} for d in dates}
            continue
        try:
            obj["units"][unit] = parse_calendar(data, dates)
        except SchemaChanged as e:
            return _finish(obj, failures, f"{unit}: {e}")
    return _finish(obj, failures, None, refused=refused,
                   refusal_status=refusal_status)


def _base_object(username: str, recipe: dict) -> dict:
    return _common_base(username, recipe, "per_unit",
                        default_engine="homereserve")
