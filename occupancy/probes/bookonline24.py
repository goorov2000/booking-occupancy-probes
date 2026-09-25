# -*- coding: utf-8 -*-
"""Пробник bookonline24 («Контур.Отель», виджет bookonline24.ru/widget.js).

Разведка 08.09.2026 (smr_baza_otdyha_briz, briz.club; чистый HTTP, без
браузера, 13 запросов). Сайт грузит https://bookonline24.ru/widget.js
(сборка СКБ Контур: retail-ui, metrika.kontur.ru, внутри fingerprintjs) и
зовёт HotelWidget.init({hotelId: "<uuid>"}); данные виджет берёт JSON-API
под префиксом https://bookonline24.ru/widget/api/v1 без авторизации, ему
хватает Origin/Referer сайта (ответ несёт access-control-allow-origin
домена отеля и vary: Origin). Три эндпоинта, все только читают:

- GET  daily/{hotelId}/entities -> [{id, name, placesMin, placesMax, rooms,
  ...}] — справочник категорий. Фонда (сколько домиков под категорией) в
  нём НЕТ: rooms — это комнаты внутри дома, а «всего на территории 5 таких
  домов» лежит прозой в description.
- POST availabilities/{hotelId}/daily {roomCategoryId, adultsCount,
  children, fromDate, toDate = YYYY-MM-DD} -> {"<epoch-ms полуночи ночи>":
  {"price": {"roubles", "copecks"}}} — ночи, которые ПРОДАЮТСЯ, с ценой за
  ночь. Ночь ЕСТЬ в ответе = свободна, ночи НЕТ = не продаётся (продана /
  закрыта владельцем / цена не задана — неотличимо, ОЦЕНКА СВЕРХУ, как у
  всех движков). roomCategoryId обязателен: без него сервер отвечает 204 и
  пустым телом (проверка 04.09). Диапазон отдаётся целиком, потолка длины
  у сервера не видно: запрос 08.09.2026-09.09.2027 вернул все ночи до конца
  окна продаж отеля (30.11.2026), запрос 01.12-31.01 — пустой {}; то есть
  граница в ответе — это ОКНО ПРОДАЖ отеля, а не кап сервера, и глубина
  сетки бесплатна (один POST на категорию на весь горизонт). Стену за
  окном продаж размечает общее правило диспетчера (sales_wall), здесь
  ничего не выдумывается. adultsCount на календарь не влияет (1 и 2 дали
  одинаковые 53 ночи), берём 1 — минимальный состав, чтобы вместимость не
  отрезала малые категории (грабли TL, тикет 08).
- POST daily/{hotelId}/accommodation-prices/all {dateFrom, dateTo,
  adultsCount, children} -> [{roomCategoryId, availableCount,
  accommodation: {prices: [...]}}] — категории, которые можно забронировать
  на ОТРЕЗОК dateFrom..dateTo, и ОСТАТОК домиков availableCount (в бандле
  это LastAvailableIndicator «осталось N номеров» и карта createAvailabilityMap).
  Это и есть фонд, которого не хватало разведке 04.09. Распроданная
  категория из ответа ПРОПАДАЕТ, а не приходит с нулём (живая проверка:
  сб 26.09 категория «Барн-Хаус-70» отсутствует в ответе и отсутствует в
  календаре). Цена здесь за ОТРЕЗОК, в клетку она не идёт — цена за ночь
  берётся из календаря.

Что из этого клетка. Состояние — из календаря категории (present -> free
с ценой, absent -> busy). Пара units_total/units_free ставится ТОЛЬКО там,
где остаток и календарь говорят одно и то же (правило TL apply_inventory):
календарь free и остаток > 0 -> (фонд, остаток); календарь busy и
остаток 0/категории нет -> (фонд, 0). Расходятся (минимальный срок,
закрытый заезд, ограничение по гостям) — клетка остаётся бинарной, и число
таких ночей называется в причине. Фонд категории = максимум наблюдённого
остатка за горизонт фонда, то есть ОЦЕНКА СНИЗУ (как у UHotels): категория,
ни разу не бывшая свободной за горизонт, фонда не получает и считается по
типу — об этом пишется в причине.

Цена. Остаток стоит одного POST на КАЖДУЮ ночь (один запрос отдаёт все
категории разом), поэтому он снимается только до inventory_date_to (тикет
06: 45 ближних ночей), а сетка — на весь горизонт двумя-тремя POST по
категориям. Окна остатка — params.spans, по умолчанию только 1 ночь: в
отличие от UHotels, ограниченные минимальным сроком категории движок в
ответе ОСТАВЛЯЕТ (клиент отсеивает их сам по restrictionsRecommendations,
функция extractAccommodationsWithoutRestrictions в бандле), так что второе
окно нужно лишь объекту, у которого живой прогон покажет расхождения
календаря и остатка. Справочник категорий обновляется каждым прогоном
одним GET entities (новая категория иначе молча занижала бы фонд год);
не отдал — идёт справочник из рецепта (params.room_categories).

Правила ошибок — единый каскад (транспорт probes/_common.py + правила
исхода probes/_outcome.py): смена схемы календаря, 4xx (в том числе 204 без
roomCategoryId) и не-JSON тело при 200 -> broken с причиной; сетевой сбой и
5xx -> unknown-клетки и partial/insufficient_data, рецепт жив; 403/429/
заслон -> «нас не пустили», рецепт не трогаем. Шаг остатка вспомогательный
и рецепт НЕ ломает никогда (прецедент TL _collect_inventory): не снялся —
объект считается по типам и говорит это вслух. Один запрос — одна попытка,
пауза >= 1.2 с к хосту, ничего не бронируется (booking/*, payments/* не
зовутся никогда).
"""
from __future__ import annotations

import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from ._common import (AccessRefused, SchemaChanged,
                      _base_object as _common_base, _iso_dates,
                      deep_date_to, make_fetch as _make_fetch)
# Каскад и сборка объекта идут через правила ИСХОДА (probes/_outcome.py):
# 5xx рецепт не ломает никогда, а отказ хоста поверх уже снятой сетки не
# заводит счётчик суток. Транспорт про судьбу рецепта не знает.
from ._outcome import classify_response, finish as _finish

API_PREFIX = "/widget/api/v1"
# Минимальный состав гостей: больший отрезал бы малые категории по
# вместимости (грабли TL, тикет 08). Сверка 08.09 на briz: adultsCount 1 и 2
# дают один и тот же календарь.
DEFAULT_ADULTS = 1
# Окна съёма остатка в ночах. Одно окно: движок оставляет в ответе
# категории с ограничением по сроку (см. докстринг), второе окно — рычаг
# рецепта (params.spans) на случай расхождений.
DEFAULT_SPANS = (1,)
MAX_SPANS = 4
# Полночь ночи в ответе — epoch-ms в часовом поясе отеля (Самара UTC+4,
# сервер отдавал полночь MSK). Сдвиг на полсуток кладёт любую полночь с
# поясом от -12 до +12 на ТУ ЖЕ календарную дату в UTC — пояс отеля
# спрашивать не нужно.
_HALF_DAY_SEC = 12 * 3600

_BROKEN_HINT = "похоже на смену API bookonline24 или антибот"

# Версия РАЗБОРА движка (поле probe_version снапшота). Поднимать при смене
# смысла полей ответа, а не оформления кода (тикет 10).
PROBE_VERSION = 1


def _body_text(fetch) -> str:
    """Тело последнего ответа fetch — им опознаётся страница-заслон.

    Импорт ленивый: пакет probes грузит этот модуль сам, и импорт на уровне
    модуля замкнул бы круг. Реализация одна на все движки — probes.body_text_of.
    """
    from . import body_text_of
    return body_text_of(fetch)


def _base_object(username: str, recipe: dict) -> dict:
    return _common_base(username, recipe, "per_unit",
                        default_engine="bookonline24")


# ---------------------------------------------------------------------------
# Разбор ответов
# ---------------------------------------------------------------------------

def night_of(key) -> Optional[str]:
    """Ключ календаря (epoch-ms полуночи ночи) -> YYYY-MM-DD. Мусор -> None."""
    try:
        ms = int(str(key).strip())
        when = datetime.fromtimestamp(ms / 1000 + _HALF_DAY_SEC,
                                      tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    return when.date().isoformat()


def _money(value) -> Optional[float]:
    """{"roubles": N, "copecks": M} -> число; не деньги -> None."""
    if not isinstance(value, dict):
        return None
    roubles = value.get("roubles")
    if not isinstance(roubles, (int, float)) or isinstance(roubles, bool):
        return None
    copecks = value.get("copecks")
    if isinstance(copecks, (int, float)) and not isinstance(copecks, bool) \
            and copecks:
        return round(roubles + copecks / 100, 2)
    return roubles


def parse_calendar(payload: object, dates: list[str]) -> dict:
    """Ответ availabilities/daily -> клетки {дата: {"state", "price"?}}.

    Ночь есть в ответе -> free (+ цена за ночь), нет -> busy (оценка
    сверху). Пустой словарь — законный ответ (за окном продаж), это все
    busy: стену дальше размечает диспетчер. Не та форма -> SchemaChanged.
    """
    if not isinstance(payload, dict):
        raise SchemaChanged(
            "ответ availabilities/daily не словарь ночь->цена — схема "
            "bookonline24 сменилась")
    available: dict[str, Optional[float]] = {}
    for key, value in payload.items():
        night = night_of(key)
        if night is None:
            raise SchemaChanged(
                f"ключ календаря {key!r} не epoch-ms полуночи — схема сменилась")
        if not isinstance(value, dict):
            raise SchemaChanged(
                f"клетка календаря {night} не объект — схема сменилась")
        available[night] = _money(value.get("price"))
    cells: dict[str, dict] = {}
    for d in dates:
        if d in available:
            cells[d] = {"state": "free"}
            price = available[d]
            if price is not None:
                cells[d]["price"] = price
        else:
            cells[d] = {"state": "busy"}
    return cells


def parse_entities(payload: object) -> dict:
    """Ответ entities -> {id категории: имя}. Не та форма -> SchemaChanged."""
    if not isinstance(payload, list):
        raise SchemaChanged(
            "ответ entities не список категорий — схема bookonline24 сменилась")
    out: dict[str, str] = {}
    for item in payload:
        if not isinstance(item, dict):
            raise SchemaChanged("в списке entities не-объект")
        cat_id = str(item.get("id") or "").strip()
        if not cat_id:
            raise SchemaChanged("категория entities без id")
        name = str(item.get("name") or "").strip()
        out[cat_id] = name or f"Категория {cat_id[:8]}"
    return out


def parse_prices(payload: object) -> dict:
    """Ответ accommodation-prices/all -> {id категории: остаток домиков}.

    Категории, которой в ответе нет, в словаре нет — это «на этот отрезок
    не продаётся» (распродана или закрыта), а толкует пропажу вызывающий
    вместе с календарём. Не та форма -> SchemaChanged.
    """
    if not isinstance(payload, list):
        raise SchemaChanged(
            "ответ accommodation-prices/all не список категорий — схема "
            "bookonline24 сменилась")
    out: dict[str, int] = {}
    for item in payload:
        if not isinstance(item, dict):
            raise SchemaChanged("в списке accommodation-prices не-объект")
        cat_id = str(item.get("roomCategoryId") or "").strip()
        if not cat_id:
            raise SchemaChanged("запись accommodation-prices без roomCategoryId")
        left = item.get("availableCount")
        if not isinstance(left, int) or isinstance(left, bool) or left < 0:
            raise SchemaChanged(
                f"accommodation-prices[{cat_id[:8]}].availableCount не целое "
                f"({left!r})")
        # Одна категория дважды (по тарифам) — берём больший остаток, как
        # UHotels и TL разрешают дубли.
        if left > out.get(cat_id, -1):
            out[cat_id] = left
    return out


# ---------------------------------------------------------------------------
# Рецепт -> адреса и справочник
# ---------------------------------------------------------------------------

def _origin(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return "https://bookonline24.ru"


def endpoints(request: dict) -> dict:
    """url_template + params -> адреса трёх эндпоинтов (переопределяемые).

    Адрес календаря — url_template рецепта; адреса справочника и остатка
    выводятся из его origin и params.hotel_id, если в params не лежат
    entities_url / prices_url явно.
    """
    params = request.get("params", {}) or {}
    url = request.get("url_template", "") or ""
    hotel_id = str(params.get("hotel_id") or "").strip()
    base = f"{_origin(url)}{API_PREFIX}/daily/{hotel_id}"
    return {
        "calendar": url,
        "entities": params.get("entities_url") or f"{base}/entities",
        "prices": params.get("prices_url") or f"{base}/accommodation-prices/all",
    }


def _spans(params: dict) -> tuple:
    """Длины окон остатка из рецепта, с предохранителем. Мусор -> умолчание."""
    raw = params.get("spans")
    if not isinstance(raw, (list, tuple)) or not raw:
        return DEFAULT_SPANS
    clean = sorted({int(x) for x in raw
                    if isinstance(x, int) and not isinstance(x, bool)
                    and 1 <= x <= 30})
    return tuple(clean[:MAX_SPANS]) or DEFAULT_SPANS


def _adults(params: dict) -> int:
    raw = params.get("adults_count")
    if isinstance(raw, int) and not isinstance(raw, bool) and 1 <= raw <= 20:
        return raw
    return DEFAULT_ADULTS


def unit_names(directory: dict) -> dict:
    """{id: имя} -> {id: уникальное имя юнита} (дубли различаем началом id)."""
    seen: dict[str, int] = {}
    for name in directory.values():
        seen[name] = seen.get(name, 0) + 1
    used: set[str] = set()
    names: dict[str, str] = {}
    for cat_id, name in directory.items():
        unit = name if seen[name] == 1 else f"{name} [{cat_id[:8]}]"
        while unit in used:
            unit = f"{unit} [{cat_id[:8]}]"
        used.add(unit)
        names[cat_id] = unit
    return names


def _recipe_directory(params: dict) -> dict:
    raw = params.get("room_categories") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for cat_id, value in raw.items():
        key = str(cat_id).strip()
        if not key:
            continue
        if isinstance(value, dict):
            name = str(value.get("name") or value.get("title") or "").strip()
        else:
            name = str(value or "").strip()
        out[key] = name or f"Категория {key[:8]}"
    return out


# ---------------------------------------------------------------------------
# Остаток домиков по ночам
# ---------------------------------------------------------------------------

def _windows(nights: list[str], spans) -> list[tuple]:
    """Окна остатка -> [(ночь заезда, длина, [ночи окна внутри горизонта])]."""
    known = set(nights)
    out = []
    for start in nights:
        first = date.fromisoformat(start)
        for span in spans:
            covered = [(first + timedelta(days=i)).isoformat()
                       for i in range(span)]
            covered = [d for d in covered if d in known]
            if covered:
                out.append((start, span, covered))
    return out


def _collect_inventory(url: str, headers: dict, nights: list[str],
                       spans, adults: int, fetch: Callable,
                       obj: dict) -> tuple[dict, list[str]]:
    """Остатки по ночам: {ночь: {id категории: остаток}} + список сбоев.

    Один POST на окно отдаёт все категории разом. Шаг НЕ ломает рецепт
    (прецедент TL): не снялось — объект остаётся посчитанным по типам, и
    сводка это честно пишет. Отказ хоста обрывает шаг: долбиться в
    закрытую дверь нельзя, а рецепт от этого не страдает.
    """
    by_night: dict[str, dict] = {}
    failures: list[str] = []
    snapped = 0
    for start, span, covered in _windows(nights, spans):
        first = date.fromisoformat(start)
        body = {
            "dateFrom": start,
            "dateTo": (first + timedelta(days=span)).isoformat(),
            "adultsCount": adults,
            "children": [],
        }
        what = f"остатки на {start}" + (f" ({span} ноч.)" if span > 1 else "")
        try:
            status, data = fetch(url, headers, body)
        except AccessRefused as e:
            failures.append(f"остатки: {e.reason}")
            break
        except OSError as e:
            failures.append(f"{what}: сетевой сбой ({e})")
            continue
        verdict = classify_response(status, data is not None, what,
                                    hint=_BROKEN_HINT,
                                    body_text=_body_text(fetch))
        if verdict.kind == "refused":
            failures.append(verdict.reason)
            break
        if verdict.kind != "ok":
            failures.append(verdict.reason)
            continue
        try:
            left_by_cat = parse_prices(data)
        except SchemaChanged as e:
            # Схема остатка сменилась: дальше спрашивать бессмысленно, но
            # календарь уже снят — объект считается по типам с этой причиной.
            failures.append(f"{what}: {e} — остаток дальше не снимался")
            break
        snapped += 1
        for night in covered:
            rest = by_night.setdefault(night, {})
            for cat_id, left in left_by_cat.items():
                if left > rest.get(cat_id, -1):
                    rest[cat_id] = left
    if snapped:
        obj["source_urls"].append(
            f"{url}#окна {'+'.join(str(s) for s in spans)} ноч. на каждую "
            f"ночь до {nights[-1]}, POST")
    return by_night, failures


def apply_inventory(units_by_cat: dict, by_night: dict) -> tuple[dict, int, int]:
    """Проставить фонд в сетку по остаткам. -> (фонд по категориям,
    сколько клеток размечено, сколько ночей разошлись с календарём).

    Фонд категории = максимум наблюдённого остатка (оценка СНИЗУ). Клетка
    получает пару только при согласии с календарём: free и остаток > 0 ->
    (фонд, остаток); busy и остаток 0 или категории в ответе нет -> (фонд,
    0). Расходятся — клетка остаётся бинарной (прецедент TL apply_inventory).
    """
    capacity: dict[str, int] = {}
    for rest in by_night.values():
        for cat_id, left in rest.items():
            if left > capacity.get(cat_id, 0):
                capacity[cat_id] = left
    marked = conflicts = 0
    for cat_id, cells in units_by_cat.items():
        cap = capacity.get(cat_id)
        if not cap:
            continue
        for night, rest in by_night.items():
            cell = cells.get(night)
            if cell is None:
                continue
            left = rest.get(cat_id, 0)
            if cell["state"] == "free" and left > 0:
                cell["units_total"] = max(cap, left)
                cell["units_free"] = left
                marked += 1
            elif cell["state"] == "busy" and left == 0:
                cell["units_total"] = cap
                cell["units_free"] = 0
                marked += 1
            else:
                conflicts += 1
    return capacity, marked, conflicts


def _trim(items: list[str], keep: int = 3) -> str:
    return "; ".join(items[:keep]) + (
        f" и ещё {len(items) - keep}" if len(items) > keep else "")


def _with_note(result: tuple, note: str) -> tuple:
    """Дописать информационную пометку в reason, не трогая статус объекта.

    Категория без фонда и расхождения календаря с остатком — не «снимок
    неполный»: снято ровно то, что спрашивали. Через failures они сделали
    бы объект partial каждый день без единого сбоя.
    """
    obj, broken = result
    if note and not broken:
        obj["reason"] = "; ".join(x for x in (obj.get("reason"), note) if x)
    return obj, broken


# ---------------------------------------------------------------------------
# Пробник
# ---------------------------------------------------------------------------

def probe(username: str, recipe: dict, date_from: date, date_to: date,
          fetch: Optional[Callable] = None, inventory: bool = True,
          inventory_date_to: Optional[date] = None):
    """Снять сетку по рецепту bookonline24. -> (obj, broken_reason | None).

    Сетка — один POST календаря на категорию на весь глубокий горизонт
    (глубина бесплатна, см. докстринг модуля), grid_until = его конец.
    Фонд — POST остатка на каждую ночь до inventory_date_to (тикет 06);
    inventory=False или горизонт фонда короче сетки -> inventory_until
    равен date_from (ноль ночей фонда), клетки бинарные.
    """
    if fetch is None:
        # get_json: с payload уходит POST, без него GET — один транспорт и
        # один трекер пауз на справочник, календарь и остаток.
        fetch = _make_fetch("get_json")
    request = recipe.get("request", {}) or {}
    params = request.get("params", {}) or {}
    headers = request.get("headers", {}) or {}
    obj = _base_object(username, recipe)
    hotel_id = str(params.get("hotel_id") or "").strip()
    if not hotel_id:
        return _finish(obj, [], "в рецепте нет params.hotel_id — нечем звать "
                                "виджет bookonline24")
    urls = endpoints(request)
    if not urls["calendar"]:
        return _finish(obj, [], "в рецепте нет request.url_template")

    deep_to = deep_date_to(date_to)
    dates = _iso_dates(date_from, deep_to)
    obj["grid_until"] = deep_to.isoformat()
    obj["inventory_until"] = date_from.isoformat()
    failures: list[str] = []
    notes: list[str] = []

    # --- справочник категорий: живой entities, иначе рецепт ---------------
    directory = _recipe_directory(params)
    try:
        status, data = fetch(urls["entities"], headers)
    except AccessRefused as e:
        return _finish(obj, failures, None, refused=e.reason,
                       refusal_status=e.status)
    except OSError as e:
        data, live_problem = None, f"сетевой сбой ({e})"
        fatal_problem = False
    else:
        verdict = classify_response(status, data is not None,
                                    "справочник категорий",
                                    hint=_BROKEN_HINT,
                                    body_text=_body_text(fetch))
        if verdict.kind == "refused":
            return _finish(obj, failures, None, refused=verdict.reason,
                           refusal_status=status)
        live_problem = "" if verdict.kind == "ok" else verdict.reason
        fatal_problem = verdict.kind == "broken"
    if data is not None and not live_problem:
        try:
            live = parse_entities(data)
        except SchemaChanged as e:
            live, live_problem, fatal_problem = {}, str(e), True
        else:
            obj["source_urls"].append(urls["entities"])
            stale = sorted(set(directory) - set(live))
            if stale:
                notes.append(
                    "категорий рецепта нет в живом справочнике, не снимались: "
                    + ", ".join(f"{directory[c]} [{c[:8]}]" for c in stale))
            directory = live
    if live_problem:
        if not directory:
            reason = (f"справочник категорий не отдан ({live_problem}), а в "
                      f"рецепте нет params.room_categories")
            # 4xx/схема — переразведка; сеть/5xx — повтор прогона.
            return _finish(obj, [] if fatal_problem else [reason],
                           reason if fatal_problem else None)
        notes.append(f"справочник категорий не обновлён ({live_problem}), "
                     f"взят из рецепта")
    if not directory:
        return _finish(obj, [], "справочник категорий пуст — у отеля нет "
                                "категорий в виджете, нужна переразведка")

    # --- календарь: один POST на категорию на весь горизонт ----------------
    names = unit_names(directory)
    units_by_cat: dict[str, dict] = {}
    broken: Optional[str] = None
    refused: Optional[str] = None
    refusal_status: Optional[int] = None
    adults = _adults(params)
    for cat_id, unit in names.items():
        body = {
            "roomCategoryId": cat_id,
            "adultsCount": adults,
            "children": [],
            "fromDate": date_from.isoformat(),
            "toDate": deep_to.isoformat(),
        }
        try:
            status, data = fetch(urls["calendar"], headers, body)
        except AccessRefused as e:
            refused, refusal_status = e.reason, e.status
            obj["units"][unit] = {d: {"state": "unknown"} for d in dates}
            break
        except OSError as e:
            failures.append(f"{unit}: сетевой сбой ({e})")
            obj["units"][unit] = {d: {"state": "unknown"} for d in dates}
            continue
        obj["source_urls"].append(f"{urls['calendar']}#roomCategoryId={cat_id}")
        verdict = classify_response(status, data is not None, unit,
                                    hint=_BROKEN_HINT,
                                    body_text=_body_text(fetch))
        if verdict.kind == "refused":
            refused, refusal_status = verdict.reason, status
            obj["units"][unit] = {d: {"state": "unknown"} for d in dates}
            break
        if verdict.kind == "broken":
            broken = verdict.reason
            break
        if verdict.kind == "network":
            failures.append(verdict.reason)
            obj["units"][unit] = {d: {"state": "unknown"} for d in dates}
            continue
        try:
            cells = parse_calendar(data, dates)
        except SchemaChanged as e:
            broken = f"{unit}: {e}"
            break
        obj["units"][unit] = cells
        units_by_cat[cat_id] = cells
    if broken or refused:
        return _finish(obj, failures, broken, refused=refused,
                       refusal_status=refusal_status)

    # --- остаток домиков: POST на ночь до горизонта фонда ------------------
    if inventory and units_by_cat:
        fund_until = date_to if inventory_date_to is None \
            else min(date_to, inventory_date_to)
        if fund_until >= date_from:
            obj["inventory_until"] = fund_until.isoformat()
            nights = _iso_dates(date_from, fund_until)
            by_night, fund_failures = _collect_inventory(
                urls["prices"], headers, nights, _spans(params), adults,
                fetch, obj)
            failures.extend(fund_failures)
            capacity, marked, conflicts = apply_inventory(units_by_cat,
                                                          by_night)
            no_fund = sorted(names[c] for c in units_by_cat
                             if not capacity.get(c))
            if by_night and no_fund:
                notes.append(
                    "фонд не снят у категорий (ни разу не были свободны за "
                    "горизонт фонда, считаются как один домик): "
                    + _trim(no_fund))
            if conflicts:
                notes.append(
                    f"календарь и остаток разошлись на {conflicts} клетках "
                    f"(минимальный срок или закрытый заезд) — они считаны "
                    f"бинарно, без фонда")
    return _with_note(_finish(obj, failures, None), "; ".join(notes))
