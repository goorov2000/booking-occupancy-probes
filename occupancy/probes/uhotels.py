# -*- coding: utf-8 -*-
"""Пробник UHotels (виджет artDg): рецепт -> сетка по категориям с фондом.

Разведка 16.08.2026 (чистый HTTP, без браузера). Сайт объекта грузит
api.uhotels.app/widget/booking2/loader.js и отдаёт ему токен отеля вида
"<id>:<hash>"; сам виджет — iframe на том же хосте, а данные берёт из
JSON-API https://api.uhotels.app/api/widget/v1/booking/ (jQuery $.ajax,
POST, без авторизации). Из его методов пробнику нужен ровно один:

    POST .../rooms {hotel, lang, currency, dateIn, dateOut, days,
                    rooms: [{adults, children}], roomType, tariff, coupon}
        -> [{id, code, name, places, price, max, ...}, ...]

Читающие соседи (не зовём, но знать полезно при переразведке): init —
справочник отеля и агрегатный calendarPrice по месяцам; calendar и
roomRateCalendar — ценовые календари. Метод save — оформление брони,
пробник его не трогает никогда (запреты SKILL.md).

Что означает ответ. Движок отвечает НЕ на вопрос «свободна ли ночь», а на
вопрос «можно ли ЗАБРОНИРОВАТЬ такой заезд»: max категории — сколько её
домиков доступно на ВЕСЬ запрошенный отрезок. Отсюда два следствия, на
которых первая редакция этого пробника (16.08, до аудита) сломалась:

* распроданную категорию движок оставляет в выдаче с max=0, а ПРОПАДАЕТ
  она, когда под запрошенный отрезок её продавать нельзя (минимальный срок,
  закрытый заезд). Читать пропажу как «продана целиком» нельзя;
* если минимум не выполняется НИ для одной категории, ответ — пустой
  список. Живой замер по pineriver_hotel: на 22.08 однночный запрос отдал
  0 категорий, а двухночный — 27 категорий и 28 свободных домиков. Первая
  редакция записала в ту субботу 180 занятых домиков из 180.

Поэтому ночь снимается НЕСКОЛЬКИМИ окнами (recipe.request.params.spans,
по умолчанию 1 и 2 ночи). Каждое окно даёт нижнюю оценку свободных домиков
для КАЖДОЙ своей ночи, и по ночи берётся максимум по всем накрывшим её
окнам — лучшая из честных нижних оценок. Смещение, которое остаётся и
названо вслух: домик, продающийся только отрезком длиннее максимального
окна, читается занятым. Это та же оценка занятости СВЕРХУ, что у соседей.

Фонд категории = максимум наблюдённого за горизонт остатка, то есть оценка
СНИЗУ: категория, ни разу за горизонт не бывшая свободной, фонда не получит
вовсе — её клетки весят один домик, и об этом пишется в reason (иначе
«сколько всего домиков» тихо занижается).

Глубина здесь НЕ бесплатна: запросов — число ночей × число окон (77 ночей ×
2 окна ≈ 2,5 минуты с паузой 1,2 с), поэтому горизонт не углубляется до
общего DEEP_DATE_TO: снимаем ровно date_from..date_to. С 04.09.2026 (тикет
06) верхняя граница ещё и ограничивается горизонтом ФОНДА: на годовой сетке
это было бы 366 ночей × 2 окна = 732 запроса к одному хосту на объект,
то есть примерно 18 минут прогона ради одного объекта-референса.

Правила ошибок — единый каскад (транспорт probes/_common.py + правила
исхода probes/_outcome.py), с одной оговоркой: 4xx на ОТДЕЛЬНОМ окне
рецепт не ломает (77+ запросов подряд к одному хосту — самый
вероятный кандидат словить 429; сосед TravelLine так же не ломает рецепт на
поночном шаге фонда). Рецепт уходит в broken, только если не снялось НИ
ОДНО окно или если сменилась схема ответа. Один запрос — одна попытка,
пауза >= 1.2 с между запросами к хосту, ничего не бронируется.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Optional

from ._common import (AccessRefused, SchemaChanged,
                      _base_object as _common_base, _iso_dates,
                      make_fetch as _make_fetch)
# Каскад и сборка объекта идут через правила ИСХОДА (probes/_outcome.py):
# 5xx рецепт не ломает никогда, а отказ хоста поверх уже снятой сетки не
# заводит счётчик суток. Транспорт про судьбу рецепта не знает.
from ._outcome import classify_response, finish as _finish

# Состав гостей минимальный: больший отрезал бы малые категории по
# вместимости и они читались бы как проданные (грабли TL, тикет 08).
# Сверка 16.08 на pineriver_hotel: adults=1 и adults=2 дают одну выдачу.
GUESTS = [{"adults": 1, "children": []}]

# Окна съёма в ночах. 1 — «ночь свободна сама по себе» (ловит одиночную
# дыру между бронями), 2 — обходит минимальный срок выходного дня, самый
# частый у загородных объектов. Переопределяется рецептом (params.spans).
DEFAULT_SPANS = (1, 2)
MAX_SPANS = 4  # предохранитель: окна множатся на число ночей горизонта

_BROKEN_HINT = "похоже на смену API UHotels или антибот"

# Версия РАЗБОРА движка (поле probe_version снапшота). 2 — правка
# 04.09.2026: горизонт съёма ограничивается границей фонда (тикет 06), 403/
# 429 больше не ломают рецепт (тикет 03).
PROBE_VERSION = 2


def _body_text(fetch) -> str:
    """Тело последнего ответа fetch — им опознаётся страница-заслон.

    Импорт ленивый: пакет probes грузит этот модуль сам, и импорт на уровне
    модуля замкнул бы круг. Реализация одна на все движки — probes.body_text_of.
    """
    from . import body_text_of
    return body_text_of(fetch)


def parse_rooms(payload: object) -> dict:
    """Ответ rooms -> {код категории: {"name", "left", "price"}}.

    left — поле max движка: сколько домиков категории доступно на ВЕСЬ
    запрошенный отрезок (0 = ни одного). price — цена за отрезок целиком
    (проверено 16.08: двухночное окно стоит вдвое дороже однночного), в
    клетку она попадает поделённой на длину окна. Категории, которую под
    этот отрезок продавать нельзя, в ответе нет вовсе — это НЕ распродажа,
    её толкует вызывающий. Не та форма -> SchemaChanged.
    """
    if not isinstance(payload, list):
        raise SchemaChanged(
            "ответ rooms не список категорий — схема UHotels сменилась")
    out: dict[str, dict] = {}
    for item in payload:
        if not isinstance(item, dict):
            raise SchemaChanged("в списке rooms не-объект")
        code = str(item.get("code") or item.get("id") or "").strip()
        if not code:
            raise SchemaChanged("запись rooms без code и id категории")
        left = item.get("max")
        if not isinstance(left, int) or isinstance(left, bool) or left < 0:
            raise SchemaChanged(
                f"rooms[{code}].max не целое число ({left!r})")
        name = str(item.get("name") or "").strip() or f"Категория {code}"
        entry = {"name": name, "left": left}
        price = item.get("price")
        if left > 0 and isinstance(price, (int, float)) \
                and not isinstance(price, bool) and price > 0:
            entry["price"] = price
        # Одна категория дважды в выдаче — берём больший остаток: так же
        # разрешает дубли тарифов TravelLine (parse_hotel_availability).
        if code not in out or entry["left"] > out[code]["left"]:
            out[code] = entry
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


def windows(dates: list[str], spans) -> list[tuple]:
    """Окна съёма -> [(дата заезда, длина, [ночи окна внутри горизонта]), ...].

    Окно, вылезающее за конец горизонта, не выбрасывается: оно всё равно
    несёт информацию о своих первых ночах. Ночи за горизонтом просто не
    записываются.
    """
    known = set(dates)
    out = []
    for start in dates:
        first = date.fromisoformat(start)
        for span in spans:
            covered = [(first + timedelta(days=i)).isoformat()
                       for i in range(span)]
            covered = [d for d in covered if d in known]
            if covered:
                out.append((start, span, covered))
    return out


def _window_body(params: dict, start: str, span: int) -> dict:
    """Тело rooms на окно: заезд start, выезд start+span."""
    first = date.fromisoformat(start)
    return {
        "hotel": params.get("hotel", ""),
        "lang": params.get("lang", "ru"),
        "currency": params.get("currency", "RUB"),
        "dateIn": start,
        "dateOut": (first + timedelta(days=span)).isoformat(),
        "days": span,
        "rooms": GUESTS,
        "roomType": "",
        "tariff": "",
        "coupon": "",
        "searchingRoomIndex": 0,
    }


def merge_window(free: dict, prices: dict, rooms: dict, covered: list,
                 span: int) -> None:
    """Влить выдачу одного окна в накопители (in-place).

    По каждой ночи окна остаток категории — НИЖНЯЯ оценка свободных домиков,
    поэтому копим максимум. Цена окна делится на его длину, и окно покороче
    вытесняет цену окна подлиннее: однночная цена точная, двухночная —
    средняя по двум ночам.
    """
    for night in covered:
        by_code = free.setdefault(night, {})
        price_by_code = prices.setdefault(night, {})
        for code, entry in rooms.items():
            if entry["left"] > by_code.get(code, 0):
                by_code[code] = entry["left"]
            if "price" in entry:
                best = price_by_code.get(code)
                if best is None or span < best[0]:
                    price_by_code[code] = (span, entry["price"] / span)


def _unit_names(directory: dict) -> dict:
    """{код: имя} -> {код: уникальное имя юнита} (дубли различаем кодом)."""
    seen: dict[str, int] = {}
    for name in directory.values():
        seen[name] = seen.get(name, 0) + 1
    used: set[str] = set()
    names: dict[str, str] = {}
    for code, name in directory.items():
        unit = name if seen[name] == 1 else f"{name} [{code}]"
        while unit in used:
            unit = f"{unit} [{code}]"
        used.add(unit)
        names[code] = unit
    return names


def build_units(free: dict, prices: dict, directory: dict,
                dates: list[str]) -> tuple[dict, list[str]]:
    """Накопители -> (сетка units по SCHEMA (б), список категорий без фонда).

    Ночь, по которой не снялось ни одно окно, остаётся unknown у всех
    категорий и в знаменатель занятости не идёт.
    """
    capacity: dict[str, int] = {}
    for by_code in free.values():
        for code, left in by_code.items():
            if left > capacity.get(code, 0):
                capacity[code] = left
    names = _unit_names(directory)
    units: dict[str, dict] = {}
    for code, unit in names.items():
        cap = capacity.get(code, 0)
        cells: dict[str, dict] = {}
        for night in dates:
            by_code = free.get(night)
            if by_code is None:
                cells[night] = {"state": "unknown"}
                continue
            left = by_code.get(code, 0)
            cell = {"state": "free" if left > 0 else "busy"}
            if cap:
                cell["units_total"] = max(cap, left)
                cell["units_free"] = left
            priced = (prices.get(night) or {}).get(code)
            if left > 0 and priced is not None:
                cell["price"] = round(priced[1], 2)
            cells[night] = cell
        units[unit] = cells
    no_capacity = sorted(names[code] for code in names
                         if not capacity.get(code))
    return units, no_capacity


def _with_note(result: tuple, note: str) -> tuple:
    """Дописать информационную пометку в reason, не трогая статус объекта.

    Пометка про обрезанный горизонт — это не «снимок неполный»: снято ровно
    то, что спрашивали. Через failures она сделала бы объект partial, и
    ярлык строки в сводке менялся бы без единого изменения в данных.
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
    """Снять сетку по рецепту UHotels. -> (obj, broken_reason | None).

    inventory принимается для единообразия диспетчера и запросов не
    добавляет: остаток домиков приходит тем же ответом, что доступность.

    inventory_date_to (тикет 06) режет здесь и СЕТКУ, а не только фонд, —
    и это не отступление от контракта, а его следствие: у UHotels ночь
    сетки и есть запрос фонда (POST на окно), поэтому годовой горизонт
    стоил бы 366 ночей × число окон запросов к одному хосту.

    Сетку за границей НЕ добиваем пустыми клетками, хотя приёмка тикета 06
    и просит «полную сетку»: у pineriver_hotel это 27 юнитов × 308 ночей =
    8316 клеток `unknown` в сутки, то есть примерно +374 КБ к файлу в 200 КБ
    — троекратный рост самого большого файла снапшота ради нуля информации.
    Вместо этого объект говорит границу прямо: `grid_until` (докуда
    спрашивали доступность), `inventory_until` (докуда спрашивали фонд) и
    строка в reason. Сводке этого хватает, чтобы месяц за границей читался
    «фонд не снимался», а не «нет данных», и чтобы «не спрашивали» не
    путалось с «движок не ответил».
    """
    if fetch is None:
        fetch = _make_fetch("post_json")
    request = recipe.get("request", {})
    params = request.get("params", {})
    headers = request.get("headers", {})
    obj = _base_object(username, recipe)
    if not params.get("hotel"):
        return _finish(obj, [], "в рецепте нет params.hotel — нечем звать "
                                "виджет UHotels")
    url = request.get("url_template", "")
    if not url:
        return _finish(obj, [], "в рецепте нет request.url_template")

    fund_until = date_to if inventory_date_to is None \
        else max(date_from, min(date_to, inventory_date_to))
    obj["inventory_until"] = fund_until.isoformat()
    # Докуда спрашивали ДОСТУПНОСТЬ — здесь это та же граница, что у фонда
    # (одна ночь = один POST). Сводке нужна именно она, чтобы месяц за
    # границей читался «дальше не спрашивали», а не «нет данных».
    obj["grid_until"] = fund_until.isoformat()
    dates = _iso_dates(date_from, fund_until)
    spans = _spans(params)
    obj["source_urls"].append(
        f"{url}#окна {'+'.join(str(s) for s in spans)} ноч. на каждую ночь, POST")
    free: dict[str, dict] = {}
    prices: dict[str, dict] = {}
    directory: dict[str, str] = {}
    failures: list[str] = []
    # Обрезанный горизонт — не «неполный снимок»: снято ровно то, что
    # спрашивали. Поэтому note идёт в reason ПОСЛЕ _finish и не переводит
    # объект в partial: иначе ярлык строки в сводке менялся бы каждый день
    # без единого изменения в данных.
    note = ("" if fund_until >= date_to else
            f"сетка снята до {fund_until.isoformat()} (горизонт фонда): у "
            f"этого движка каждая ночь — отдельный запрос, дальше не "
            f"спрашивали")
    ok_windows = 0
    fatal_seen = 0
    refused: Optional[str] = None
    refusal_status: Optional[int] = None
    for start, span, covered in windows(dates, spans):
        body = _window_body(params, start, span)
        what = f"{start} на {span} ноч."
        try:
            status, data = fetch(url, headers, body)
        except AccessRefused as e:
            refused, refusal_status = e.reason, e.status
            break
        except OSError as e:
            failures.append(f"{what}: сетевой сбой ({e})")
            continue
        verdict = classify_response(status, data is not None, what,
                                    hint=_BROKEN_HINT,
                                    body_text=_body_text(fetch))
        # Нас не пустили — окна дальше не идут: у этого пробника десятки
        # запросов подряд к одному хосту, и продолжать после 403/429 значит
        # ломиться в закрытую дверь. Рецепт при этом жив (тикет 03).
        if verdict.kind == "refused":
            refused, refusal_status = verdict.reason, status
            break
        # Прочие 4xx на ОТДЕЛЬНОМ окне рецепт не ломают. Приговор выносится
        # ниже, по итогу всего прогона: не снялось ни одного окна -> broken.
        if verdict.kind in ("broken", "network"):
            failures.append(verdict.reason)
            fatal_seen += 1 if verdict.kind == "broken" else 0
            continue
        try:
            rooms = parse_rooms(data)
        except SchemaChanged as e:
            # Схема — другое дело: тут повтор прогона не поможет.
            return _finish(obj, failures, f"{what}: {e}")
        ok_windows += 1
        merge_window(free, prices, rooms, covered, span)
        for code, entry in rooms.items():
            directory.setdefault(code, entry["name"])
    if refused and not ok_windows:
        return _with_note(_finish(obj, failures, None, refused=refused,
                                  refusal_status=refusal_status), note)
    if not ok_windows:
        reason = ("ни одно окно не снялось: " + _trim(failures) if failures
                  else "движок не ответил ни на одно окно")
        # Рецепт ломаем ТОЛЬКО если хост отвергал запросы (4xx кроме
        # 403/429, не-JSON): там переразведка и правда нужна. Сеть и 5xx
        # рецепт не трогают — нужен повтор прогона, не агент.
        return _with_note(
            _finish(obj, [] if fatal_seen else [reason],
                    reason if fatal_seen else None), note)
    if not directory:
        # Окна снялись, но категорий нет ни в одном: модуль выключен или
        # продаж нет. Это НЕ смена схемы (живой пример 16.08: TravelLine
        # того же объекта отвечает 200 с пустым календарём), поэтому рецепт
        # оставляем живым — переразведка тут ничего не починит.
        failures.append("движок не отдал ни одной категории ни на одно "
                        "окно — модуль выключен или продаж нет")
        return _with_note(_finish(obj, [_trim(failures)], None), note)

    obj["units"], no_capacity = build_units(free, prices, directory, dates)
    if no_capacity:
        # Фонд такой категории неизвестен: она весит один домик и в
        # занятости, и в ответе на вопрос «сколько всего домиков».
        failures.append(
            "фонд не снят у категорий (ни разу не были свободны за горизонт, "
            "считаются как один домик): " + _trim(no_capacity))
    if refused:
        # Отказ хоста ПОСЛЕ того, как окна уже отдали сетку, — это конец
        # удачного съёма, а не «нас не пустили»: тикет 03 говорит «успешный
        # съём обнуляет счётчик», а частичный съём с данными — это успех.
        # Поле refusal здесь копило бы сутки отказа живому рецепту, и три
        # таких дня подряд (на 92 запросах к одному хосту — обычная неделя)
        # отправили бы агента на переразведку, которая ничего не чинит.
        # Причина при этом не теряется: она уходит в reason объекта.
        failures.append(f"снято не до конца: {refused}")
    return _with_note(_finish(obj, [_trim(failures)] if failures else [],
                              None), note)


def _base_object(username: str, recipe: dict) -> dict:
    return _common_base(username, recipe, "per_unit",
                        default_engine="uhotels")
