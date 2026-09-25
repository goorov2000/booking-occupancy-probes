# -*- coding: utf-8 -*-
"""Пробник TravelLine: рецепт -> сетка доступности по категориям с ценами.

Движок живёт в двух вариантах (оба встречались в calendars-out/ волны 2):

(1) reservationsteps.ru — модуль на public-api.reservationsteps.ru
    (разведка тикета 03: на деле это платформа Bnovo — engine=bnovo ходит
    в тот же код алиасом, см. bnovo.py; здесь вариант остаётся, потому что
    волна 2 классифицировала такие объекты как travelline):
    GET .../v1/api/min_prices?uid={uid}&dfrom={DD-MM-YYYY}&dto={DD-MM-YYYY}
        [&room_type_id={id}]
    Ответ: {"min_prices": {"YYYY-MM-DD": null | {"p": цена, "g": гостей}}}.
    null = категория в эту ночь не продаётся (занята/закрыта — неотличимо,
    оценка сверху), объект с "p" = свободна, цена за ночь. Пустой ответ
    {"min_prices": []} = тарифов нет вообще -> sales_not_open (живой пример:
    ok_reka, модуль формально подключён, продаж нет ни на одну дату до 2027).
    Запрос без room_type_id — агрегат по всем категориям (granularity
    aggregate). ВАЖНО: агрегатный сосед closed_dates_with_reasons красит дату
    занятой только когда проданы ВСЕ категории — поэтому снимаем min_prices
    ПО КАТЕГОРИЯМ (params.room_types), а не агрегат.

(2) TL Integration (ibe.tlintegration.ru / ru-ibe.tlintegration.ru):
    три лёгких GET на объект (разведано браузером 14.08 по живому трафику
    виджета istra-cottage.ru, затем воспроизведено чистым HTTP):
    - BookingForm/hotel_info?hotels[0].code={code}&language=ru-ru —
      справочник категорий (код -> имя) и окно продаж
      (booking_rules.availability_max_date);
    - AvailabilityCalendar/room_type_availability_2?aggregate_dates=false&
      max_nights=1&hotel={code}&start_date=...&end_date=... — календарь
      виджета: по каждой категории ночи, где она свободна, с ценами
      (несколько записей на ночь = тарифы/гости, берём минимум); проданные
      ночи в ответе просто ОТСУТСТВУЮТ (is_available=false не приходит);
      сверено с тяжёлым BookingForm/hotel_availability — совпадает 1:1.
      Диапазон длиннее 360 суток эндпоинт отвергает телом
      {"room_type_availability": [], "errors": [{"error_code": "320", ...}]}
      при HTTP 200 — поэтому горизонт спрашивается окнами (RULES_WINDOW_DAYS)
      и склеивается, а непустой errors читается как «окно не снялось»;
    - AvailabilityCalendar/hotel_booking_rules?start_date=...&end_date=... —
      агрегатный календарь дейтпикера, нужен только чтобы толковать ночи,
      пустые у ВСЕХ категорий: forbidden -> busy (проданы все), иначе
      unknown; за окном продаж -> sales_not_open.
    WAF Qrator пропускает обычные HTTP-запросы с браузерными заголовками на
    эти пути (разведка 14.08); волна 2 получала 403 на корень домена и
    угаданные пути — это не признак недоступности API.

Оба варианта: один запрос — одна попытка, пауза между запросами к хосту
(транспорт — probes/_common.py, правила исхода — probes/_outcome.py),
ничего не бронируется.
Сетевой сбой -> unknown-клетки/insufficient_data без пометки broken; смена
схемы ответа или 4xx/капча -> insufficient_data и broken_reason (рецепт
уходит на переразведку).

Глубина (ревью 14.08): углубление у обоих вариантов бесплатно по числу
запросов, поэтому снапшот несёт весь доступный горизонт — вариант (1)
расширяет dfrom..dto минимум до 2027-03 (за окном продаж ночи приходят
null и читаются busy — оценка сверху), вариант (2) сохраняет все ночи
ответа, не режа сетку по date_to. Сводка показывает только свои месяцы.
"""
from __future__ import annotations

import re
import urllib.parse
from datetime import date, timedelta
from typing import Callable, Optional

from occupancy_core import AGGREGATE_UNIT

from ._common import (AccessRefused, SchemaChanged,
                      _base_object as _common_base, _iso_dates,
                      deep_date_to, is_challenge,
                      make_fetch as _make_fetch)
# Каскад и сборка объекта идут через правила ИСХОДА (probes/_outcome.py):
# 5xx рецепт не ломает никогда, а отказ хоста поверх уже снятой сетки не
# заводит счётчик суток. Транспорт про судьбу рецепта не знает.
from ._outcome import classify_response, finish as _finish

# Окно продаж: верхняя оценка длительности юнитов TravelLine в днях.
_TIME_UNIT_DAYS = {"day": 1, "week": 7, "month": 31, "year": 366}

_BROKEN_HINT = "похоже на смену API или антибот"

# Версия РАЗБОРА этого движка (пишется в снапшот полем probe_version).
# 2 — правка 04.09.2026: фонд снимается по своему горизонту (тикет 06), а
# отказ хоста больше не ломает рецепт (тикет 03). Смысл клетки за границей
# фонда изменился («фонд не спрашивали» вместо «движок не отдал»), поэтому
# снимки до и после этой правки обязаны различаться подписью.
PROBE_VERSION = 2


class RequestRejected(Exception):
    """4xx/капча/не-JSON: движок отверг запрос -> broken (переразведка)."""


class WindowRejected(Exception):
    """HTTP 200 с непустым errors: движок отверг ДИАПАЗОН окна, а не схему.

    Отдельный класс, а не SchemaChanged: смена схемы ломает рецепт
    (переразведка руками), а отвергнутое окно — ошибка НАШЕГО запроса
    (живой пример 05.09: error 320 «max 360 days period» на годовой
    диапазон), и лечится она следующим прогоном или правкой окна. Ночи
    такого окна обязаны стать unknown, а не читаться как «свободных нет».
    """


def _base_object(username: str, recipe: dict, granularity: str) -> dict:
    return _common_base(username, recipe, granularity,
                        default_engine="travelline")


def _body_text(fetch) -> str:
    """Тело последнего ответа fetch — им опознаётся страница-заслон.

    Импорт ленивый: пакет probes грузит этот модуль сам, и импорт на уровне
    модуля замкнул бы круг. Реализация одна на все движки — probes.body_text_of.
    """
    from . import body_text_of
    return body_text_of(fetch)


def _inventory_until(date_from: date, date_to: date,
                     inventory_date_to: Optional[date]) -> date:
    """Последняя ночь, на которую спрашивается ФОНД (тикет 06).

    Шаг фонда стоит одного HTTP-запроса на ночь — это почти вся цена
    прогона, поэтому у него свой горизонт, короче горизонта сетки. Граница
    ниже date_from не опускается: пустой список ночей и без того значит
    «фонд не спрашивали», а отрицательный диапазон только путал бы читателя.

    Замер по живому снапшоту 2026-09-04-0631 (13 объектов с поночным шагом:
    10 travelline/bnovo + pineriver с двумя окнами + dacha_limerence):
    горизонт 366 ночей — 5124 запроса, то есть ~128 мин при паузе 1,2 с плюс
    средний джиттер 0,3 с; стартовые 45 ночей фонда — 644 запроса, ~16 мин.
    Экономия ~112 минут на прогон, и без неё прогон не влезал бы даже в
    поднятый до 120 мин TimeoutStartSec.
    """
    if inventory_date_to is None:
        return date_to
    return max(date_from, min(date_to, inventory_date_to))


# ---------------------------------------------------------------------------
# Вариант (1): public-api.reservationsteps.ru
# ---------------------------------------------------------------------------

def parse_min_prices(payload: object, dates: list[str]) -> dict:
    """Ответ min_prices -> клетки {дата: {"state": ..., "price"?: ...}}.

    Не та форма -> SchemaChanged. Пустой min_prices ([] или {}) — тарифов
    нет вообще: все даты sales_not_open.
    """
    if not isinstance(payload, dict) or "min_prices" not in payload:
        raise SchemaChanged(
            "в ответе нет ключа min_prices — схема reservationsteps сменилась")
    prices = payload["min_prices"]
    if prices == [] or prices == {}:
        return {d: {"state": "sales_not_open"} for d in dates}
    if not isinstance(prices, dict):
        raise SchemaChanged(
            f"min_prices не словарь дата->цена ({type(prices).__name__})")
    cells = {}
    for d in dates:
        value = prices.get(d, "__missing__")
        if value == "__missing__":
            cells[d] = {"state": "unknown"}
        elif value is None:
            cells[d] = {"state": "busy"}
        elif isinstance(value, dict) and isinstance(value.get("p"), (int, float)):
            cells[d] = {"state": "free", "price": value["p"]}
        else:
            raise SchemaChanged(f"неожиданная клетка min_prices[{d}]={value!r}")
    return cells


def _ddmmyyyy(d: date) -> str:
    return d.strftime("%d-%m-%Y")


# Селект карточки категории на rooms/index: data-real-room-id — категория,
# data-available — сколько её номеров свободно на запрошенные даты.
_ROOMS_SELECT = re.compile(r"<select\b[^>]*>")
_TAG_ATTRS = re.compile(r'([a-z-]+)="([^"]*)"')

ROOMS_API = "https://public-api.reservationsteps.ru/v1/api/rooms"
ROOMS_PAGE = "https://reservationsteps.ru/rooms/index/{uid}"


def parse_rooms_inventory(payload: object) -> dict:
    """/v1/api/rooms -> {room_type_id: сколько номеров свободно}.

    Категории, которой в ответе нет, свободных номеров нет вовсе (проверено
    15.08 на a_ureki: полностью занятая ночь отдаёт rooms: []).
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("rooms"), list):
        raise SchemaChanged("в ответе нет списка rooms — схема Bnovo сменилась")
    rest: dict[str, int] = {}
    for room in payload["rooms"]:
        if not isinstance(room, dict) or not room.get("id"):
            raise SchemaChanged("запись rooms без id категории")
        available = room.get("available")
        if isinstance(available, int) and not isinstance(available, bool) \
                and available > 0:
            rest[str(room["id"])] = available
    return rest


def parse_rooms_page_inventory(html: str) -> dict:
    """Страница rooms/index -> {room_type_id: свободно номеров}.

    Фолбэк для объектов без account_id (у них нет формы листа ожидания, а
    другого места с числовым id аккаунта на странице нет — проверка 15.08 на
    yck_kuzminskoe). Страница тяжелее JSON, поэтому путь второй по очереди.

    Живучесть страницы проверяем по блоку `const categories` — он есть
    всегда. Селектов с остатком может не быть вовсе: на распроданную ночь
    выбирать нечего (живой пример — yck_kuzminskoe на 15-16.08.2026). Это
    пустой остаток, а не смена вёрстки: путать их нельзя, иначе объект
    уходит в partial на ровном месте.
    """
    if "const categories" not in html:
        raise SchemaChanged(
            "на странице rooms/index нет блока categories — вёрстка Bnovo "
            "сменилась или пришла не та страница")
    rest: dict[str, int] = {}
    for tag in _ROOMS_SELECT.findall(html):
        attrs = dict(_TAG_ATTRS.findall(tag))
        code = attrs.get("data-real-room-id")
        available = attrs.get("data-available")
        if not code or not available or not available.isdigit():
            continue
        value = int(available)
        if value > rest.get(code, 0):
            rest[code] = value
    return rest


def _collect_bnovo_inventory(recipe: dict, nights: list[str], fetch: Callable,
                             text_fetch: Optional[Callable],
                             obj: dict, span: int = 1
                             ) -> tuple[dict, list[str], Optional[str]]:
    """Остатки номеров Bnovo по ночам: {ночь: {категория: свободно}}, сбои, fatal.

    Лёгкий путь — JSON /v1/api/rooms (нужен params.account_id из формы листа
    ожидания). Нет account_id — тяжёлый фолбэк по HTML страницы подбора.
    Шаг вспомогательный: не снялся — объект остаётся посчитанным по типам.

    span — длина окна запроса в ночах. По умолчанию одна ночь (шаг фонда
    типов). Объекты с минимальным сроком проживания спрашиваются окном в
    min_stay ночей: на окно короче минимального движок честно отвечает
    «свободных нет» по ВСЕМ категориям, и однночное окно у такого объекта
    даёт ложную полную занятость (живой пример 15.08 — dacha_limerence).

    Третьим элементом — fatal: причина, по которой шаг требует РАЗВЕДЧИКА
    (смена вёрстки/схемы, 4xx кроме отказа), или None, если шаг просто не
    снялся. Для фонда типов разница неважна — там шаг вспомогательный, — но
    у ветки min_stay этот же шаг И ЕСТЬ сетка, и без признака сетевой сбой
    возвращался вызывающему как смена схемы, то есть снимал живой рецепт с
    производства насовсем (ревью волны 3).
    """
    params = recipe.get("request", {}).get("params", {})
    headers = recipe.get("request", {}).get("headers", {})
    account_id = params.get("account_id")
    uid = params.get("uid", "")
    by_night: dict[str, dict] = {}
    failures: list[str] = []
    fatal: Optional[str] = None
    if not account_id and text_fetch is None:
        # Ни лёгкого, ни тяжёлого пути: объект честно считается по типам,
        # и сводка помечает его сама — шуметь в reason незачем.
        return {}, [], None
    for night in nights:
        d = date.fromisoformat(night)
        dfrom, dto = _ddmmyyyy(d), _ddmmyyyy(d + timedelta(days=span))
        if account_id:
            url = (f"{ROOMS_API}?account_id={account_id}&dfrom={dfrom}"
                   f"&dto={dto}&lang=ru")
            reader, parser = fetch, parse_rooms_inventory
        else:
            url = (ROOMS_PAGE.format(uid=uid)
                   + f"?lang=ru&dfrom={dfrom}&dto={dto}")
            reader, parser = text_fetch, parse_rooms_page_inventory
        try:
            status, data = reader(url, headers)
        except AccessRefused as e:
            failures.append(f"остатки: {e.reason}")
            break
        except OSError as e:
            failures.append(f"остатки на {night}: сетевой сбой ({e})")
            continue
        # На HTML-пути тело — это САМ ответ: транспорт держит тела только
        # неудачных ответов, а заслон Cloudflare приходит с кодом 200 и
        # страницей вместо разметки. Без этой проверки заслон доезжал до
        # парсера и вылетал как «вёрстка Bnovo сменилась», то есть слал
        # живой рецепт на переразведку (ревью волны 3). Заслон мы не
        # обходим — честно говорим, что нас не пустили.
        page = data if isinstance(data, str) else ""
        body_ok = data is not None and not is_challenge(page)
        verdict = classify_response(status, body_ok,
                                    f"остатки на {night}",
                                    body_text=page or _body_text(reader))
        if verdict.kind == "refused":
            # Шаг фонда вспомогательный: рецепт он не ломает никогда, и
            # копить сутки отказов по нему незачем. Но долбиться в хост,
            # который нас не пускает, тоже нельзя — обрываем шаг.
            failures.append(verdict.reason)
            break
        if verdict.kind != "ok":
            failures.append(verdict.reason)
            if verdict.kind == "broken":
                fatal = fatal or verdict.reason
            continue
        try:
            by_night[night] = parser(data)
        except SchemaChanged as e:
            failures.append(f"остатки на {night}: {e}")
            fatal = fatal or f"остатки на {night}: {e}"
            break
    if any(rest for rest in by_night.values()):
        obj["source_urls"].append(
            (ROOMS_API if account_id else ROOMS_PAGE.format(uid=uid))
            + "#по ночам")
    return by_night, failures, fatal


def _probe_bnovo_min_stay(username: str, recipe: dict, date_from: date,
                          date_to: date, fetch: Callable,
                          text_fetch: Optional[Callable], min_stay: int,
                          room_types: dict,
                          inventory_date_to: Optional[date] = None):
    """Сетка объекта с минимальным сроком: окна по min_stay ночей.

    Зачем отдельная ветка (разведка 15.08, dacha_limerence): min_prices
    отвечает ценой только на выполнимый заезд, поэтому у объекта с минимумом
    в 2 ночи он отдаёт null на ВЕСЬ горизонт — а пустая цена читается как
    «занято», и объект показывает ровные 100% занятости в каждом месяце.
    Здесь вместо цен спрашивается сама страница подбора окном в min_stay
    ночей: data-available категории = сколько её номеров свободно под такой
    заезд.

    Клетка тут значит «в эту ночь можно НАЧАТЬ заезд», а не «ночь свободна»:
    свободная ночь в промежутке короче минимального срока считается занятой.
    Это та же оценка сверху, что и везде, но названная вслух — объект уходит
    в partial с пояснением, и сводка показывает причину читателю.

    min_stay=1 (сюда приходит рецепт с params.grid_source="rooms") оговорки
    не получает: окно в одну ночь и есть вопрос «свободна ли ночь».
    """
    obj = _base_object(username, recipe, "per_unit")
    # Здесь сетка И ЕСТЬ фонд: каждая ночь — отдельное окно, отдельный
    # запрос. Поэтому граница фонда режет и сетку (тикет 06): на годовом
    # горизонте эта ветка стоила бы 366 запросов к одному хосту.
    fund_until = _inventory_until(date_from, date_to, inventory_date_to)
    obj["inventory_until"] = fund_until.isoformat()
    # Докуда спрашивали ДОСТУПНОСТЬ. Здесь это та же граница, что у фонда:
    # сводка обязана отличать «дальше не спрашивали» от «движок не ответил»,
    # иначе месяц за границей читается как обычное «нет данных».
    obj["grid_until"] = fund_until.isoformat()
    nights = _iso_dates(date_from, fund_until)
    by_night, failures, fatal = _collect_bnovo_inventory(
        recipe, nights, fetch, text_fetch, obj, span=min_stay)
    if fund_until < date_to:
        failures.append(
            f"сетка снята до {fund_until.isoformat()} — дальше окна не "
            f"спрашивались (горизонт фонда): у объекта, чья сетка строится "
            f"окнами страницы подбора, ночь стоит отдельного запроса")
    if not by_night:
        # Рецепт ломаем ТОЛЬКО на смене вёрстки/схемы (fatal): сеть, 5xx и
        # отказ хоста лечатся повтором прогона, а broken-рецепт в прогоны
        # больше не идёт вовсе, пока агент не переразведает объект руками.
        # У dacha_limerence эта ветка несёт ВСЮ сетку, и один ночной обрыв
        # снимал объект с производства насовсем (ревью волны 3).
        return _finish(obj, failures,
                       f"объект с минимальным сроком {min_stay} ноч.: страница "
                       f"подбора не отдала ни одного окна — снимать нечего"
                       if fatal else None)
    capacity: dict[str, int] = {}
    for rest in by_night.values():
        for code, left in rest.items():
            if isinstance(left, int) and left > capacity.get(code, 0):
                capacity[code] = left
    for code, unit_name in ((str(k), v) for k, v in room_types.items()):
        cells = {}
        for night in nights:
            rest = by_night.get(night)
            if rest is None:
                cells[night] = {"state": "unknown"}
                continue
            left = rest.get(code, 0)
            cap = max(capacity.get(code, 0), left, 1)
            cells[night] = {"state": "free" if left > 0 else "busy",
                            "units_total": cap, "units_free": left}
        obj["units"][unit_name] = cells
    if min_stay >= 2:
        # Окно длиной в одну ночь оговорки не требует: «можно начать заезд
        # на одну ночь» и «ночь свободна» — одно и то же утверждение.
        failures.append(
            f"минимальный срок проживания {min_stay} ноч.: клетка показывает, "
            f"можно ли НАЧАТЬ заезд в эту ночь — свободные ночи в промежутках "
            f"короче минимума считаются занятыми (оценка сверху)")
    return _finish(obj, failures, None)


def _probe_reservationsteps(username: str, recipe: dict, date_from: date,
                            date_to: date, fetch: Callable,
                            inventory: bool = True,
                            text_fetch: Optional[Callable] = None,
                            inventory_date_to: Optional[date] = None):
    request = recipe["request"]
    params = request.get("params", {})
    headers = request.get("headers", {})
    min_stay = params.get("min_stay")
    has_min_stay = (isinstance(min_stay, int) and not isinstance(min_stay, bool)
                    and min_stay >= 2)
    # params.grid_source="rooms" — сетку строит НЕ min_prices, а страница
    # подбора/эндпоинт rooms окнами по ночи. Нужен там, где цены в публичный
    # API не отдаются вовсе: у аккаунта с has_to_show_calendar_prices=0
    # виджет getMinimalPrices даже не зовёт, min_prices отвечает пустотой на
    # любые даты, и пробник покрасил бы весь горизонт sales_not_open
    # (живой smr_shvedskie_dachi, 09.09.2026). Механика та же, что у ветки
    # минимального срока, поэтому и ветка одна: окно длиной min_stay (по
    # умолчанию одна ночь — тогда клетка значит ровно «ночь свободна»).
    if params.get("grid_source") == "rooms" and params.get("room_types"):
        return _probe_bnovo_min_stay(
            username, recipe, date_from, date_to, fetch, text_fetch,
            min_stay if has_min_stay else 1, params["room_types"],
            inventory_date_to)
    if has_min_stay and params.get("room_types"):
        return _probe_bnovo_min_stay(username, recipe, date_from, date_to,
                                     fetch, text_fetch, min_stay,
                                     params["room_types"], inventory_date_to)
    # Минимум есть, а категорий в рецепте нет: ветка окон без них сетку не
    # построит, и остаётся обычный min_prices. Он у такого объекта отвечает
    # «можно ли НАЧАТЬ заезд», то есть красит занятыми и свободные ночи в
    # промежутках короче минимума. Раньше объект молчал об этом со статусом
    # ok и показывал почти 100% (тикет 07); теперь говорит вслух.
    min_stay_note = ""
    if has_min_stay:
        min_stay_note = (
            f"минимальный срок проживания {min_stay} ноч., а в рецепте нет "
            f"room_types: клетка показывает, можно ли НАЧАТЬ заезд в эту "
            f"ночь — свободные ночи в промежутках короче минимума считаются "
            f"занятыми (оценка сверху); нужна доразведка категорий")
    # Глубина бесплатна: тот же ОДИН min_prices на категорию отдаёт весь
    # запрошенный диапазон — dto расширяется минимум до 2027-03 (ревью
    # 14.08), сводка глубину не показывает, она живёт в снапшоте. За окном
    # продаж ночи приходят null и читаются busy — оценка сверху (тикет 03).
    deep_to = deep_date_to(date_to)
    dates = _iso_dates(date_from, deep_to)
    room_types = params.get("room_types") or {}
    if room_types:
        targets = [(str(rid), name) for rid, name in room_types.items()]
        granularity = "per_unit"
    else:
        targets = [("", AGGREGATE_UNIT)]
        granularity = "aggregate"

    obj = _base_object(username, recipe, granularity)
    obj["grid_until"] = deep_to.isoformat()
    # Граница ФОНДА объявляется ДО шага фонда и остаётся, даже если шага не
    # будет (inventory=False, рецепт без room_types, обрыв ветки). Молчание
    # поля сводка читает как «фонд снят везде» (build_summary.
    # _month_inventory_gap), и объект без единой ночи фонда выглядел как
    # объект с полным фондом. Пол — date_from: ни одной ночи не спрошено.
    obj["inventory_until"] = date_from.isoformat()
    failures: list[str] = [min_stay_note] if min_stay_note else []
    broken: Optional[str] = None
    refused: Optional[str] = None
    refusal_status: Optional[int] = None
    for room_id, unit_name in targets:
        url = request["url_template"].format(
            uid=params.get("uid", ""),
            date_from=_ddmmyyyy(date_from), date_to=_ddmmyyyy(deep_to),
            room_type_id=room_id)
        try:
            status, data = fetch(url, headers)
        except AccessRefused as e:
            # Транспорт уже понял, что нас не пустили (например, хост просит
            # ждать дольше, чем прогон может себе позволить). Дальше по
            # категориям не идём: тот же хост, тот же ответ.
            refused, refusal_status = e.reason, e.status
            obj["units"][unit_name] = {d: {"state": "unknown"} for d in dates}
            break
        except OSError as e:
            failures.append(f"{unit_name}: сетевой сбой ({e})")
            obj["units"][unit_name] = {d: {"state": "unknown"} for d in dates}
            continue
        obj["source_urls"].append(url)
        verdict = classify_response(status, data is not None, unit_name,
                                    hint=_BROKEN_HINT,
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
            obj["units"][unit_name] = parse_min_prices(data, dates)
        except SchemaChanged as e:
            broken = f"{unit_name}: {e}"
            break

    # Фонд типов: min_prices знает только «продаётся ли хоть один номер
    # категории», а категория «Этнодом» у a_ureki держит минимум три (замер
    # 15.08). Остатки снимаем отдельным запросом на ночь горизонта сводки.
    if inventory and not broken and not refused and room_types:
        room_names = {str(rid): name for rid, name in room_types.items()}
        # Ночи фонда — по СВОЕМУ горизонту (тикет 06): один запрос на ночь,
        # и на годовой сетке это 366 запросов к одному хосту на объект.
        fund_until = _inventory_until(date_from, date_to, inventory_date_to)
        obj["inventory_until"] = fund_until.isoformat()
        nights = [d for d in _iso_dates(date_from, fund_until)
                  if any(cells.get(d, {}).get("state") in ("free", "busy")
                         for cells in obj["units"].values())]
        # fatal здесь не смотрим нарочно: фонд типов — шаг вспомогательный,
        # не снялся -> объект считается по типам, рецепт жив.
        by_night, inv_failures, _ = _collect_bnovo_inventory(
            recipe, nights, fetch, text_fetch, obj)
        if any(rest for rest in by_night.values()):
            _, marked = apply_inventory(obj["units"], room_names, by_night)
            if not marked:
                failures.append("остатки номеров сняты, но ни одна клетка не "
                                "сошлась с календарём — объект считан по типам")
        elif by_night and any(cell.get("state") == "free"
                              for cells in obj["units"].values()
                              for cell in cells.values()):
            # Ни одного остатка за весь горизонт при живых свободных ночах —
            # движок счётчик не отдаёт. Не поломка, но объект считается по
            # типам, и читатель должен знать причину.
            failures.append("фонд типов не снят: движок не отдал ни одного "
                            "остатка номеров — объект считан по типам")
        if inv_failures:
            failures.append("фонд типов снят частично: "
                            + "; ".join(inv_failures[:3])
                            + (f" и ещё {len(inv_failures) - 3}"
                               if len(inv_failures) > 3 else ""))
    return _finish(obj, failures, broken, refused=refused,
                   refusal_status=refusal_status)


# ---------------------------------------------------------------------------
# Вариант (2): TL Integration (ApiWebDistribution)
# ---------------------------------------------------------------------------

def parse_hotel_info(payload: object) -> tuple[dict, int]:
    """hotel_info -> ({код категории: имя}, окно продаж в днях от сегодня)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("hotels"), list) \
            or not payload["hotels"]:
        raise SchemaChanged("hotel_info без hotels[] — схема TL API сменилась")
    hotel = payload["hotels"][0]
    room_types = hotel.get("room_types")
    if not isinstance(room_types, list) or not room_types:
        raise SchemaChanged("hotel_info без room_types — схема TL API сменилась")
    names: dict[str, str] = {}
    for rt in room_types:
        code = str(rt.get("code", ""))
        if not code:
            raise SchemaChanged("категория hotel_info без code")
        names[code] = str(rt.get("name") or code)
    # дубли имён различаем кодом — юнит в сетке должен быть уникален
    seen: dict[str, int] = {}
    for code, name in names.items():
        seen[name] = seen.get(name, 0) + 1
    names = {code: (name if seen[name] == 1 else f"{name} [{code}]")
             for code, name in names.items()}
    max_rule = (hotel.get("booking_rules") or {}).get("availability_max_date") or {}
    unit_days = _TIME_UNIT_DAYS.get(max_rule.get("time_unit"), 366)
    window_days = int(max_rule.get("duration", 1) or 1) * unit_days
    return names, window_days


# Окно запроса обоих календарей TL. Пределы диапазона у них разные, и оба
# эндпоинта на длинный диапазон не падают, а отвечают HTTP 200 с отказом:
# - hotel_booking_rules — замер 04.09 на живом отеле 11269 (istra-cottage):
#   57 и 91 сутки -> booking_rules; 181 и 365 суток -> {"errors": ...} БЕЗ
#   booking_rules. Парсер читал это как «схема сменилась», и после тикета 01
#   (горизонт 12 месяцев вместо 58 суток) все 14 рецептов travelline разом
#   отдали insufficient_data;
# - room_type_availability_2 — замер 05.09 00:05 там же: 366 суток ->
#   {"room_type_availability": [], "errors": [{"error_code": "320", "message":
#   "max 360 days period is allowed in single request"}]}; 91 сутки -> 137 КБ
#   живого календаря. Пустой список парсер читал как «ни одной свободной
#   ночи», booking_rules поверх красил запретные ночи busy — сетка без единой
#   свободной клетки (один TL-объект 05.09 00:00: free 0 при 464 утром).
# Режем ОБА окнами по 90 суток и склеиваем: одно окно на оба календаря
# проще двух порогов, а лишние четыре GET на объект в год стоят ~5 с.
RULES_WINDOW_DAYS = 90


def _rules_windows(date_from: date, date_to: date) -> list[tuple[date, date]]:
    """Диапазон -> окна по RULES_WINDOW_DAYS суток (последнее короче).

    Одни и те же окна для hotel_booking_rules и room_type_availability_2:
    границы ночей у обоих совпадают (end_date — включённая ночь), и склейка
    идёт без дыр и нахлёстов.
    """
    windows = []
    cur = date_from
    while cur <= date_to:
        end = min(cur + timedelta(days=RULES_WINDOW_DAYS - 1), date_to)
        windows.append((cur, end))
        cur = end + timedelta(days=1)
    return windows


def parse_booking_rules(payload: object) -> set[str]:
    """hotel_booking_rules -> даты forbidden=true (агрегатный календарь)."""
    if not isinstance(payload, dict) or "booking_rules" not in payload:
        raise SchemaChanged("нет booking_rules — схема календаря TL сменилась")
    forbidden = set()
    for rule in payload["booking_rules"] or []:
        if isinstance(rule, dict) and rule.get("forbidden") and rule.get("date"):
            forbidden.add(str(rule["date"]))
    return forbidden


def parse_hotel_availability(payload: object) -> dict:
    """hotel_availability -> {код категории: остаток номеров | None}.

    None = категория свободна, но счётчик скрыт (TL показывает
    limited_inventory_count, только когда свободных мало) — фонд такой ночи
    считать нельзя, клетка останется бинарной. Категории, которой в ответе
    нет вовсе, в словаре нет: её остаток = 0 (продана целиком), и это
    решает вызывающий, сверяясь с календарём.
    """
    if not isinstance(payload, dict) or "room_stays" not in payload:
        raise SchemaChanged(
            "в ответе нет room_stays — схема hotel_availability сменилась")
    rest: dict[str, Optional[int]] = {}
    for stay in payload["room_stays"] or []:
        if not isinstance(stay, dict):
            raise SchemaChanged("room_stays содержит не-объект")
        for rt in stay.get("room_types") or []:
            if not isinstance(rt, dict) or not rt.get("code"):
                raise SchemaChanged("room_stay без code категории")
            code = str(rt["code"])
            left = rt.get("limited_inventory_count")
            value = left if isinstance(left, int) and not isinstance(left, bool) \
                else None
            if code not in rest:
                rest[code] = value
            elif rest[code] is None or (value is not None and value > rest[code]):
                # несколько тарифов на одну категорию: берём наибольший
                # известный остаток — это и есть «сколько номеров свободно»
                rest[code] = value if value is not None else rest[code]
    return rest


def _inventory_body(hotel_code: str, night: date) -> dict:
    """Тело hotel_availability на ОДНУ ночь. adults=1 — минимальный состав:
    больший состав отрезал бы малые категории по вместимости и они читались
    бы как проданные."""
    return {
        "criterions": [{
            "hotels": [{"code": hotel_code}],
            "start_date": night.isoformat(),
            "end_date": (night + timedelta(days=1)).isoformat(),
            "guests": [{"adults": 1, "children": []}],
        }],
        "language": "ru-ru",
        "currency": "RUB",
    }


def _collect_inventory(base: str, hotel_code: str, headers: dict,
                       nights: list[str], fetch: Callable,
                       obj: dict) -> tuple[dict, list[str]]:
    """Остатки номеров по ночам: {ночь: {код: остаток|None}} + список сбоев.

    Один POST на ночь — дешевле у TL не отдают: календарь категорий про
    количество номеров не знает вовсе (проверено 15.08: параметры rooms=N
    он игнорирует). Шаг НЕ ломает рецепт: не снялось — объект остаётся
    посчитанным по типам, а сводка это честно пишет.
    """
    url = f"{base}/ApiWebDistribution/BookingForm/hotel_availability"
    by_night: dict[str, dict] = {}
    failures: list[str] = []
    for night in nights:
        body = _inventory_body(hotel_code, date.fromisoformat(night))
        try:
            status, data = fetch(url, headers, body)
        except AccessRefused as e:
            failures.append(f"остатки: {e.reason}")
            break
        except OSError as e:
            failures.append(f"остатки на {night}: сетевой сбой ({e})")
            continue
        verdict = classify_response(status, data is not None,
                                    f"остатки на {night}",
                                    body_text=_body_text(fetch))
        if verdict.kind == "refused":
            # Шаг вспомогательный (рецепт им не ломается), но долбиться в
            # хост, который нас не пускает, нельзя — обрываем шаг.
            failures.append(verdict.reason)
            break
        if verdict.kind != "ok":
            failures.append(verdict.reason)
            continue
        try:
            by_night[night] = parse_hotel_availability(data)
        except SchemaChanged as e:
            failures.append(f"остатки на {night}: {e}")
            break
    if any(rest for rest in by_night.values()):
        obj["source_urls"].append(f"{url}#по ночам, POST")
    return by_night, failures


def apply_inventory(units_by_code: dict, room_names: dict,
                    by_night: dict) -> tuple[dict, int]:
    """Проставить фонд в сетку по снятым остаткам. -> (сетка, сколько клеток).

    Фонд категории = максимум наблюдённого остатка (оценка СНИЗУ: где счётчик
    скрыт, свободных было больше). Клетку размечаем, только когда остаток и
    календарь говорят одно и то же: календарь free и остаток > 0 -> занято
    capacity-остаток; календарь busy и категории в ответе нет -> заняты все.
    Расходятся (min-stay, ограничение по гостям) — клетка остаётся бинарной.
    """
    capacity: dict[str, int] = {}
    for rest in by_night.values():
        for code, left in rest.items():
            if isinstance(left, int) and left > capacity.get(code, 0):
                capacity[code] = left
    marked = 0
    for code, unit_name in room_names.items():
        cap = capacity.get(code)
        if not cap:
            continue
        cells = units_by_code.get(unit_name) or {}
        for night, rest in by_night.items():
            cell = cells.get(night)
            if cell is None:
                continue
            left = rest.get(code, 0)
            if cell["state"] == "free" and isinstance(left, int) and left > 0:
                cell["units_total"] = max(cap, left)
                cell["units_free"] = left
                marked += 1
            elif cell["state"] == "busy" and left == 0 and code not in rest:
                cell["units_total"] = cap
                cell["units_free"] = 0
                marked += 1
    return units_by_code, marked


def parse_room_type_availability(payload: object) -> dict:
    """room_type_availability_2 -> {код категории: {дата: мин. цена | None}}.

    В ответе по категории перечислены только ночи, где она свободна
    (несколько записей на ночь — разные тарифы/составы гостей, берём
    минимальную цену). Записи с is_available=false, если появятся,
    пропускаются. Не та форма -> SchemaChanged.

    Непустой errors -> WindowRejected: движок отверг диапазон (живой ответ
    05.09 на 366 суток — error 320, список категорий пустой). Пустой список
    БЕЗ errors — честное «свободных ночей нет», это разные вещи.
    """
    if not isinstance(payload, dict) or "room_type_availability" not in payload:
        raise SchemaChanged(
            "нет room_type_availability — схема календаря TL сменилась")
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        described = "; ".join(
            f"{e.get('error_code', '?')}: {e.get('message', '')}".strip(": ")
            if isinstance(e, dict) else str(e) for e in errors[:3])
        raise WindowRejected(f"движок отверг диапазон окна ({described})")
    result: dict[str, dict] = {}
    for rta in payload["room_type_availability"] or []:
        if not isinstance(rta, dict) or "id_room_type" not in rta:
            raise SchemaChanged("запись календаря TL без id_room_type")
        code = str(rta["id_room_type"])
        nights = result.setdefault(code, {})
        for entry in rta.get("availability_date") or []:
            if not isinstance(entry, dict):
                raise SchemaChanged("availability_date содержит не-объект")
            if entry.get("is_available") is False:
                continue
            period = entry.get("period")
            if not isinstance(period, dict) or "start_date" not in period:
                raise SchemaChanged(
                    "запись календаря TL без period.start_date — ждали "
                    "aggregate_dates=false")
            night = str(period["start_date"])
            price = (entry.get("price") or {}).get("price_before_tax")
            if not isinstance(price, (int, float)):
                price = None
            prev = nights.get(night)
            if night not in nights or (price is not None
                                       and (prev is None or price < prev)):
                nights[night] = price
    return result


def _tl_base(url_template: str) -> str:
    parsed = urllib.parse.urlsplit(url_template)
    return f"{parsed.scheme}://{parsed.netloc}"


def _probe_tl_api(username: str, recipe: dict, date_from: date,
                  date_to: date, fetch: Callable, inventory: bool = True,
                  inventory_date_to: Optional[date] = None):
    """Обёртка над телом ветки: отказ хоста -> refusal, а не broken.

    Объект собирается внутри _tl_api_body и передаётся сюда исключением,
    чтобы уже снятые клетки не пропали: отказ на третьем шаге не должен
    стирать сетку, снятую первыми двумя.
    """
    try:
        return _tl_api_body(username, recipe, date_from, date_to, fetch,
                            inventory, inventory_date_to)
    except _TlRefused as e:
        return _finish(e.obj, e.failures, None, refused=e.reason,
                       refusal_status=e.status)


class _TlRefused(Exception):
    """Нас не пустили посреди ветки TL API: несёт объект и накопленные сбои."""

    def __init__(self, obj: dict, failures: list, reason: str,
                 status: Optional[int]):
        super().__init__(reason)
        self.obj = obj
        self.failures = failures
        self.reason = reason
        self.status = status


def _tl_api_body(username: str, recipe: dict, date_from: date,
                 date_to: date, fetch: Callable, inventory: bool = True,
                 inventory_date_to: Optional[date] = None):
    request = recipe["request"]
    params = request.get("params", {})
    headers = request.get("headers", {})
    hotel_code = str(params.get("hotel_code", ""))
    base = _tl_base(request["url_template"])
    obj = _base_object(username, recipe, "per_unit")
    failures: list[str] = []

    def get_json(url: str, what: str):
        """(data | None, fatal_reason | None, network_reason | None).

        Отказ хоста (403/429/заслон) наружу не возвращается, а поднимается
        исключением _TlRefused: у этой ветки три обязательных запроса подряд
        к одному хосту, и если нас не пустили на первом — остальные два
        отправлять незачем. Ловит его один except на весь пробник.
        """
        try:
            status, data = fetch(url, headers)
        except AccessRefused as e:
            raise _TlRefused(obj, failures, e.reason, e.status) from e
        except OSError as e:
            return None, None, f"{what}: сетевой сбой ({e})"
        obj["source_urls"].append(url)
        verdict = classify_response(status, data is not None, what,
                                    hint=_BROKEN_HINT,
                                    body_text=_body_text(fetch))
        if verdict.kind == "refused":
            raise _TlRefused(obj, failures, verdict.reason, status)
        if verdict.kind == "broken":
            return None, verdict.reason, None
        if verdict.kind == "network":
            return None, None, verdict.reason
        return data, None, None

    # 1. Справочник категорий и окно продаж — без него сетка не строится.
    info_url = (f"{base}/ApiWebDistribution/BookingForm/hotel_info"
                f"?hotels[0].code={hotel_code}&language=ru-ru")
    data, fatal, net_fail = get_json(info_url, "hotel_info")
    if fatal or net_fail:
        return _finish(obj, [net_fail] if net_fail else [], fatal)
    try:
        room_names, window_days = parse_hotel_info(data)
    except SchemaChanged as e:
        return _finish(obj, [], str(e))
    sales_until = date.today() + timedelta(days=window_days)

    # 2. Поюнитный календарь: ночи каждой категории с ценами, окнами по
    #    RULES_WINDOW_DAYS (годовой диапазон движок отвергает, см. константу)
    #    со склейкой по категориям. Смена схемы или 4xx на любом окне
    #    фатальны — это про API, а не про диапазон. Окно, которое не снялось
    #    (сеть, отказ диапазона), НЕ красит ночи: они уходят в unknown, и
    #    объект честно partial. Ни одного снятого окна — сетки нет.
    by_code: dict[str, dict] = {}
    failed_nights: set[str] = set()
    got_any = False
    for win_from, win_to in _rules_windows(date_from, date_to):
        calendar_url = request["url_template"].format(
            hotel_code=hotel_code,
            date_from=win_from.isoformat(), date_to=win_to.isoformat())
        what = (f"календарь категорий {win_from.isoformat()}.."
                f"{win_to.isoformat()}")
        data, fatal, net_fail = get_json(calendar_url, what)
        if fatal:
            return _finish(obj, failures, fatal)
        if net_fail:
            failures.append(net_fail)
            failed_nights.update(_iso_dates(win_from, win_to))
            continue
        try:
            chunk = parse_room_type_availability(data)
        except WindowRejected as e:
            failures.append(f"{what}: {e}")
            failed_nights.update(_iso_dates(win_from, win_to))
            continue
        except SchemaChanged as e:
            return _finish(obj, failures, str(e))
        got_any = True
        for code, nights in chunk.items():
            merged = by_code.setdefault(code, {})
            for night, price in nights.items():
                prev = merged.get(night)
                if night not in merged or (price is not None
                                           and (prev is None or price < prev)):
                    merged[night] = price
    if not got_any:
        return _finish(obj, failures, None)

    # 3. Агрегатный календарь — только чтобы толковать ночи, пустые у ВСЕХ
    #    категорий (forbidden = проданы все). Сбой не фатален: такие ночи
    #    станут unknown.
    forbidden: Optional[set] = None
    got_any = False
    for win_from, win_to in _rules_windows(date_from, date_to):
        rules_url = (f"{base}/ApiWebDistribution/AvailabilityCalendar/"
                     f"hotel_booking_rules?start_date={win_from.isoformat()}"
                     f"&end_date={win_to.isoformat()}&hotel={hotel_code}"
                     f"&shared=false")
        data, fatal, net_fail = get_json(rules_url, "агрегатный календарь")
        if fatal:
            return _finish(obj, failures, fatal)
        if net_fail:
            failures.append(net_fail)
            continue
        try:
            chunk = parse_booking_rules(data)
        except SchemaChanged as e:
            return _finish(obj, failures, str(e))
        forbidden = chunk if forbidden is None else (forbidden | chunk)
        got_any = True
    if not got_any:
        forbidden = None

    # 4. Сетка: ночь есть у категории -> free (+цена); нет, но есть у другой
    #    -> busy; пустая у всех -> sales_not_open за окном продаж / busy по
    #    forbidden / иначе unknown. Ночь из окна, которое НЕ снялось, busy по
    #    forbidden не красится: календарь её не видел, и «проданы все» ничем
    #    не подтверждено — только unknown (или sales_not_open: окно продаж
    #    знает hotel_info, календарь тут ни при чём). Категории календаря
    #    сверяем со справочником: неизвестный код -> смена схемы.
    unknown_codes = set(by_code) - set(room_names)
    if unknown_codes:
        return _finish(obj, failures,
                       f"календарь вернул категории вне hotel_info: "
                       f"{sorted(unknown_codes)} — справочник разъехался")
    nights_alive = {d for nights in by_code.values() for d in nights}
    # ВАЖНО: календарь TL знает только «свободно ли хоть что-то в типе», а
    # типов вида «А-фрейм ×3» полно. Фонд снимаем отдельным шагом ниже.
    # Снапшот не режется по date_to: всё, что календарь отдал глубже
    # последнего окна, сохраняется (ревью 14.08; спека: «всё, что движок
    # отдаёт глубже, — в снапшот»).
    grid_to = date_to
    if nights_alive:
        grid_to = max(grid_to, date.fromisoformat(max(nights_alive)))
    dates = _iso_dates(date_from, grid_to)
    obj["grid_until"] = grid_to.isoformat()
    # Та же граница фонда, объявленная заранее (ревью волны 4): с
    # inventory=False шага фонда не будет вовсе, и поле обязано сказать это
    # вслух, а не промолчать.
    obj["inventory_until"] = date_from.isoformat()
    for code, unit_name in room_names.items():
        unit_nights = by_code.get(code, {})
        cells = {}
        for d in dates:
            if d in unit_nights:
                cells[d] = {"state": "free"}
                if unit_nights[d] is not None:
                    cells[d]["price"] = unit_nights[d]
            elif d in nights_alive:
                cells[d] = {"state": "busy"}
            elif date.fromisoformat(d) > sales_until:
                cells[d] = {"state": "sales_not_open"}
            elif d in failed_nights:
                cells[d] = {"state": "unknown"}
            elif forbidden is not None and d in forbidden:
                cells[d] = {"state": "busy"}
            else:
                cells[d] = {"state": "unknown"}
        obj["units"][unit_name] = cells

    # 5. Фонд типов: сколько номеров категории свободно каждую ночь. Один POST
    #    на ночь горизонта ФОНДА (тикет 06) — он короче горизонта сетки:
    #    на годовой сетке поночный шаг стоил бы 366 запросов на объект.
    if inventory:
        fund_until = _inventory_until(date_from, date_to, inventory_date_to)
        obj["inventory_until"] = fund_until.isoformat()
        nights = [d for d in _iso_dates(date_from, fund_until)
                  if any(cells.get(d, {}).get("state") in ("free", "busy")
                         for cells in obj["units"].values())]
        by_night, inv_failures = _collect_inventory(
            base, hotel_code, headers, nights, fetch, obj)
        if any(rest for rest in by_night.values()):
            _, marked = apply_inventory(obj["units"], room_names, by_night)
            if not marked:
                failures.append("остатки номеров сняты, но ни одна клетка не "
                                "сошлась с календарём — объект считан по типам")
        elif by_night and any(cell.get("state") == "free"
                              for cells in obj["units"].values()
                              for cell in cells.values()):
            # Ни одного остатка за весь горизонт при живых свободных ночах —
            # движок счётчик не отдаёт. Не поломка, но объект считается по
            # типам, и читатель должен знать причину.
            failures.append("фонд типов не снят: движок не отдал ни одного "
                            "остатка номеров — объект считан по типам")
        if inv_failures:
            # Фонд не снялся -> объект считается по типам: цифра занижена, но
            # честная. Рецепт при этом жив (шаг вспомогательный).
            failures.append("фонд типов снят частично: "
                            + "; ".join(inv_failures[:3])
                            + (f" и ещё {len(inv_failures) - 3}"
                               if len(inv_failures) > 3 else ""))
    return _finish(obj, failures, None)


# ---------------------------------------------------------------------------
# Вход пробника
# ---------------------------------------------------------------------------

def probe(username: str, recipe: dict, date_from: date, date_to: date,
          fetch: Optional[Callable] = None, inventory: bool = True,
          text_fetch: Optional[Callable] = None,
          inventory_date_to: Optional[date] = None):
    """Снять сетку по рецепту TravelLine. -> (obj, broken_reason | None).

    inventory=True — снимать ещё и фонд типов (сколько номеров категории
    свободно): без него занятость считается по типам и занижена там, где
    одинаковых домиков несколько. Стоит одного запроса на ночь горизонта.
    """
    if fetch is None:
        fetch = _make_fetch("get_json")
        if text_fetch is None:
            # страничный фолбэк остатков Bnovo — только в живом режиме
            text_fetch = _make_fetch("get_text")
    url = recipe.get("request", {}).get("url_template", "")
    if "reservationsteps" in url:
        return _probe_reservationsteps(username, recipe, date_from, date_to,
                                       fetch, inventory, text_fetch,
                                       inventory_date_to)
    if "ApiWebDistribution" in url:
        return _probe_tl_api(username, recipe, date_from, date_to, fetch,
                             inventory, inventory_date_to)
    obj = _base_object(username, recipe, "aggregate")
    reason = ("url_template рецепта не похож ни на один известный вариант "
              "TravelLine (reservationsteps / ApiWebDistribution)")
    obj["status"] = "insufficient_data"
    obj["reason"] = reason
    return obj, reason
