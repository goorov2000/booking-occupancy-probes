# -*- coding: utf-8 -*-
"""Пробник «Бронируй Онлайн» (bronirui-online): рецепт -> сетка по номерам.

Разведка 14.08.2026 (тикет 04, чистый HTTP — браузер не понадобился): виджет
znms (widget.bronirui-online.ru/js/app.js -> widget/init.js ->
assets/init.chunk-*.js, Vue3) ходит POST-ами с JSON-телом в
https://api.bronirui-online.ru/v2. Ключ, о который споткнулась волна 2:
поля тела — snake_case (module_id), а не camelCase moduleId; и календарь
без number_id конкретного номера отдаёт ВСЕ ночи isClosed (профиль модуля
vlesu_glamping это подтвердил живьём) — поэтому агрегатного съёма у движка
нет, пробник работает только по номерам из params.numbers рецепта.

Эндпоинты (все POST, Origin/Referer сайта обязательны — домен проверяется
по списку доверенных):
- widget/calendar {module_id, widget_type: "booking-rooms", number_id,
  dateFrom/dateTo = YYYY-MM-DD, adult_count, ...} -> {"dates": {"elements":
  {"YYYY-MM-DD": {"isClosed": bool, "price": num|null, ...},
  "firstAvailableDate": ...}}. isClosed=true — ночь не продаётся
  (занята/закрыта владельцем — неотличимо, ОЦЕНКА СВЕРХУ); isClosed=false —
  свободна, price — цена за ночь. Запрошенный диапазон отдаётся целиком
  (прошедшие даты отсекаются сервером); окно продаж глубокое — vlesu_glamping
  14.08 отдавал живые цены вплоть до июня 2027, стены «всё закрыто» не видно,
  поэтому sales_not_open этим пробником не ставится.
- hotel-info {index, module_id, widget_type} — профиль модуля; выключенный
  отелем модуль отвечает 401 «Отель временно не принимает бронирования
  онлайн» (живой пример — shale_aframe/7130), это broken с текстом отеля.
- numbers {module_id, date_from/date_to (snake!), ...} — доступные на даты
  номера; разведчику (тикет 05): полный справочник номеров для
  params.numbers собирается СОЮЗОМ выдач на 2-3 пары дат в разных месяцах
  (метод тикета 03), module_id — в HTML сайта: znmsWidget.init({moduleId}).
  Поле rooms_count записи — ФОНД номера (сколько домиков продаётся под одним
  number_id, живая разведка 04.09 видела 3): его надо сохранять в рецепт,
  иначе про многодомиковый номер не известно даже этого. Форма записи
  справочника в рецепте — либо старая {"id": "имя"}, либо
  {"id": {"name": ..., "rooms_count": N}}; читаются обе (см. directory()).

ПОШТУЧНОГО ОСТАТКА ДВИЖОК НЕ ОТДАЁТ (ревью волны 4, 04.09.2026). Календарь
отвечает про номер одним булевым isClosed — «продаётся ли под этим номером
хоть что-то», — а не «сколько из трёх свободно». Живой ответ (фикстура
tests/fixtures/bronirui/widget_calendar_vlesu_aframe15.json, снята 14.08)
несёт по ночи только date/minNight/minNightArrival/maxNight/price/isClosed
(+isClosedOnArrival/isClosedOnDeparture): числа свободных домиков среди полей
нет. Поэтому пара units_total/units_free пишется ТОЛЬКО при rooms_count=1 —
там бинарный ответ движка и ЕСТЬ остаток по домику; при фонде больше единицы
клетка идёт без пары, объект считается по типам (core.unit_basis -> "type"
или "mixed"), а фонд из справочника называется в причине объекта. Иначе
снимок обещал бы человеку поштучный счёт, которого у нас нет, — ровно то
занижение/завышение, против которого писался тикет 08.

Правила ошибок — единый каскад (транспорт probes/_common.py + правила
исхода probes/_outcome.py): смена схемы ответа, 4xx (включая 401
выключенного модуля) и не-JSON тело при коде 200 -> broken с причиной
(переразведка); сетевой сбой и 5xx с любым телом -> unknown-клетки и
partial/insufficient_data, рецепт жив. Один запрос — одна попытка, пауза >= 1.2 c
между запросами к хосту (общий трекер _common), ничего не бронируется
(order/* не зовётся никогда). Глубина (ревью 14.08): dateTo расширяется
минимум до 2027-03 — тот же набор запросов, снапшот несёт весь горизонт.
"""
from __future__ import annotations

from datetime import date
from typing import Callable, Optional

from ._common import (AccessRefused, SchemaChanged,
                      _base_object as _common_base, _iso_dates,
                      deep_date_to, make_fetch as _make_fetch)
# Каскад и сборка объекта идут через правила ИСХОДА (probes/_outcome.py):
# 5xx рецепт не ломает никогда, а отказ хоста поверх уже снятой сетки не
# заводит счётчик суток. Транспорт про судьбу рецепта не знает.
from ._outcome import classify_response, finish as _finish

WIDGET_TYPE = "booking-rooms"

# Версия РАЗБОРА движка (поле probe_version снапшота). 2 — правка
# 04.09.2026: units_total клетки берётся из rooms_count справочника
# (тикет 04). До неё каждый номер весил один домик, и снимки с одинаковой
# подписью значили бы разное. 3 — ревью волны 4 того же дня: фонд больше
# единицы перестал становиться парой units_total/units_free, потому что
# поштучного остатка календарь не отдаёт. Ряд сезонности живёт годами, и
# ночи, снятые версией 2, весят другое — версию надо уметь различить.
PROBE_VERSION = 3


def _base_object(username: str, recipe: dict, granularity: str) -> dict:
    return _common_base(username, recipe, granularity,
                        default_engine="bronirui")


def _body_text(fetch) -> str:
    """Тело последнего ответа fetch — им опознаётся страница-заслон.

    Импорт ленивый: пакет probes грузит этот модуль сам, и импорт на уровне
    модуля замкнул бы круг. Реализация одна на все движки — probes.body_text_of.
    """
    from . import body_text_of
    return body_text_of(fetch)


def directory(numbers: dict, params: Optional[dict] = None) -> tuple:
    """params.numbers -> ({number_id: имя}, {number_id: фонд}, [id без фонда]).

    Справочник рецепта живёт в двух формах, обе читаются (тикет 04):
      "10043": "A-frame дом"                       — старая, фонд неизвестен;
      "10043": {"name": ..., "rooms_count": 3}     — с фондом из /v2/numbers.
    Фонд можно положить и отдельной картой params.rooms_count — так рецепт
    правится руками, не переписывая справочник целиком.

    rooms_count — это ЧИСЛО ДОМИКОВ под одним number_id, а не остаток: живая
    разведка 04.09 видела rooms_count=3 у одного идентификатора. Остаток
    поштучно API не отдаёт вовсе (см. докстринг модуля), поэтому фондом
    клетки становится ТОЛЬКО единица: под таким номером один домик, и
    бинарный ответ календаря — это и есть его остаток. Фонд больше единицы
    здесь тоже возвращается, но идёт не в клетку, а в причину объекта:
    читателю надо знать, что за номером стоят три домика и цифра занижена.

    Поля нет или оно не целое (bool в том числе) -> фонда НЕТ, и номер
    попадает в третий список. Заглушки «один домик» здесь не будет: она
    делала снимок неотличимым от объекта со снятым фондом, и сводка
    подписывала строку «по домикам: 6 в 6 типах» ровно там, где движок про
    фонд не сказал ничего (ревью волны 3, вопрос заказчика «сколько домиков, а
    не типов»). Клетка без пары units_total/units_free считается по-старому
    (один домик, бинарно), но говорит об этом вслух: core.unit_basis видит
    "type", и человек читает «по типам (фонд не снят)».
    """
    extra = (params or {}).get("rooms_count") or {}
    names: dict[str, str] = {}
    capacity: dict[str, int] = {}
    unknown: list[str] = []
    for number_id, value in numbers.items():
        key = str(number_id)
        if isinstance(value, dict):
            names[key] = str(value.get("name") or value.get("title")
                             or f"Номер {key}")
            rooms = value.get("rooms_count")
        else:
            names[key] = str(value)
            rooms = None
        if rooms is None:
            rooms = extra.get(key, extra.get(number_id))
        if isinstance(rooms, int) and not isinstance(rooms, bool) and rooms >= 1:
            capacity[key] = rooms
        else:
            unknown.append(key)
    counts: dict[str, int] = {}
    for name in names.values():
        counts[name] = counts.get(name, 0) + 1
    # Дубли имён различаем number_id («A-frame дом [731]»): у одного объекта
    # легко стоят три одинаковых домика, а ключ сетки обязан быть уникальным.
    unique = {key: (name if counts[name] == 1 else f"{name} [{key}]")
              for key, name in names.items()}
    return unique, capacity, unknown


def parse_calendar(payload: object, dates: list[str],
                   rooms_count: Optional[int] = None) -> dict:
    """Ответ widget/calendar -> клетки {дата: {"state": ..., "price"?: ...}}.

    isClosed=true -> busy (оценка сверху), isClosed=false -> free (+цена),
    даты вне dates.elements -> unknown. Не та форма -> SchemaChanged.

    rooms_count — фонд номера из справочника (тикет 04). Пара
    units_total/units_free ставится клетке ТОЛЬКО при rooms_count=1: под
    таким номером один домик, и «продаётся ли хоть что-то» про него — это и
    есть остаток (свободен один из одного, занят ноль из одного).

    Фонд больше единицы парой НЕ становится (ревью волны 4): календарь
    отвечает про номер одним булевым isClosed и не говорит, сколько из трёх
    домиков занято, — а units_free=3 у свободной ночи выдавало бы это
    незнание за поштучный счёт. Клетка идёт без пары и весит один домик,
    как ночь без фонда: цифра занижена (номер считается занятым, только
    когда продан последний домик), зато названа своим именем — core.
    unit_basis видит "type", и человек читает «по типам».

    None (справочник фонда не отдал) — клетка тоже идёт БЕЗ пары: снимок не
    выдаёт догадку «один домик» за снятый фонд (ревью волны 3).
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("dates"), dict):
        raise SchemaChanged(
            "в ответе нет объекта dates — схема widget/calendar сменилась")
    elements = payload["dates"].get("elements")
    if not isinstance(elements, dict):
        raise SchemaChanged(
            "dates.elements не словарь дата->ночь — схема сменилась")
    # Фонд клетки — только доказанный: один домик под номером.
    one_home = rooms_count == 1
    cells = {}
    for d in dates:
        el = elements.get(d)
        if el is None:
            cells[d] = {"state": "unknown"}
            continue
        if not isinstance(el, dict) or not isinstance(el.get("isClosed"), bool):
            raise SchemaChanged(
                f"клетка elements[{d}] без булева isClosed — схема сменилась")
        if el["isClosed"]:
            cells[d] = {"state": "busy"}
            if one_home:
                cells[d].update(units_total=1, units_free=0)
        else:
            cells[d] = {"state": "free"}
            if one_home:
                cells[d].update(units_total=1, units_free=1)
            price = el.get("price")
            if isinstance(price, (int, float)):
                cells[d]["price"] = price
    return cells


def probe(username: str, recipe: dict, date_from: date, date_to: date,
          fetch: Optional[Callable] = None, inventory: bool = True,
          inventory_date_to: Optional[date] = None):
    """Снять сетку по рецепту bronirui-online. -> (obj, broken_reason | None).

    inventory и inventory_date_to принимаются для единообразия диспетчера и
    запросов не добавляют: number_id движка — это адрес номера, и его фонд
    приходит справочником, а не запросом на ночь. Поэтому фонд известен на
    ВСЮ сетку, и inventory_until = её конец (тикет 06).
    """
    if fetch is None:
        fetch = _make_fetch("post_json")
    request = recipe.get("request", {})
    params = request.get("params", {})
    headers = request.get("headers", {})
    url = request.get("url_template", "")
    numbers = params.get("numbers") or {}
    if not numbers:
        obj = _base_object(username, recipe, "aggregate")
        obj["status"] = "insufficient_data"
        obj["reason"] = ("в рецепте нет params.numbers — календарь без "
                         "number_id отдаёт все ночи закрытыми (проверка "
                         "14.08), нужна доразведка номеров")
        return obj, None

    # Глубина бесплатна: тот же набор POST-ов на любой диапазон, окно продаж
    # глубокое (живые цены vlesu до июня 2027) — dateTo расширяется минимум
    # до 2027-03 (ревью 14.08), глубина живёт в снапшоте, не в сводке.
    deep_to = deep_date_to(date_to)
    dates = _iso_dates(date_from, deep_to)
    obj = _base_object(username, recipe, "per_unit")
    # Фонд у этого движка приходит справочником номеров, а не запросом на
    # ночь, поэтому он известен на всю глубину сетки (тикет 06).
    obj["inventory_until"] = deep_to.isoformat()
    obj["grid_until"] = deep_to.isoformat()
    failures: list[str] = []
    broken: Optional[str] = None
    refused: Optional[str] = None
    refusal_status: Optional[int] = None
    # Имена номеров у движка НЕ уникальны: «в одном глэмпинге несколько домов
    # одного типа и названия» (вопрос заказчика 15.08) — три одинаковых A-frame
    # приходят тремя number_id с одним именем. До правки такой номер молча
    # затирал предыдущий в obj["units"], и занятый домик пропадал из сетки
    # вместе с запросом, который за него уже заплатили. Различаем номером,
    # как travelline различает категории кодом: суффикс стабилен между
    # прогонами, поэтому дифф не разъезжается.
    unit_names, capacity, without_capacity = directory(numbers, params)
    for number_id, unit_name in unit_names.items():
        payload = {
            "module_id": params.get("module_id"),
            "widget_type": WIDGET_TYPE,
            "number_id": int(number_id) if str(number_id).isdigit() else number_id,
            "dateFrom": date_from.isoformat(),
            "dateTo": deep_to.isoformat(),
            # виджет всегда шлёт состав гостей; дефолт виджета — 2 взрослых
            "adult_count": 2,
            "child_count": 0,
            "child_count_by_ages": [],
            "is_checkin": True,
            "is_checkout": False,
            "tariff_id": None,
        }
        try:
            status, data = fetch(url, headers, payload)
        except AccessRefused as e:
            # Хост нас не пускает — остальные номера того же хоста не идут.
            refused, refusal_status = e.reason, e.status
            obj["units"][unit_name] = {d: {"state": "unknown"} for d in dates}
            break
        except OSError as e:
            failures.append(f"{unit_name}: сетевой сбой ({e})")
            obj["units"][unit_name] = {d: {"state": "unknown"} for d in dates}
            continue
        obj["source_urls"].append(f"{url}#number_id={number_id}")
        detail = (data["message"]
                  if isinstance(data, dict) and data.get("message") else "")
        verdict = classify_response(status, data is not None, unit_name,
                                    detail=detail,
                                    body_text=_body_text(fetch))
        if verdict.kind == "refused":
            refused, refusal_status = verdict.reason, status
            obj["units"][unit_name] = {d: {"state": "unknown"} for d in dates}
            break
        if verdict.kind == "broken":
            broken = verdict.reason
            break
        if verdict.kind == "network":
            failures.append(verdict.reason)
            obj["units"][unit_name] = {d: {"state": "unknown"} for d in dates}
            continue
        try:
            obj["units"][unit_name] = parse_calendar(
                data, dates, capacity.get(number_id))
        except SchemaChanged as e:
            broken = f"{unit_name}: {e}"
            break
    result = _finish(obj, failures, broken, refused=refused,
                     refusal_status=refusal_status)
    if not broken:
        # Две пометки, обе — не сбой: цифры честные, но занижены, и читатель
        # должен знать, чем именно. В failures их класть нельзя — объект
        # стал бы partial каждый день без единой поломки.
        for note in (_note_without_fund(without_capacity),
                     _note_multi_home(capacity, unit_names)):
            if note:
                result[0]["reason"] = "; ".join(
                    x for x in (result[0].get("reason"), note) if x)
    return result


def _shortlist(keys) -> str:
    """Первые пять идентификаторов через запятую плюс «и ещё N»."""
    keys = sorted(keys)
    head = ", ".join(keys[:5])
    return head + (f" и ещё {len(keys) - 5}" if len(keys) > 5 else "")


def _note_without_fund(without_capacity: list) -> str:
    """Номера, про фонд которых справочник не сказал ничего."""
    if not without_capacity:
        return ""
    return (f"считано по номерам без фонда (rooms_count не отдан "
            f"справочником): {_shortlist(without_capacity)}")


def _note_multi_home(capacity: dict, unit_names: dict) -> str:
    """Номера с несколькими домиками: поштучного остатка движок не отдаёт.

    Пометка появилась в волне 4 вместо пары units_total/units_free, которую
    такие номера получали раньше: календарь отвечает «продаётся ли хоть
    что-то», и выдавать это за поштучный счёт нельзя. Фонд из справочника не
    пропадает — он назван здесь, чтобы человек видел размер занижения.
    """
    multi = {key: fund for key, fund in capacity.items() if fund > 1}
    if not multi:
        return ""
    listed = ", ".join(f"{unit_names.get(key, key)} [{key}] ×{multi[key]}"
                       for key in sorted(multi)[:5])
    tail = f" и ещё {len(multi) - 5}" if len(multi) > 5 else ""
    return (f"поштучного остатка движок не отдаёт: под номерами {listed}"
            f"{tail} стоит несколько домиков, а календарь отвечает только "
            f"«продаётся ли хоть один» — эти клетки считаны по типу, "
            f"занятость занижена")
