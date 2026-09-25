# -*- coding: utf-8 -*-
"""Пробник RC Bookings (СТАРЫЙ виджет RealtyCalendar): рецепт -> сетка по домикам.

Второе семейство API того же вендора, что и homereserve (realtycalendar.ru),
но другого поколения и другой механики — поэтому отдельный модуль, а не
ветка в probes/homereserve.py: общего у них только хост. Разведка 04.09 и
08.09.2026 (чистый HTTP, без браузера, zagorod-samara.ru).

Как это выглядит на сайте. Страница (Tilda) грузит
realtycalendar.ru/webpack/search.js и зовёт
RC_SMALL_BOOKINGS_WIDGET.init('<agency_token>') — это форма «даты + гости»,
которая уводит на realtycalendar.ru/booking-widget/<agency_token>; та страница
инициализирует RC_BOOKINGS_WIDGET.init(token, {apartment_ids, city_ids}) из
бандла assets.realtycalendar.ru/webpack/application.js. В бандле ровно
четыре читающих вызова (проверено по тексту бандла 08.09, других путей
/widgets/bookings/* в нём нет):

    GET /widgets/bookings/settings/{token}.json      -> агентство, правила заезда
    GET /widgets/bookings/search/{token}.json?humans=N&begin_date=ДД.ММ.ГГГГ
        &end_date=ДД.ММ.ГГГГ[&apartment_ids[]=..]   -> {"apartments": [...]}
    GET /widgets/bookings/apartments/{id}/price.json?token=..&begin_date=..
        &end_date=..&humans=..                       -> тариф на отрезок
    GET /widgets/bookings/terms?token=..             -> текст оферты

и группа /widgets/bookings/apartments/{id}/event_calendars{,/valid} (POST) —
это ОФОРМЛЕНИЕ БРОНИ, пробник её не зовёт никогда (запреты SKILL.md).
Посуточного календаря у этого API НЕТ: v2-эндпоинты соседа
(POST /v2/widget/{token}/{apartments,calendar}) на agency_token отвечают 404
(проверено 04.09), а price.json — это справочник тарифов, а не доступности:
на ночь, где домик занят (166851 на 08.09), он так же отдаёт цену 9000, как и
на свободную (проверено 08.09). Единственный источник занятости — search.

Что означает ответ search. Движок отвечает не «свободна ли ночь», а «какие
домики можно ЗАБРОНИРОВАТЬ на такой заезд»: домик, занятый хотя бы одну
ночь отрезка, из выдачи ПРОПАДАЕТ (166851 на 08-09.09 — есть в выдаче на
12.09, нет на 08.09). Пропасть он может и из-за минимального срока
проживания (та же ловушка, что у uhotels, 16.08), поэтому ночь снимается
несколькими окнами (params.spans, по умолчанию 1 и 2 ночи): домик, бывший
в выдаче хоть одного накрывшего ночь окна, — free; не бывший ни в одном при
хотя бы одном удачном окне — busy. Это та же ОЦЕНКА СВЕРХУ, что у всех
движков: занято, закрыто владельцем и минимум длиннее самого длинного окна
неотличимы. Цена в выдаче — за ночь, средняя по отрезку (12-13.09: 11000;
12-14.09: 10000 = (11000+9000)/2), в клетку идёт цена самого короткого окна.

Единица продажи — КОНКРЕТНЫЙ ДОМИК (apartment), как у homereserve: фонд
клетки всегда 1 и известен на всю сетку, флаг inventory запросов не делает.
Справочник домиков этот API не отдаёт (search показывает только доступное,
settings домиков не знает), поэтому его несёт РЕЦЕПТ: params.apartment_ids
(+ apartment_titles) — разведчик снимает союзом выдач на 2-3 пары дат.
Без него домик, занятый весь горизонт, невидим — снимок честно говорит об
этом в reason.

Глубина НЕ бесплатна: один запрос = одна ночь на все домики (не на домик —
у объекта на 3 домика это 1 запрос на окно). Поэтому, как у uhotels,
плотная сетка снимается только до горизонта ФОНДА (inventory_date_to, 45
ночей: 46 × 2 окна = 92 запроса ≈ 2,5 мин с паузой 1,2 с), а не на год.

Закрытое окно продаж — главная ловушка этого API. Пустая выдача на ночь
значит «ни один домик не бронируется» — и это либо все домики проданы, либо
календарь закрыт владельцем. У zagorod-samara.ru на 08.09 продажи обрываются
между 29.10 (только дом 3) и 01.11 (пусто до конца горизонта), то есть уже
через десять дней хвост 45-ночной сетки станет сплошным «busy» — фиктивные
100% ноября, ровно та выдумка, против которой писалось правило стены в
probes/__init__.py. Само правило здесь не сработает никогда: ему нужно 30
занятых ночей после 21-й, а сетка — 46 ночей. Поэтому пробник добывает
недостающее правилу свидетельство сам — ВЫБОРОЧНОЙ ПРОВЕРКОЙ ЗА СЕТКОЙ:
от границы сетки до конца горизонта один запрос окном 2 ночи каждые
SCAN_STEP_NIGHTS ночей (шаг взаимно прост с 7 — выборка гуляет по дням
недели, иначе попадала бы в одни субботы), ~36 запросов на годовой горизонт.
Хвост сетки без единого бронируемого домика переписывается в sales_not_open,
только если вместе с продолжением за сеткой он тянется не короче
SALES_WALL_MIN_NIGHTS, начинается не ближе SALES_WALL_FLOOR_DAYS от начала и
перед ним было SALES_WALL_MIN_LIVE_NIGHTS ночей живых продаж — пороги те же
самые, что у правила диспетчера, взяты оттуда импортом, а не скопированы.
Три домика, распроданные на 30+ ночей вперёд без единой дыры, — неправдоподобно;
три домика, закрытые на зиму, — обычное дело. Объект без живых продаж вовсе
не трогается: им занимается правило no_free_cells диспетчера («цифра не
показывается»).

Правила ошибок — единый каскад (транспорт probes/_common.py + правила исхода
probes/_outcome.py) с оговоркой uhotels: 4xx на ОТДЕЛЬНОМ окне рецепт не
ломает (сотня запросов подряд к одному хосту — первый кандидат на 429),
broken — только если не снялось ни одно окно или сменилась схема ответа.
Один запрос — одна попытка, пауза >= 1.2 с к хосту (очередь общая с
homereserve — хост один), ничего не бронируется, формы не отправляются.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Optional

from ._common import (AccessRefused, BudgetExceeded, SchemaChanged,
                      _base_object as _common_base, _iso_dates,
                      make_fetch as _make_fetch)
# Каскад и сборка объекта идут через правила ИСХОДА (probes/_outcome.py):
# 5xx рецепт не ломает никогда, а отказ хоста поверх уже снятой сетки не
# заводит счётчик суток. Транспорт про судьбу рецепта не знает.
from ._outcome import classify_response, finish as _finish
# Окна съёма — та же механика, что у uhotels (ночь = отрезок заезда, ловим
# минимальный срок несколькими окнами). Реализация одна, не копия.
from .uhotels import windows

# Состав гостей минимальный: humans=1 показывает все домики (проверено 08.09:
# дом 3 на 9 мест виден с humans=1). Больший состав отрезал бы малые домики
# по вместимости, и они читались бы занятыми (грабли TL, тикет 08).
DEFAULT_HUMANS = 1

# Окна съёма в ночах: 1 — «ночь свободна сама по себе», 2 — обходит минимум
# выходного дня. Переопределяется рецептом (params.spans).
DEFAULT_SPANS = (1, 2)
MAX_SPANS = 4  # предохранитель: окна множатся на число ночей горизонта

# Выборочная проверка за сеткой: один запрос окном SCAN_WINDOW_NIGHTS ночей
# каждые SCAN_STEP_NIGHTS ночей. Шаг 9 взаимно прост с 7: выборка обходит все
# дни недели, а не бьёт в одну субботу. Окно 2 ночи — чтобы минимальный срок
# выходного не читался как «ничего не бронируется».
SCAN_STEP_NIGHTS = 9
SCAN_WINDOW_NIGHTS = 2

# Имя правила в sales_window/relabeled_by — сводка отличает по нему ночь,
# размеченную пробником по выборочной проверке, от размеченной диспетчером.
CLOSED_HORIZON_RULE = "closed_horizon"

_BROKEN_HINT = "похоже на смену API виджета bookings RealtyCalendar или антибот"

# Версия РАЗБОРА движка (поле probe_version снапшота).
PROBE_VERSION = 1


def _body_text(fetch) -> str:
    """Тело последнего ответа fetch — им опознаётся страница-заслон.

    Импорт ленивый: пакет probes грузит этот модуль сам, и импорт на уровне
    модуля замкнул бы круг. Реализация одна на все движки — probes.body_text_of.
    """
    from . import body_text_of
    return body_text_of(fetch)


def _wall_thresholds() -> tuple[int, int, int]:
    """(мин. ночей стены, отступ от начала, ночей живых продаж до стены).

    Пороги правила стены живут в диспетчере (probes/__init__.py) — берём их
    оттуда, чтобы у завода не завелось второй, чуть другой копии того же
    суждения. Импорт ленивый по той же причине, что у _body_text.
    """
    from . import (SALES_WALL_FLOOR_DAYS, SALES_WALL_MIN_LIVE_NIGHTS,
                   SALES_WALL_MIN_NIGHTS)
    return (SALES_WALL_MIN_NIGHTS, SALES_WALL_FLOOR_DAYS,
            SALES_WALL_MIN_LIVE_NIGHTS)


def ru_date(d: date) -> str:
    """Дата в формате виджета: ДД.ММ.ГГГГ (helpers/date.js бандла, #DD.#MM.#YYYY)."""
    return f"{d.day:02d}.{d.month:02d}.{d.year:04d}"


def build_url(template: str, token: str, humans: int, start: date,
              span: int) -> str:
    """URL search на окно: заезд start, выезд start+span, состав humans.

    Подстановка — заменой плейсхолдеров рецепта, не str.format: в шаблоне
    могут стоять чужие фигурные скобки. {guests} и {humans} — синонимы: в
    ссылке кнопки сайта параметр зовётся guests, в API — humans.
    """
    out = template
    for name, value in (("{token}", token), ("{guests}", str(humans)),
                        ("{humans}", str(humans)),
                        ("{date_from}", ru_date(start)),
                        ("{date_to}", ru_date(start + timedelta(days=span)))):
        out = out.replace(name, value)
    return out


def parse_search(payload: object) -> dict[str, dict]:
    """Ответ search -> {id домика: {"title", "price"?}} — только БРОНИРУЕМЫЕ.

    Домика, который на этот отрезок продавать нельзя (занят, закрыт, короче
    минимума), в ответе нет вовсе — что это значит, решает вызывающий.
    price — за ночь, средняя по отрезку (см. докстринг модуля). Не та форма
    -> SchemaChanged.
    """
    if not isinstance(payload, dict) or not isinstance(
            payload.get("apartments"), list):
        raise SchemaChanged("в ответе нет списка apartments — схема виджета "
                            "bookings RealtyCalendar сменилась")
    out: dict[str, dict] = {}
    for item in payload["apartments"]:
        if not isinstance(item, dict) or item.get("id") in (None, ""):
            raise SchemaChanged("запись apartments без id домика")
        apartment_id = str(item["id"])
        entry = {"title": str(item.get("title") or "").strip()}
        price = item.get("price")
        if isinstance(price, (int, float)) and not isinstance(price, bool) \
                and price > 0:
            entry["price"] = price
        out[apartment_id] = entry
    return out


def _spans(params: dict) -> tuple:
    """Длины окон из рецепта, с предохранителем. Мусор -> умолчание."""
    raw = params.get("spans")
    if not isinstance(raw, (list, tuple)) or not raw:
        return DEFAULT_SPANS
    clean = sorted({int(x) for x in raw
                    if isinstance(x, int) and not isinstance(x, bool)
                    and 1 <= x <= 30})
    return tuple(clean[:MAX_SPANS]) or DEFAULT_SPANS


def _humans(params: dict) -> int:
    raw = params.get("humans", params.get("guests"))
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    return DEFAULT_HUMANS


def merge_window(free: dict, prices: dict, seen: dict, bookable: dict,
                 covered: list, span: int) -> None:
    """Влить выдачу одного окна в накопители (in-place).

    free[ночь] — множество id домиков, бронируемых хоть одним накрывшим ночь
    окном (объединение: каждое окно — нижняя оценка свободного). Ночь
    заводится в free даже пустым окном: «спросили, никого» — это busy, а
    «не спрашивали» — unknown. Цена окна покороче вытесняет цену подлиннее:
    однночная точная, двухночная — средняя по двум ночам.
    """
    for night in covered:
        ids = free.setdefault(night, set())
        price_by_id = prices.setdefault(night, {})
        for apartment_id, entry in bookable.items():
            ids.add(apartment_id)
            if "price" in entry:
                best = price_by_id.get(apartment_id)
                if best is None or span < best[0]:
                    price_by_id[apartment_id] = (span, entry["price"])
    for apartment_id, entry in bookable.items():
        seen.setdefault(apartment_id, entry["title"])


def _directory(params: dict, seen: dict) -> tuple[list[tuple[str, str]], str]:
    """Справочник домиков -> ([(id, имя)], оговорка | '').

    Рецепт первичен: только он знает домик, занятый весь горизонт (search
    его не покажет), и только его имена стабильны от снимка к снимку — живой
    title берётся лишь там, где рецепт имени не дал, иначе домик, ни разу не
    бывший свободным, получал бы имя по id, а на следующий день — живое, и
    ряд по юниту рвался бы. Без apartment_ids в рецепте берётся то, что
    показала выдача, — с честной оговоркой.
    """
    titles = params.get("apartment_titles") or {}
    if not isinstance(titles, dict):
        titles = {}
    wanted = [str(x) for x in (params.get("apartment_ids") or [])]
    if wanted:
        pairs = []
        for apartment_id in wanted:
            name = str(titles.get(apartment_id) or "").strip() \
                or str(seen.get(apartment_id) or "").strip() \
                or f"Домик {apartment_id}"
            pairs.append((apartment_id, name))
        return pairs, ""
    pairs = [(apartment_id, str(titles.get(apartment_id) or title or "").strip()
              or f"Домик {apartment_id}")
             for apartment_id, title in seen.items()]
    note = ("справочник домиков в рецепте не задан (params.apartment_ids): "
            "видны только домики, хоть раз бывшие свободными в горизонте")
    return pairs, note


def _unit_names(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """[(id, имя)] -> {id: уникальное имя юнита} (дубли различаем id)."""
    seen: dict[str, int] = {}
    for _, name in pairs:
        seen[name] = seen.get(name, 0) + 1
    used: set[str] = set()
    names: dict[str, str] = {}
    for apartment_id, name in pairs:
        unit = name if seen[name] == 1 else f"{name} [{apartment_id}]"
        while unit in used:
            unit = f"{unit} [{apartment_id}]"
        used.add(unit)
        names[apartment_id] = unit
    return names


def build_units(free: dict, prices: dict, pairs: list, dates: list[str]) -> dict:
    """Накопители -> сетка units по SCHEMA (б).

    Ночь, по которой не снялось ни одно окно, остаётся unknown у всех
    домиков и в знаменатель занятости не идёт. Домик — физический дом:
    фонд клетки 1, свободно 1 или 0.
    """
    names = _unit_names(pairs)
    units: dict[str, dict] = {}
    for apartment_id, unit in names.items():
        cells: dict[str, dict] = {}
        for night in dates:
            ids = free.get(night)
            if ids is None:
                cells[night] = {"state": "unknown"}
                continue
            if apartment_id in ids:
                cell = {"state": "free", "units_total": 1, "units_free": 1}
                priced = (prices.get(night) or {}).get(apartment_id)
                if priced is not None:
                    cell["price"] = priced[1]
            else:
                cell = {"state": "busy", "units_total": 1, "units_free": 0}
            cells[night] = cell
        units[unit] = cells
    return units


def scan_starts(after: date, until: date, step: int = SCAN_STEP_NIGHTS) -> list[date]:
    """Заезды выборочной проверки: after+1, дальше каждые step ночей до until."""
    out = []
    start = after + timedelta(days=1)
    while start <= until:
        out.append(start)
        start += timedelta(days=step)
    return out


def closed_horizon(free: dict, dates: list[str], scan: list[tuple],
                   origin: date, horizon: date) -> Optional[tuple[str, str]]:
    """Хвост сетки без бронируемых домиков, подтверждённый проверкой за сеткой.

    -> (с какой ночи переписывать, до какой ночи продажи закрыты по проверке)
    или None. scan — [(заезд ISO, бронируемые id | None при сбое), ...] по
    возрастанию. Условия — те же, что у правила стены диспетчера, порогами
    оттуда же: хвост начинается не ближе floor ночей от начала сетки, перед
    ним есть live ночей с бронируемыми домиками, а стена (хвост плюс пустое
    продолжение за сеткой до первой бронируемой выборки или до горизонта) не
    короче min_nights. Любая неснятая ночь в хвосте — не догадываемся:
    снимок неполный. Сбой в продолжении режет свидетельство на последней
    удачной выборке, а не дотягивает его до горизонта.
    """
    min_nights, floor_days, live_min = _wall_thresholds()
    if not dates:
        return None
    asked = [n for n in dates if free.get(n) is not None]
    if not asked:
        return None
    live = [n for n in dates if free.get(n)]
    if not live:
        return None                      # объект без продаж — не догадываемся
    tail = [n for n in dates if n > live[-1]]
    if not tail or any(free.get(n) is None for n in tail):
        return None
    floor = (origin + timedelta(days=floor_days)).isoformat()
    since = max(tail[0], floor)
    if since > dates[-1]:
        return None
    if len([n for n in live if n < since]) < live_min:
        return None
    closed_until = None
    for start, ids in scan:
        if ids is None:
            break                        # сбой: свидетельство кончается здесь
        if ids:
            closed_until = (date.fromisoformat(start)
                            - timedelta(days=1)).isoformat()
            break
        closed_until = start
    else:
        if scan:
            closed_until = horizon.isoformat()
    if closed_until is None:
        closed_until = dates[-1]         # за сеткой не проверяли/не вышло
    wall = (date.fromisoformat(closed_until)
            - date.fromisoformat(since)).days + 1
    if wall < min_nights:
        return None
    return since, closed_until


def relabel_closed(units: dict, since: str) -> tuple[int, int]:
    """busy-клетки с ночи since -> sales_not_open (форма как у диспетчера).

    Фонд — физическое свойство домика, пара units_total/units_free
    переписывается целиком (этого требует схема), в знаменатель занятости
    sales_not_open не идёт. Цена не переносится: она относилась к прочтению
    «занято».
    """
    changed = 0
    touched: set[str] = set()
    for cells in units.values():
        for night, cell in cells.items():
            if night < since or cell.get("state") != "busy":
                continue
            fresh = {"state": "sales_not_open", "relabeled_from": "busy",
                     "relabeled_by": CLOSED_HORIZON_RULE}
            total = cell.get("units_total")
            if isinstance(total, int) and not isinstance(total, bool) \
                    and total > 0:
                fresh["units_total"] = total
                fresh["units_free"] = 0
            cells[night] = fresh
            changed += 1
            touched.add(night)
    return changed, len(touched)


def _with_note(result: tuple, note: str) -> tuple:
    """Дописать информационную пометку в reason, не трогая статус объекта.

    Обрезанный горизонт, выборочная проверка за сеткой, размеченный хвост —
    это не «снимок неполный»: снято ровно то, что спрашивали. Через failures
    пометка сделала бы объект partial, и ярлык строки в сводке менялся бы
    без единого изменения в данных.
    """
    obj, broken = result
    if note and not broken:
        obj["reason"] = "; ".join(x for x in (obj.get("reason"), note) if x)
    return obj, broken


def _trim(failures: list[str], keep: int = 3) -> str:
    return "; ".join(failures[:keep]) + (
        f" и ещё {len(failures) - keep}" if len(failures) > keep else "")


def probe(username: str, recipe: dict, date_from: date, date_to: date,
          fetch: Optional[Callable] = None, inventory: bool = True,
          inventory_date_to: Optional[date] = None):
    """Снять сетку по рецепту виджета bookings. -> (obj, broken_reason | None).

    inventory принимается для единообразия диспетчера и запросов не
    добавляет: apartment — конкретный домик, фонд клетки 1 на всю сетку.

    inventory_date_to (тикет 06) режет здесь и СЕТКУ, как у uhotels: ночь
    сетки и есть запрос, годовой горизонт стоил бы 366 × окна запросов к
    одному хосту. За границей клеток не добиваем: объект называет её сам
    (grid_until, inventory_until) и дописывает, что нашла выборочная
    проверка за сеткой (sales_scan).
    """
    if fetch is None:
        fetch = _make_fetch("get_json")
    request = recipe.get("request", {})
    params = request.get("params", {})
    headers = request.get("headers", {})
    obj = _base_object(username, recipe)
    token = str(params.get("token") or "").strip()
    if not token:
        return _finish(obj, [], "в рецепте нет params.token — нечем звать "
                                "виджет bookings RealtyCalendar")
    template = request.get("url_template", "")
    if not template:
        return _finish(obj, [], "в рецепте нет request.url_template")
    if "{date_from}" not in template or "{date_to}" not in template:
        return _finish(obj, [], "в request.url_template нет подстановок "
                                "{date_from}/{date_to} — окно дат не задать")
    humans = _humans(params)
    spans = _spans(params)

    fund_until = date_to if inventory_date_to is None \
        else max(date_from, min(date_to, inventory_date_to))
    obj["inventory_until"] = fund_until.isoformat()
    obj["grid_until"] = fund_until.isoformat()
    dates = _iso_dates(date_from, fund_until)
    obj["source_urls"].append(
        f"{template}#окна {'+'.join(str(s) for s in spans)} ноч. на каждую "
        f"ночь, GET")
    note_cut = ("" if fund_until >= date_to else
                f"сетка снята до {fund_until.isoformat()} (горизонт фонда): у "
                f"этого движка каждая ночь — отдельный запрос, дальше — "
                f"только выборочная проверка")

    free: dict[str, set] = {}
    prices: dict[str, dict] = {}
    seen: dict[str, str] = {}
    failures: list[str] = []
    ok_windows = 0
    fatal_seen = 0
    refused: Optional[str] = None
    refusal_status: Optional[int] = None
    budget_out = False

    def ask(start: date, span: int, what: str):
        """Одно окно -> (bookable | None, 'stop' | None). Сбои — в failures."""
        nonlocal refused, refusal_status, fatal_seen, budget_out
        url = build_url(template, token, humans, start, span)
        try:
            status, data = fetch(url, headers)
        except BudgetExceeded as e:
            # Бюджет объекта — наше решение, а не отказ хоста: дальше окон
            # не шлём, клетки остаются unknown, рецепт жив.
            failures.append(f"{what}: {e}")
            budget_out = True
            return None, "stop"
        except AccessRefused as e:
            refused, refusal_status = e.reason, e.status
            return None, "stop"
        except OSError as e:
            failures.append(f"{what}: сетевой сбой ({e})")
            return None, None
        verdict = classify_response(status, data is not None, what,
                                    hint=_BROKEN_HINT,
                                    body_text=_body_text(fetch))
        # Нас не пустили — окна дальше не идут: у этого пробника сотня
        # запросов подряд к одному хосту, и продолжать после 403/429 значит
        # ломиться в закрытую дверь. Рецепт при этом жив (тикет 03).
        if verdict.kind == "refused":
            refused, refusal_status = verdict.reason, status
            return None, "stop"
        # Прочие 4xx на ОТДЕЛЬНОМ окне рецепт не ломают. Приговор выносится
        # ниже, по итогу всего прогона: не снялось ни одного окна -> broken.
        if verdict.kind in ("broken", "network"):
            failures.append(verdict.reason)
            fatal_seen += 1 if verdict.kind == "broken" else 0
            return None, None
        try:
            return parse_search(data), None
        except SchemaChanged as e:
            # Схема — другое дело: тут повтор прогона не поможет.
            raise SchemaChanged(f"{what}: {e}") from None

    try:
        for start, span, covered in windows(dates, spans):
            bookable, stop = ask(date.fromisoformat(start), span,
                                 f"{start} на {span} ноч.")
            if stop:
                break
            if bookable is None:
                continue
            ok_windows += 1
            merge_window(free, prices, seen, bookable, covered, span)
    except SchemaChanged as e:
        return _finish(obj, failures, str(e))

    if refused and not ok_windows:
        return _with_note(_finish(obj, failures, None, refused=refused,
                                  refusal_status=refusal_status), note_cut)
    if not ok_windows:
        reason = ("ни одно окно не снялось: " + _trim(failures) if failures
                  else "движок не ответил ни на одно окно")
        # Рецепт ломаем ТОЛЬКО если хост отвергал запросы (4xx кроме
        # 403/429, не-JSON): там переразведка и правда нужна. Сеть, 5xx и
        # бюджет рецепт не трогают — нужен повтор прогона, не агент.
        return _with_note(
            _finish(obj, [] if fatal_seen else [reason],
                    reason if fatal_seen else None), note_cut)

    pairs, directory_note = _directory(params, seen)
    if directory_note:
        failures.append(directory_note)
    if not pairs:
        # Окна снялись, но домиков нет ни в одном и рецепт их не назвал:
        # модуль выключен, продаж нет или всё продано — изнутри ответа не
        # отличить. Это НЕ смена схемы, рецепт живой.
        failures.append("движок не отдал ни одного домика ни на одно окно "
                        "— модуль выключен, продаж нет или всё продано")
        return _with_note(_finish(obj, [_trim(failures)], None), note_cut)
    obj["units"] = build_units(free, prices, pairs, dates)

    # Выборочная проверка за сеткой: только если сетка снялась до конца, нас
    # не выгоняли и бюджет цел — иначе свидетельства о закрытых продажах
    # взять неоткуда, и хвост остаётся тем, что показал движок.
    scan: list[tuple] = []
    notes: list[str] = [note_cut] if note_cut else []
    if not refused and not budget_out and fund_until < date_to:
        starts = scan_starts(fund_until, date_to)
        scan_url = build_url(template, token, humans, starts[0],
                             SCAN_WINDOW_NIGHTS) if starts else ""
        if scan_url:
            obj["source_urls"].append(
                f"{scan_url}#выборочно за сеткой: каждые {SCAN_STEP_NIGHTS} "
                f"ноч. окном {SCAN_WINDOW_NIGHTS} ноч. до {date_to.isoformat()}")
        bookable_until = None
        samples = failed = 0
        try:
            for start in starts:
                what = f"выборка {start.isoformat()} на {SCAN_WINDOW_NIGHTS} ноч."
                bookable, stop = ask(start, SCAN_WINDOW_NIGHTS, what)
                if stop:
                    scan.append((start.isoformat(), None))
                    break
                if bookable is None:
                    failed += 1
                    scan.append((start.isoformat(), None))
                    continue
                samples += 1
                ids = set(bookable)
                scan.append((start.isoformat(), ids))
                if ids:
                    bookable_until = start.isoformat()
                for apartment_id, entry in bookable.items():
                    seen.setdefault(apartment_id, entry["title"])
        except SchemaChanged as e:
            return _finish(obj, failures, str(e))
        obj["sales_scan"] = {
            "from": starts[0].isoformat() if starts else None,
            "to": date_to.isoformat(),
            "step_nights": SCAN_STEP_NIGHTS,
            "window_nights": SCAN_WINDOW_NIGHTS,
            "samples": samples, "failed": failed,
            "bookable_until": bookable_until,
        }
        verdict = closed_horizon(free, dates, scan, date_from, date_to)
        if verdict:
            since, closed_until = verdict
            cells, touched = relabel_closed(obj["units"], since)
            obj["sales_window"] = {"rule": CLOSED_HORIZON_RULE, "since": since,
                                   "to_state": "sales_not_open",
                                   "relabeled_cells": cells,
                                   "relabeled_nights": touched,
                                   "closed_until": closed_until}
            notes.append(
                f"продажи закрыты с {since}: хвост сетки без единого "
                f"бронируемого домика продолжается за сеткой до "
                f"{closed_until} (выборочная проверка каждые "
                f"{SCAN_STEP_NIGHTS} ноч.) — размечен как закрытые продажи, "
                f"а не занятость")
        elif samples and bookable_until is None:
            notes.append(f"за сеткой бронируемых ночей не найдено "
                         f"(выборочно до {date_to.isoformat()})")
    if refused:
        # Отказ хоста ПОСЛЕ того, как окна уже отдали сетку, — конец
        # удачного съёма, а не «нас не пустили»: частичный съём с данными —
        # успех (правило 2 probes/_outcome.py), счётчик суток не двигается.
        failures.append(f"снято не до конца: {refused}")
    return _with_note(_finish(obj, [_trim(failures)] if failures else [],
                              None), "; ".join(notes))


def _base_object(username: str, recipe: dict) -> dict:
    return _common_base(username, recipe, "per_unit",
                        default_engine="rc-bookings")
