# -*- coding: utf-8 -*-
"""Пробник Frontdesk24 (PMS, виджет pms.frontdesk24.ru/onlineWidget): рецепт
-> сетка по категориям с фондом.

Разведка 08.09.2026 (отель «Гора», Карелия, gora-hotel.ru/book/; чистый HTTP,
без браузера). Сайт грузит pms.frontdesk24.ru/onlineWidget/bookWidget.js и
зовёт FD24BookWidget.createWidget("book-widget", "<token>") — токен лежит в
Bitrix-кэше страницы, а не в HTML (page_*.js). Виджет — iframe
onlineWidget/full.html?token=…, данные берёт jQuery-ом (common/dataAccess.js)
POST-ами с JSON-телом из https://pms.frontdesk24.ru/api/ (common/global.js:
Global.api_root). Из его методов пробнику нужны три читающих:

    POST online/getHotelParams {token, language}
        -> {"data": [[параметры отеля], [валюты], [языки], [удобства],
                     [тарифы], [], [категории {id, name}], [{hotelName,
                     token}]]}
        Справочник категорий (data[6]) и ПРИЗНАК ЖИВОГО ТОКЕНА (data[7]):
        по чужому токену движок отвечает не ошибкой, а тем же каркасом с
        пустыми data[6] и data[7] (живой ответ 08.09 — фикстура
        hotel_params_bad_token.json). Без этого запроса мёртвый токен был бы
        неотличим от «продаж нет» — см. getAvailableDates ниже.
    POST online/getAvailableDates {token, language, dateFrom, dateTo,
                                   currency}
        -> {"data": [{roomCategoryID, date, price}, ...]}
        КАЛЕНДАРЬ: запись есть только у ночи, которую категория ПРОДАЁТ
        (в виджете по ней рисуется цена дня, full.js: getAvailablePrice).
        Нет записи — ночь не продаётся: занята или закрыта владельцем,
        неотличимо, ОЦЕНКА СВЕРХУ как у соседей. Диапазон принимается любой,
        но отвечает движок тем дольше, чем он длиннее (366 суток — 44 с,
        см. CALENDAR_WINDOW_DAYS), поэтому год спрашивается окнами. По
        чужому токену — {"data": []}: ровно то же, что «ни одной открытой
        ночи».
    POST online/getVariants {token, language, currency, dateFrom, dateTo,
                             rooms: [{adults}], onlyRostourismProgram}
        -> {"data": [[категории {id, name, availableRooms, ...}], [опции],
                     [тарифы], [опции тарифов], [картинки], [варианты]]}
        ПОДБОР на заезд: availableRooms категории — сколько её номеров
        доступно на ВЕСЬ запрошенный отрезок; категории, которую под этот
        отрезок продавать нельзя, в data[0] нет вовсе (виджет такой же
        фильтр держит в DataFormatter.getVariantsTree). Это ФОНД: один POST
        на ночь горизонта фонда, окно в одну ночь.
    putOrder — оформление брони, не зовётся никогда (запреты SKILL.md).

Что проверено живьём 08.09 на «Горе» (фикстуры tests/fixtures/frontdesk24/):
- adults=1 и adults=2 дают одну выдачу; окно 1 ночь и 2 ночи — одну и ту же
  (и в будни 15.09, и в субботу 19.09), то есть минимального срока у
  объекта нет и однночного окна хватает. У объекта С минимумом подбор
  на 1 ночь категорию не покажет, а календарь ночь покажет — клетка тогда
  остаётся бинарной без пары (правило TravelLine, apply_inventory ниже),
  фонд не выдумывается.
- Origin/Referer сайта API не требует (ответ без них тот же), но в рецепт
  Referer кладётся как у всех — WAF может появиться.
- Ошибка параметра приходит кодом 200 с телом {"error": {code, message}}
  (фикстура error_bad_date.json), чужой HTTP-метод — 405 с {"Message"}.
  Тело с "error" — broken с текстом движка, как у виджета (_onSuccess).
- Окно продаж: на 08.09 записи календаря кончаются 29.12.2026 у ВСЕХ пяти
  категорий разом, дальше до горизонта пусто. Хвост за последней ночью,
  которую движок продаёт хоть в одной категории, размечается
  sales_not_open: 40 номеров «Стандарта», проданных на девять месяцев
  вперёд, — это не аншлаг, а закрытые продажи. Границу окна движок сам не
  называет, поэтому она читается по календарю целиком, а не по одной
  категории: категория, чья последняя открытая ночь раньше общей границы,
  до неё считается занятой (оценка сверху).

Фонд категории = максимум наблюдённого остатка за горизонт фонда — оценка
СНИЗУ, как у UHotels и TravelLine: категория, ни разу не бывшая свободной,
фонда не получает, весит один домик и названа в reason. У «Горы» три
хаусбота — три категории с availableRooms=1, то есть по борту на
категорию; «Стандарт» показывал 34–40.

Справочник юнитов: имена берутся из params.room_types рецепта (там же
разведчик помечает береговые номера «(берег)», как у bereg_ladogi), а
движок — источник ПОЛНОТЫ: категория, которой в рецепте нет, всё равно
снимается под именем движка и называется в reason (иначе новый домик
пропадал бы из сетки молча); категория рецепта, которой у движка больше
нет, идёт unknown-клетками и тоже называется (её ночи без записей в
календаре читались бы как 100% занятости несуществующего номера).

Для цели, соответствующей одному судну, params.include_room_types явно
ограничивает выдачу списком ID категорий. Без него снимается весь комплекс.
Пропавший выбранный ID остаётся unknown, а общая граница продаж по-прежнему
вычисляется по полному календарю отеля, чтобы не спутать занятость судна с
закрытием продаж комплекса.

Правила ошибок — единый каскад (транспорт probes/_common.py + правила исхода
probes/_outcome.py): 4xx кроме 403/429, не-JSON тело при 200, тело с
"error", смена схемы -> broken (переразведка); сеть и 5xx -> unknown-клетки
и partial/insufficient_data, рецепт жив; 403/429/заслон -> refusal, рецепт
жив. На поночном шаге фонда одиночный 4xx рецепт НЕ ломает (45 запросов
подряд к одному хосту — как у соседей): ночь остаётся бинарной, сбой
уходит в причину. Один запрос — одна попытка, пауза >= 1.2 с к хосту,
ничего не бронируется.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Optional

from ._common import (AccessRefused, SchemaChanged,
                      _base_object as _common_base, _iso_dates,
                      deep_date_to, make_fetch as _make_fetch, one_line)
# Каскад и сборка объекта — через правила ИСХОДА (probes/_outcome.py):
# 5xx рецепт не ломает никогда, отказ хоста поверх снятой сетки не заводит
# счётчик суток. Транспорт про судьбу рецепта не знает.
from ._outcome import classify_response, finish as _finish

METHOD_PARAMS = "getHotelParams"
METHOD_DATES = "getAvailableDates"
METHOD_VARIANTS = "getVariants"

# Состав гостей минимальный: больший отрезал бы малые категории по
# вместимости, и они читались бы как проданные (грабли TL, тикет 08).
# Сверка 08.09 на «Горе»: adults=1 и adults=2 — одна выдача.
GUESTS = [{"adults": 1}]

# Индексы data[] ответа getHotelParams (full.js: getHotelParameters).
_HP_CATEGORIES = 6
_HP_HOTEL = 7
_HP_MIN_LEN = 8

_BROKEN_HINT = "похоже на смену API Frontdesk24 или антибот"

# Календарь спрашивается ОКНАМИ, а не одним POST-ом на год. Замер 08.09 на
# «Горе» (5 категорий): 366 суток — 44 с ответа при таймауте транспорта 25 с
# (живой прогон 01:09 упал на Read timed out), 91 сутки — 14 с, 90 суток за
# краем продаж — 3,5 с, то есть цена растёт с длиной диапазона (~0,13 с на
# сутки), а не с числом записей. 61 сутки — около 8 с на окно с запасом на
# отель побольше; год — шесть POST-ов вместо одного. Сама глубина при этом
# остаётся бесплатной по смыслу правила ревью 14.08: окна идут до конца
# горизонта, снапшот несёт весь год.
CALENDAR_WINDOW_DAYS = 61

# Версия РАЗБОРА движка (поле probe_version снапшота, тикет 10).
PROBE_VERSION = 2


def _body_text(fetch) -> str:
    """Тело последнего ответа fetch — им опознаётся страница-заслон.

    Импорт ленивый: пакет probes грузит этот модуль сам, и импорт на уровне
    модуля замкнул бы круг. Реализация одна на все движки — probes.body_text_of.
    """
    from . import body_text_of
    return body_text_of(fetch)


def endpoint(url_template: str, method: str) -> str:
    """URL метода API рядом с url_template рецепта.

    url_template рецепта — календарь (…/api/online/getAvailableDates); два
    других метода живут в том же каталоге, и держать три адреса в рецепте
    значило бы править три строки при смене хоста.
    """
    base = url_template.rstrip("/").rsplit("/", 1)[0]
    return f"{base}/{method}"


def engine_error(payload: object) -> str:
    """Текст ошибки движка из тела с кодом 200, иначе пустая строка.

    Frontdesk24 отвечает на негодный параметр не кодом 4xx, а телом
    {"error": {"code", "message"}} (живой ответ 08.09 на dateFrom="abc").
    Виджет читает это как ошибку (dataAccess.js: _onSuccess), и пробник
    обязан так же: иначе {"error": …} без "data" читался бы как смена схемы
    без текста, а с "data": [] — как «продаж нет».
    """
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if not error:
        return ""
    if isinstance(error, dict):
        message = error.get("message") or error.get("description") or ""
        code = error.get("code")
        text = str(message) if message else "без текста"
        return f"{text} (код {code})" if code not in (None, "") else text
    return str(error)


def _data_of(payload: object, what: str) -> list:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise SchemaChanged(
            f"в ответе {what} нет списка data — схема Frontdesk24 сменилась")
    return payload["data"]


def parse_hotel_params(payload: object) -> dict:
    """Ответ getHotelParams -> {"directory": {код: имя}, "hotel": dict|None}.

    directory — категории из data[6]; hotel — запись data[7] (hotelName,
    token) или None, когда движок отеля за токеном не нашёл. Пустые ОБА —
    токен мёртв (живой ответ по чужому токену 08.09: каркас на месте, списки
    пустые, кода ошибки нет). Не та форма -> SchemaChanged.
    """
    data = _data_of(payload, METHOD_PARAMS)
    if len(data) < _HP_MIN_LEN:
        raise SchemaChanged(
            f"в {METHOD_PARAMS} {len(data)} блоков data вместо "
            f"{_HP_MIN_LEN} — схема Frontdesk24 сменилась")
    categories = data[_HP_CATEGORIES]
    hotels = data[_HP_HOTEL]
    if not isinstance(categories, list) or not isinstance(hotels, list):
        raise SchemaChanged(
            f"в {METHOD_PARAMS} блоки категорий/отеля не списки — схема "
            f"Frontdesk24 сменилась")
    directory: dict[str, str] = {}
    for item in categories:
        if not isinstance(item, dict) or item.get("id") in (None, ""):
            raise SchemaChanged(
                f"категория {METHOD_PARAMS} без id — схема Frontdesk24 сменилась")
        code = str(item["id"])
        name = str(item.get("name") or "").strip() or f"Категория {code}"
        directory[code] = name
    hotel = hotels[0] if hotels and isinstance(hotels[0], dict) else None
    return {"directory": directory, "hotel": hotel}


def parse_available_dates(payload: object) -> dict:
    """Ответ getAvailableDates -> {код категории: {ночь: цена | None}}.

    В ответе перечислены только ПРОДАВАЕМЫЕ ночи категории; отсутствие
    записи толкует вызывающий (busy или sales_not_open — см. build_units).
    Цена — за ночь (проверено 08.09: подбор на две ночи стоит вдвое дороже
    цены дня из календаря). Не та форма -> SchemaChanged.
    """
    data = _data_of(payload, METHOD_DATES)
    offers: dict[str, dict] = {}
    for item in data:
        if not isinstance(item, dict) or item.get("roomCategoryID") in (None, "") \
                or not isinstance(item.get("date"), str):
            raise SchemaChanged(
                f"запись {METHOD_DATES} без roomCategoryID/date — схема "
                f"Frontdesk24 сменилась")
        night = item["date"][:10]
        if len(night) != 10 or night[4] != "-" or night[7] != "-":
            raise SchemaChanged(
                f"дата {item['date']!r} в {METHOD_DATES} не ISO — схема "
                f"Frontdesk24 сменилась")
        price = item.get("price")
        if not isinstance(price, (int, float)) or isinstance(price, bool) \
                or price <= 0:
            price = None
        nights = offers.setdefault(str(item["roomCategoryID"]), {})
        prev = nights.get(night)
        # Две записи на ночь (разные тарифы) — берём меньшую цену, как TL.
        if night not in nights or (price is not None
                                   and (prev is None or price < prev)):
            nights[night] = price
    return offers


def parse_variants(payload: object) -> dict:
    """Ответ getVariants -> {код категории: availableRooms}.

    Категории, которую под запрошенный отрезок продавать нельзя, в data[0]
    нет вовсе — это НЕ распродажа сама по себе (минимальный срок, закрытый
    заезд), её толкует apply_inventory вместе с календарём. Не та форма ->
    SchemaChanged.
    """
    data = _data_of(payload, METHOD_VARIANTS)
    if not data or not isinstance(data[0], list):
        raise SchemaChanged(
            f"в {METHOD_VARIANTS} нет списка категорий data[0] — схема "
            f"Frontdesk24 сменилась")
    left: dict[str, int] = {}
    for item in data[0]:
        if not isinstance(item, dict) or item.get("id") in (None, ""):
            raise SchemaChanged(
                f"категория {METHOD_VARIANTS} без id — схема Frontdesk24 "
                f"сменилась")
        rooms = item.get("availableRooms")
        if not isinstance(rooms, int) or isinstance(rooms, bool) or rooms < 0:
            raise SchemaChanged(
                f"{METHOD_VARIANTS}[{item['id']}].availableRooms не целое "
                f"число ({rooms!r}) — схема Frontdesk24 сменилась")
        code = str(item["id"])
        # Одна категория дважды — берём больший остаток (как TL и UHotels).
        if rooms > left.get(code, -1):
            left[code] = rooms
    return left


def unit_names(recipe_types: dict, engine_dir: dict) -> tuple[dict, list, list]:
    """Справочник юнитов -> ({код: уникальное имя}, [коды вне рецепта],
    [коды рецепта, которых нет у движка]).

    Имя рецепта главнее имени движка: там разведчик помечает береговые
    номера «(берег)» и это не должно затираться прогоном. Движок — источник
    полноты: его категорию без записи в рецепте снимаем под его именем.
    Пустой справочник движка (запрос не дошёл) полноту не проверяет — тогда
    третий список пуст, а не «всё пропало».
    """
    names: dict[str, str] = {}
    for code, name in (recipe_types or {}).items():
        names[str(code)] = str(name).strip() or f"Категория {code}"
    extra = [code for code in engine_dir if code not in names]
    for code in extra:
        names[code] = engine_dir[code]
    missing = ([code for code in names if code not in engine_dir]
               if engine_dir else [])
    seen: dict[str, int] = {}
    for name in names.values():
        seen[name] = seen.get(name, 0) + 1
    used: set[str] = set()
    unique: dict[str, str] = {}
    for code, name in names.items():
        unit = name if seen[name] == 1 else f"{name} [{code}]"
        while unit in used:
            unit = f"{unit} [{code}]"
        used.add(unit)
        unique[code] = unit
    return unique, extra, missing


def sales_edge(offers: dict) -> Optional[str]:
    """Последняя ночь, которую движок продаёт хоть в одной категории."""
    nights = [n for by_night in offers.values() for n in by_night]
    return max(nights) if nights else None


def calendar_windows(date_from: date, date_to: date,
                     days: int = CALENDAR_WINDOW_DAYS) -> list[tuple]:
    """Окна календаря [(первая ночь, последняя ночь), ...] встык до date_to."""
    out = []
    start = date_from
    while start <= date_to:
        end = min(date_to, start + timedelta(days=days - 1))
        out.append((start, end))
        start = end + timedelta(days=1)
    return out


def build_units(offers: dict, names: dict, dates: list[str],
                edge: Optional[str], absent: list,
                covered: Optional[set] = None) -> dict:
    """Календарь -> сетка units по SCHEMA (б), без фонда.

    Запись есть -> free (+цена); нет записи до общей границы окна -> busy
    (оценка сверху); за границей (edge) -> sales_not_open у всех категорий.
    Категории из absent (рецепт знает, движок уже нет) — unknown целиком;
    ночи вне covered (окно календаря не снялось) — unknown у всех.
    """
    units: dict[str, dict] = {}
    for code, unit in names.items():
        by_night = offers.get(code) or {}
        cells: dict[str, dict] = {}
        for night in dates:
            if code in absent or (covered is not None and night not in covered):
                cells[night] = {"state": "unknown"}
            elif night in by_night:
                cells[night] = {"state": "free"}
                if by_night[night] is not None:
                    cells[night]["price"] = by_night[night]
            elif edge is not None and night > edge:
                cells[night] = {"state": "sales_not_open"}
            else:
                cells[night] = {"state": "busy"}
        units[unit] = cells
    return units


def apply_inventory(units: dict, names: dict,
                    by_night: dict) -> tuple[int, int, dict]:
    """Проставить фонд по подбору. -> (клеток размечено, клеток-разногласий,
    {код: фонд}).

    Правило TravelLine (probes/travelline.py: apply_inventory) дословно:
    фонд категории = максимум наблюдённого остатка (оценка снизу); клетка
    получает пару, только когда календарь и подбор говорят одно и то же —
    календарь free и остаток > 0 -> свободно `остаток` из `фонд`; календарь
    busy и категории в подборе нет -> заняты все. Расходятся (подбор на
    одну ночь молчит при открытой ночи календаря — минимальный срок;
    подбор видит номер там, где календарь закрыт) — клетка остаётся
    бинарной и считается в разногласия: выдумать пару здесь значило бы
    подписать чужое незнание цифрой.
    """
    capacity: dict[str, int] = {}
    for rest in by_night.values():
        for code, left in rest.items():
            if left > capacity.get(code, 0):
                capacity[code] = left
    marked = disagreed = 0
    for code, unit in names.items():
        cap = capacity.get(code)
        cells = units.get(unit) or {}
        for night, rest in by_night.items():
            cell = cells.get(night)
            if cell is None or cell["state"] not in ("free", "busy"):
                continue
            # Категории нет в подборе или availableRooms=0 — под этот заезд
            # ничего не продаётся; для календаря это одно и то же.
            left = rest.get(code) or 0
            if cell["state"] == "free":
                if left:
                    cell["units_total"] = max(cap or 0, left)
                    cell["units_free"] = left
                    marked += 1
                else:
                    disagreed += 1
            elif left:
                disagreed += 1
            elif cap:
                cell["units_total"] = cap
                cell["units_free"] = 0
                marked += 1
    return marked, disagreed, capacity


def _shortlist(items, limit: int = 5) -> str:
    items = list(items)
    head = ", ".join(str(x) for x in items[:limit])
    return head + (f" и ещё {len(items) - limit}" if len(items) > limit else "")


def _with_notes(result: tuple, notes: list[str]) -> tuple:
    """Дописать информационные пометки в reason, не трогая статус.

    Граница окна продаж, категории вне рецепта, разногласия календаря и
    подбора — не сбой съёма: через failures они делали бы объект partial
    каждый день без единого изменения в данных (ревью UHotels).
    """
    obj, broken = result
    if not broken:
        parts = [obj.get("reason")] + [n for n in notes if n]
        obj["reason"] = "; ".join(p for p in parts if p)
    return obj, broken


def _unknown_grid(obj: dict, names: dict, dates: list[str]) -> None:
    for unit in names.values():
        obj["units"][unit] = {d: {"state": "unknown"} for d in dates}


def probe(username: str, recipe: dict, date_from: date, date_to: date,
          fetch: Optional[Callable] = None, inventory: bool = True,
          inventory_date_to: Optional[date] = None):
    """Снять сетку по рецепту Frontdesk24. -> (obj, broken_reason | None).

    Три шага: справочник и живость токена (1 POST) -> календарь до конца
    горизонта окнами по CALENDAR_WINDOW_DAYS (deep_date_to: глубина
    бесплатна по смыслу, но год одним POST-ом не влезает в таймаут) ->
    фонд по ночам горизонта фонда (POST на ночь; inventory=False
    пропускает шаг, inventory_date_to режет его, как у TravelLine, тикет
    06). Сетка при этом всегда полная: grid_until = конец горизонта; ночи
    окна, которое не снялось, — unknown, а не busy.
    """
    if fetch is None:
        fetch = _make_fetch("post_json")
    request = recipe.get("request", {})
    params = request.get("params", {})
    headers = request.get("headers", {})
    obj = _common_base(username, recipe, "per_unit", default_engine="frontdesk24")
    token = params.get("token")
    if not token:
        return _finish(obj, [], "в рецепте нет params.token — нечем звать "
                                "виджет Frontdesk24")
    url = request.get("url_template", "")
    if not url:
        return _finish(obj, [], "в рецепте нет request.url_template")
    language = params.get("language") or "ru"
    currency = params.get("currency") or "RUB"

    deep_to = deep_date_to(date_to)
    dates = _iso_dates(date_from, deep_to)
    obj["grid_until"] = deep_to.isoformat()
    # Пол границы фонда — date_from: ни одной ночи фонда не спрошено (контракт
    # волны 4, probes/__init__). Поднимается, когда шаг фонда идёт.
    obj["inventory_until"] = date_from.isoformat()
    failures: list[str] = []
    notes: list[str] = []
    recipe_types = params.get("room_types") or {}
    include = params.get("include_room_types")
    if "include_room_types" in params:
        if (not isinstance(include, list) or not include
                or any(isinstance(code, bool) or not isinstance(code, (str, int))
                       or not str(code).strip() for code in include)):
            return _finish(obj, [], "params.include_room_types должен быть "
                                    "непустым списком ID категорий")
        include = list(dict.fromkeys(str(code).strip() for code in include))
        recipe_types = {str(code): name for code, name in recipe_types.items()}

    # -- шаг 1: справочник категорий и живость токена ------------------------
    engine_dir: dict[str, str] = {}
    hotel_seen = False
    what = METHOD_PARAMS
    try:
        status, data = fetch(endpoint(url, METHOD_PARAMS), headers,
                             {"token": token, "language": language})
    except AccessRefused as e:
        return _finish(obj, failures, None, refused=e.reason,
                       refusal_status=e.status)
    except OSError as e:
        failures.append(f"{what}: сетевой сбой ({e})")
    else:
        obj["source_urls"].append(endpoint(url, METHOD_PARAMS))
        verdict = classify_response(status, data is not None, what,
                                    detail=engine_error(data),
                                    hint=_BROKEN_HINT,
                                    body_text=_body_text(fetch))
        if verdict.kind == "refused":
            return _finish(obj, failures, None, refused=verdict.reason,
                           refusal_status=status)
        if verdict.kind == "broken":
            return _finish(obj, failures, verdict.reason)
        if verdict.kind == "network":
            failures.append(verdict.reason)
        else:
            error = engine_error(data)
            if error:
                return _finish(obj, failures,
                               f"{what}: движок ответил ошибкой — {error}")
            try:
                parsed = parse_hotel_params(data)
            except SchemaChanged as e:
                return _finish(obj, failures, f"{what}: {e}")
            engine_dir = parsed["directory"]
            hotel_seen = parsed["hotel"] is not None
            if not hotel_seen and not engine_dir:
                # Чужой токен: каркас ответа на месте, отеля и категорий нет.
                # Календарь по нему отвечает пустым списком — то есть без
                # этой проверки мёртвый токен читался бы как «продаж нет».
                return _finish(obj, failures,
                               f"{what}: за токеном {token!r} движок не нашёл "
                               f"ни отеля, ни категорий — токен сменился или "
                               f"модуль отключён, нужна переразведка")
    if include is not None:
        # Добавляем даже неизвестный ID, иначе опечатка или удалённый борт
        # исчезнут из результата вместо явной unknown-сетки.
        recipe_types = dict(recipe_types)
        for code in include:
            recipe_types.setdefault(code, engine_dir.get(code, f"Категория {code}"))
    names, extra, missing = unit_names(recipe_types, engine_dir)
    if include is not None:
        names = {code: names[code] for code in include}
        extra = [code for code in extra if code in names]
        missing = [code for code in missing if code in names]
        # Живой отель без категорий тоже подтверждает отсутствие выбранных ID.
        if hotel_seen and not engine_dir:
            missing = list(include)
        notes.append("выдача ограничена выбранными категориями: "
                     + _shortlist(f"{names[c]} [{c}]" for c in include))
    if not names:
        failures.append("движок не отдал ни одной категории, и в рецепте "
                        "нет params.room_types — сетку строить не из чего")
        return _finish(obj, failures, None)
    if extra:
        notes.append("категории движка вне рецепта сняты под именами "
                     "движка, добавьте их в params.room_types: "
                     + _shortlist(f"{engine_dir[c]} [{c}]" for c in extra))
    if missing:
        notes.append("категорий рецепта у движка больше нет, их клетки "
                     "unknown: " + _shortlist(f"{names[c]} [{c}]"
                                              for c in missing))

    # -- шаг 2: календарь окнами по CALENDAR_WINDOW_DAYS до конца горизонта ---
    windows = calendar_windows(date_from, deep_to)
    obj["source_urls"].append(
        f"{endpoint(url, METHOD_DATES)}#{date_from.isoformat()}.."
        f"{deep_to.isoformat()} окнами по {CALENDAR_WINDOW_DAYS} сут., POST")
    offers: dict[str, dict] = {}
    covered: set[str] = set()
    windows_failed = 0
    refused: Optional[str] = None
    refusal_status: Optional[int] = None
    for start, end in windows:
        what = f"{METHOD_DATES} {start.isoformat()}..{end.isoformat()}"
        payload = {"token": token, "language": language,
                   "dateFrom": start.isoformat(), "dateTo": end.isoformat(),
                   "currency": currency}
        try:
            status, data = fetch(endpoint(url, METHOD_DATES), headers, payload)
        except AccessRefused as e:
            refused, refusal_status = e.reason, e.status
            break
        except OSError as e:
            failures.append(f"{what}: сетевой сбой ({e})")
            windows_failed += 1
            continue
        verdict = classify_response(status, data is not None, what,
                                    detail=engine_error(data),
                                    hint=_BROKEN_HINT,
                                    body_text=_body_text(fetch))
        # Нас не пустили — окна дальше не идут (и фонд тоже): ломиться в
        # закрытую дверь незачем, снятое остаётся, рецепт жив.
        if verdict.kind == "refused":
            refused, refusal_status = verdict.reason, status
            break
        if verdict.kind == "broken":
            return _finish(obj, failures, verdict.reason)
        if verdict.kind == "network":
            failures.append(verdict.reason)
            windows_failed += 1
            continue
        error = engine_error(data)
        if error:
            return _finish(obj, failures,
                           f"{what}: движок ответил ошибкой — {error}")
        try:
            window_offers = parse_available_dates(data)
        except SchemaChanged as e:
            return _finish(obj, failures, f"{what}: {e}")
        covered.update(_iso_dates(start, end))
        for code, by_night in window_offers.items():
            offers.setdefault(code, {}).update(by_night)
    if not covered:
        _unknown_grid(obj, names, dates)
        return _finish(obj, failures, None, refused=refused,
                       refusal_status=refusal_status)
    # Граница окна продаж читается только по ПОЛНОМУ календарю: если хоть
    # одно окно не снялось, «дальше ничего не продаётся» может оказаться
    # «дальше не спросили», и хвост остаётся busy (оценка сверху), а не
    # sales_not_open.
    full_calendar = not windows_failed and refused is None
    edge = sales_edge(offers)
    if edge is None:
        # Токен жив (шаг 1), но ни одной открытой ночи: продажи закрыты или
        # модуль пуст. Аншлагом это не называется, рецепт не ломается —
        # переразведка тут ничего не починит.
        failures.append(
            f"движок не отдал ни одной открытой ночи на "
            f"{len(covered)} снятых ноч. горизонта — продажи закрыты или "
            f"модуль пуст, распродажей это не считается")
        _unknown_grid(obj, names, dates)
        return _finish(obj, failures, None, refused=refused,
                       refusal_status=refusal_status)
    obj["units"] = build_units(offers, names, dates,
                               edge if full_calendar else None, missing,
                               covered)
    if full_calendar and edge < deep_to.isoformat():
        notes.append(f"продажи открыты до {edge}: ночи дальше размечены как "
                     f"закрытые продажи (sales_not_open), а не занятость")
    if refused is not None:
        return _with_notes(
            _finish(obj, failures, None, refused=refused,
                    refusal_status=refusal_status), notes)
    stray = [code for code in offers if code not in names
             and (include is None or code not in engine_dir)]
    if stray:
        # Календарь знает категорию, которой нет ни в справочнике движка, ни
        # в рецепте: клеток под неё нет, но молчать нельзя.
        notes.append("в календаре есть категории без имени в справочнике, "
                     "они не сняты: " + _shortlist(stray))

    # -- шаг 3: фонд по ночам горизонта фонда ---------------------------------
    if inventory:
        fund_until = (date_to if inventory_date_to is None
                      else max(date_from, min(date_to, inventory_date_to)))
        fund_until = min(fund_until, deep_to)
        obj["inventory_until"] = fund_until.isoformat()
        fund_nights = _iso_dates(date_from, fund_until)
        obj["source_urls"].append(
            f"{endpoint(url, METHOD_VARIANTS)}#окно 1 ночь на каждую из "
            f"{len(fund_nights)} ноч. до {fund_until.isoformat()}, POST")
        by_night: dict[str, dict] = {}
        for night in fund_nights:
            what = f"{METHOD_VARIANTS} на {night}"
            body = {
                "token": token, "language": language, "currency": currency,
                "dateFrom": night,
                "dateTo": (date.fromisoformat(night) + timedelta(days=1)).isoformat(),
                "rooms": GUESTS,
                "onlyRostourismProgram": 0,
            }
            try:
                status, data = fetch(endpoint(url, METHOD_VARIANTS), headers, body)
            except AccessRefused as e:
                refused, refusal_status = e.reason, e.status
                break
            except OSError as e:
                failures.append(f"{what}: сетевой сбой ({e})")
                continue
            verdict = classify_response(status, data is not None, what,
                                        detail=engine_error(data),
                                        hint=_BROKEN_HINT,
                                        body_text=_body_text(fetch))
            # Нас не пустили — ночи дальше не идут: десятки запросов подряд к
            # одному хосту, и продолжать после 403/429 значит ломиться в
            # закрытую дверь. Сетка при этом уже снята — рецепт жив.
            if verdict.kind == "refused":
                refused, refusal_status = verdict.reason, status
                break
            # Одиночный 4xx/5xx на ночи фонда рецепт не ломает: ночь остаётся
            # бинарной, сбой уходит в причину (как у TL и UHotels).
            if verdict.kind in ("broken", "network"):
                failures.append(verdict.reason)
                continue
            error = engine_error(data)
            if error:
                failures.append(f"{what}: движок ответил ошибкой — {error}")
                continue
            try:
                by_night[night] = parse_variants(data)
            except SchemaChanged as e:
                # Схема сменилась на ВСПОМОГАТЕЛЬНОМ шаге: календарь уже снят, фонд
                # дальше не снимаем, объект считается по типам, рецепт жив — как у
                # bookonline24 и TravelLine (ревью 14.09.2026; раньше рецепт ломался
                # и снятый календарь выпадал из сводки).
                failures.append(f"{what}: {e} — фонд дальше не снимался")
                break
        marked, disagreed, capacity = apply_inventory(obj["units"], names, by_night)
        if by_night:
            no_fund = [names[c] for c in names
                       if c not in missing and not capacity.get(c)]
            if no_fund:
                # Фонд такой категории неизвестен: она весит один домик и в
                # занятости, и в ответе «сколько всего домиков».
                failures.append(
                    "фонд не снят у категорий (ни разу не были свободны в "
                    "подборе за горизонт фонда, считаются как один домик): "
                    + _shortlist(no_fund))
            if disagreed:
                notes.append(
                    f"календарь и подбор разошлись на {disagreed} клетках "
                    f"(минимальный срок или ограничение по гостям) — эти "
                    f"клетки оставлены бинарными, без фонда")
    return _with_notes(
        _finish(obj, failures, None, refused=refused,
                refusal_status=refusal_status), notes)
