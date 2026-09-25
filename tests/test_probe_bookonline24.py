# -*- coding: utf-8 -*-
"""Пробник bookonline24 («Контур.Отель»): живые фикстуры, фонд, ошибки.

Разведка 08.09.2026 (smr_baza_otdyha_briz, briz.club; чистый HTTP, без
браузера). Виджет bookonline24.ru/widget.js ходит в JSON-API
bookonline24.ru/widget/api/v1 без авторизации:
- GET  daily/{hotelId}/entities — справочник категорий (фонда в нём нет);
- POST availabilities/{hotelId}/daily {roomCategoryId, adultsCount,
  children, fromDate, toDate} -> {"<epoch-ms полуночи>": {"price": {...}}}:
  ночь ЕСТЬ = свободна, ночи НЕТ = не продаётся (оценка сверху);
- POST daily/{hotelId}/accommodation-prices/all {dateFrom, dateTo,
  adultsCount, children} -> [{roomCategoryId, availableCount, ...}] —
  ОСТАТОК домиков категории на отрезок; распроданная категория из ответа
  пропадает.

Фикстуры — живые ответы bookonline24.ru по отелю briz, снятые 08.09.2026
(тела как есть; entities обрезан до полей id/name/placesMin/placesMax/
area/rooms — description с прозой «всего 5 таких домов», comforts и
imageMetas выброшены):
- entities.json — две категории: «Барн-хаус», 130 (6 мест) и
  " Барн- Хаус"-70 (4 места);
- availabilities_daily_cat70_2026-09-08.json — категория 70, запрос
  08.09.2026-09.09.2027: 83 ночи 08.09-30.11.2026, нет только сб 26.09
  (12/15/17 тыс. руб. за ночь по дням недели); дальше 30.11 ночей нет —
  окно продаж отеля;
- availabilities_daily_cat130_2026-09-08.json — категория 130, тот же
  запрос: все 84 ночи 08.09-30.11 свободны (17/20/22 тыс.);
- accommodation_prices_all_2026-09-12_1n.json — сб 12.09, одна ночь:
  осталось 5 домиков «70» и 2 домика «130»;
- accommodation_prices_all_2026-09-09_1n.json — ср 09.09: 8 и 5;
- accommodation_prices_all_2026-09-26_1n.json — сб 26.09: категории «70»
  в ответе НЕТ (распродана — совпадает с календарём), «130» — 3.
"""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import occupancy_core as core
import probes
from probes import bookonline24 as bo
from probes._common import AccessRefused, SchemaChanged

FIXTURES = Path(__file__).parent / "fixtures" / "bookonline24"

HOTEL = "5d5510f5-d1cc-4ac8-bb20-cb3d457d330a"
CAT130 = "3c0229ed-67e8-4438-8a98-9157c148fd96"
CAT70 = "c0c87477-7bf8-423e-bab9-f4717d7cf8e3"
NAME130 = "«Барн-хаус», 130"
NAME70 = '" Барн- Хаус"-70'
API = "https://bookonline24.ru/widget/api/v1"
CALENDAR_URL = f"{API}/availabilities/{HOTEL}/daily"
ENTITIES_URL = f"{API}/daily/{HOTEL}/entities"
PRICES_URL = f"{API}/daily/{HOTEL}/accommodation-prices/all"

DAY = date(2026, 9, 8)


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


BO_RECIPE = {
    "site": "https://briz.club",
    "engine": "bookonline24",
    "status": "ok",
    "request": {
        "url_template": CALENDAR_URL,
        "method": "POST",
        "params": {
            "hotel_id": HOTEL,
            "room_categories": {CAT130: NAME130, CAT70: NAME70},
        },
        "headers": {"Referer": "https://briz.club/",
                    "Origin": "https://briz.club",
                    "Content-Type": "application/json"},
        "date_substitution": "fromDate/toDate = YYYY-MM-DD в JSON-теле",
    },
    "discovered_at": "2026-09-08T00:30:00+03:00",
    "notes": "",
    "source_urls": [],
}


def recipe_with(**params):
    recipe = json.loads(json.dumps(BO_RECIPE))
    recipe["request"]["params"].update(params)
    return recipe


def fake_fetch(entities=None, calendars=None, prices=None,
               prices_default=None, calls=None):
    """fetch(url, headers, payload=None) -> (status, JSON) по адресу и телу.

    entities — ответ GET справочника (payload None); calendars —
    {roomCategoryId: ответ | (status, ответ) | исключение}; prices —
    {(dateFrom, длина окна): ответ | (status, ответ) | исключение}, для
    незаданного окна — prices_default (по умолчанию пустой список, то есть
    «на этот отрезок ничего не продаётся»).
    """
    calendars = calendars or {}
    prices = prices or {}

    def _answer(answer):
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, tuple) else (200, answer)

    def fetch(url, headers, payload=None):
        if calls is not None:
            calls.append((url, payload))
        if payload is None:
            if entities is None:
                raise AssertionError("справочник не ожидался")
            return _answer(entities)
        if "roomCategoryId" in payload:
            cat = payload["roomCategoryId"]
            if cat not in calendars:
                raise AssertionError(f"неожиданная категория {cat}")
            return _answer(calendars[cat])
        first = date.fromisoformat(payload["dateFrom"])
        span = (date.fromisoformat(payload["dateTo"]) - first).days
        key = (payload["dateFrom"], span)
        if key in prices:
            return _answer(prices[key])
        return _answer([] if prices_default is None else prices_default)
    fetch.last_body_text = ""
    return fetch


def live_fetch(calls=None, **overrides):
    """Живые фикстуры briz: справочник, два календаря, остатки по дням.

    Незаданные ночи остатка отвечают средой 09.09 (8 и 5 домиков)."""
    prices = {("2026-09-12", 1): load("accommodation_prices_all_2026-09-12_1n.json"),
              ("2026-09-26", 1): load("accommodation_prices_all_2026-09-26_1n.json")}
    prices.update(overrides.pop("prices", {}))
    kwargs = dict(entities=load("entities.json"),
                  calendars={CAT70: load("availabilities_daily_cat70_2026-09-08.json"),
                             CAT130: load("availabilities_daily_cat130_2026-09-08.json")},
                  prices=prices,
                  prices_default=load("accommodation_prices_all_2026-09-09_1n.json"),
                  calls=calls)
    kwargs.update(overrides)
    return fake_fetch(**kwargs)


def small_calendar(nights):
    """{дата: цена} -> ответ календаря с ключами epoch-ms полуночи MSK."""
    out = {}
    for d, price in nights.items():
        midnight = date.fromisoformat(d)
        # полночь по MSK (UTC+3) — как отдаёт живой сервер
        epoch = (midnight - date(1970, 1, 1)).days * 86400 - 3 * 3600
        out[str(epoch * 1000)] = {"price": {"roubles": price, "copecks": 0}}
    return out


def prices_all(**left_by_cat):
    """{id категории: остаток} -> ответ accommodation-prices/all."""
    return [{"roomCategoryId": cat, "availableCount": left,
             "accommodation": {"prices": []}}
            for cat, left in left_by_cat.items()]


@pytest.fixture
def today_0908(monkeypatch):
    monkeypatch.setattr(core, "today", lambda: DAY)
    return DAY


# ---------------------------------------------------------------------------
# Живые фикстуры briz: сетка по категориям с фондом
# ---------------------------------------------------------------------------

def test_live_fixture_builds_per_category_grid_with_fund(today_0908):
    obj, broken = bo.probe("smr_baza_otdyha_briz", BO_RECIPE, DAY,
                           date(2026, 9, 30), fetch=live_fetch(),
                           inventory_date_to=date(2026, 9, 30))
    assert broken is None
    assert obj["status"] == "ok", obj["reason"]
    assert obj["engine"] == "bookonline24"
    assert obj["granularity"] == "per_unit"
    assert set(obj["units"]) == {NAME130, NAME70}
    c70 = obj["units"][NAME70]
    c130 = obj["units"][NAME130]
    # среда: календарь free, остаток 8 из наблюдённого максимума 8
    assert c70["2026-09-09"] == {"state": "free", "price": 12000,
                                 "units_total": 8, "units_free": 8}
    # суббота 12.09: свободно 5 из 8, цена выходного дня
    assert c70["2026-09-12"] == {"state": "free", "price": 17000,
                                 "units_total": 8, "units_free": 5}
    assert c130["2026-09-12"] == {"state": "free", "price": 22000,
                                  "units_total": 5, "units_free": 2}
    # суббота 26.09: категории «70» нет ни в календаре, ни в остатке —
    # проданы все 8; «130» продано 2 из 5
    assert c70["2026-09-26"] == {"state": "busy", "units_total": 8,
                                 "units_free": 0}
    assert c130["2026-09-26"] == {"state": "free", "price": 22000,
                                  "units_total": 5, "units_free": 3}
    assert core.unit_basis(obj["units"])["basis"] == "unit"
    assert core.unit_basis(obj["units"])["homes"] == 13
    core.validate_object(obj)
    assert obj["inventory_until"] == "2026-09-30"
    assert obj["grid_until"] == core.grid_horizon().isoformat()
    assert ENTITIES_URL in obj["source_urls"]
    assert f"{CALENDAR_URL}#roomCategoryId={CAT70}" in obj["source_urls"]
    assert any(u.startswith(PRICES_URL + "#") for u in obj["source_urls"])


def test_live_fixture_september_pattern(today_0908):
    """Сентябрь по живой фикстуре: у «70» занята одна ночь, у «130» — ноль."""
    obj, _ = bo.probe("smr_baza_otdyha_briz", BO_RECIPE, DAY,
                      date(2026, 9, 30), fetch=live_fetch(),
                      inventory_date_to=date(2026, 9, 30))
    sep70 = {d: c for d, c in obj["units"][NAME70].items()
             if d.startswith("2026-09")}
    assert [d for d, c in sep70.items() if c["state"] == "busy"] == [
        "2026-09-26"]
    sep130 = {d: c for d, c in obj["units"][NAME130].items()
              if d.startswith("2026-09")}
    assert all(c["state"] == "free" for c in sep130.values())
    # занятость по домикам: «70» 1 ночь × 8 домиков + субботы/будни
    # по остаткам; у «130» на всех незаданных ночах свободно 5 из 5
    m = core.aggregate(obj["units"], ["2026-09"])[0]
    assert m["cuts"]["all"]["known"] == 23 * 13
    assert m["cuts"]["all"]["busy"] == 8 + 3 + 3 + 2  # 26.09 + сб 12.09 + 26.09 «130»


def test_sales_wall_beyond_the_hotel_window_is_relabeled_by_dispatcher(
        today_0908):
    """За 30.11 ночей в ответе нет — пробник пишет busy, а общее правило
    диспетчера (sales_wall) размечает стену как закрытые продажи."""
    obj, broken = bo.probe("smr_baza_otdyha_briz", BO_RECIPE, DAY,
                           core.grid_horizon(), fetch=live_fetch(),
                           inventory_date_to=date(2026, 9, 12))
    assert broken is None
    assert obj["units"][NAME130]["2026-12-01"] == {"state": "busy"}
    probes.apply_sales_window_rules(obj)
    assert obj["sales_window"]["rule"] == "sales_wall"
    assert obj["sales_window"]["since"] == "2026-12-01"
    assert obj["units"][NAME130]["2026-12-01"]["state"] == "sales_not_open"
    assert obj["units"][NAME130]["2026-11-30"]["state"] == "free"
    assert obj["units"][NAME70]["2026-09-26"]["state"] == "busy"


def test_calendar_is_one_post_per_category_on_the_deep_horizon(today_0908):
    """Глубина бесплатна: один POST на категорию, toDate — конец горизонта."""
    calls = []
    obj, broken = bo.probe("x", BO_RECIPE, DAY, date(2026, 10, 31),
                           fetch=live_fetch(calls=calls), inventory=False)
    assert broken is None
    calendar_calls = [p for u, p in calls if u == CALENDAR_URL]
    assert len(calendar_calls) == 2
    deep_end = core.grid_horizon().isoformat()
    assert all(p["fromDate"] == "2026-09-08" and p["toDate"] == deep_end
               for p in calendar_calls)
    assert all(p["adultsCount"] == 1 and p["children"] == []
               for p in calendar_calls)
    nights = [n for cells in obj["units"].values() for n in cells]
    assert max(nights) == deep_end == obj["grid_until"]


# ---------------------------------------------------------------------------
# Фонд: горизонт, флаг, расхождения с календарём
# ---------------------------------------------------------------------------

def test_inventory_is_asked_night_by_night_up_to_its_horizon(today_0908):
    calls = []
    obj, _ = bo.probe("x", BO_RECIPE, DAY, date(2026, 10, 31),
                      fetch=live_fetch(calls=calls),
                      inventory_date_to=date(2026, 9, 12))
    fund_calls = [p for u, p in calls if u == PRICES_URL]
    assert [p["dateFrom"] for p in fund_calls] == [
        "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11", "2026-09-12"]
    assert all(date.fromisoformat(p["dateTo"])
               == date.fromisoformat(p["dateFrom"]) + timedelta(days=1)
               for p in fund_calls)
    assert obj["inventory_until"] == "2026-09-12"
    c70 = obj["units"][NAME70]
    assert c70["2026-09-12"]["units_free"] == 5
    assert "units_total" not in c70["2026-09-13"]   # дальше фонд не спрашивали
    assert c70["2026-09-13"]["state"] == "free"


def test_no_inventory_flag_skips_the_fund_step(today_0908):
    calls = []
    obj, broken = bo.probe("x", BO_RECIPE, DAY, date(2026, 9, 30),
                           fetch=live_fetch(calls=calls), inventory=False)
    assert broken is None
    assert not [u for u, _ in calls if u == PRICES_URL]
    assert obj["inventory_until"] == "2026-09-08"   # ноль ночей фонда
    assert obj["status"] == "ok"
    assert core.unit_basis(obj["units"])["basis"] == "type"
    for cells in obj["units"].values():
        for cell in cells.values():
            assert "units_total" not in cell


def test_calendar_and_inventory_disagreement_stays_binary(today_0908):
    """Календарь free, а в остатке категории нет (минимальный срок/закрытый
    заезд): клетка остаётся бинарной, объект называет число расхождений."""
    fetch = live_fetch(prices={("2026-09-09", 1): prices_all(**{CAT130: 5})})
    obj, broken = bo.probe("x", BO_RECIPE, DAY, date(2026, 9, 9), fetch=fetch,
                           inventory_date_to=date(2026, 9, 9))
    assert broken is None
    c70 = obj["units"][NAME70]
    assert c70["2026-09-09"] == {"state": "free", "price": 12000}
    assert c70["2026-09-08"]["units_free"] == 8
    assert obj["status"] == "ok"                 # это не сбой
    assert "разошлись на 1 клетках" in obj["reason"]
    core.validate_object(obj)


def test_busy_night_with_positive_remainder_is_a_conflict_too(today_0908):
    calendars = {CAT70: small_calendar({"2026-09-09": 12000}),
                 CAT130: small_calendar({})}          # «130» закрыта
    fetch = fake_fetch(entities=load("entities.json"), calendars=calendars,
                       prices_default=prices_all(**{CAT70: 8, CAT130: 5}))
    obj, _ = bo.probe("x", BO_RECIPE, DAY, date(2026, 9, 9), fetch=fetch,
                      inventory_date_to=date(2026, 9, 9))
    c130 = obj["units"][NAME130]
    assert c130["2026-09-09"] == {"state": "busy"}
    assert "разошлись" in obj["reason"]
    core.validate_object(obj)


def test_category_never_free_in_fund_horizon_gets_no_fund_and_says_so(
        today_0908):
    calendars = {CAT70: small_calendar({"2026-09-08": 12000,
                                        "2026-09-09": 12000}),
                 CAT130: small_calendar({})}
    fetch = fake_fetch(entities=load("entities.json"), calendars=calendars,
                       prices_default=prices_all(**{CAT70: 8}))
    obj, broken = bo.probe("x", BO_RECIPE, DAY, date(2026, 9, 9), fetch=fetch,
                           inventory_date_to=date(2026, 9, 9))
    assert broken is None
    assert obj["status"] == "ok"
    assert obj["units"][NAME130]["2026-09-08"] == {"state": "busy"}
    assert obj["units"][NAME70]["2026-09-08"]["units_total"] == 8
    assert core.unit_basis(obj["units"])["basis"] == "mixed"
    assert "фонд не снят у категорий" in obj["reason"]
    assert NAME130 in obj["reason"]


def test_fund_is_the_max_observed_remainder(today_0908):
    calendars = {CAT70: small_calendar({"2026-09-08": 1, "2026-09-09": 1,
                                        "2026-09-10": 1})}
    prices = {("2026-09-08", 1): prices_all(**{CAT70: 2}),
              ("2026-09-09", 1): prices_all(**{CAT70: 6}),
              ("2026-09-10", 1): prices_all(**{CAT70: 1})}
    recipe = recipe_with(room_categories={CAT70: NAME70})
    fetch = fake_fetch(entities=[{"id": CAT70, "name": NAME70}],
                       calendars=calendars, prices=prices)
    obj, _ = bo.probe("x", recipe, DAY, date(2026, 9, 10), fetch=fetch,
                      inventory_date_to=date(2026, 9, 10))
    c70 = obj["units"][NAME70]
    nights = ["2026-09-08", "2026-09-09", "2026-09-10"]
    assert [c70[d]["units_total"] for d in nights] == [6, 6, 6]
    assert [c70[d]["units_free"] for d in nights] == [2, 6, 1]
    assert "units_total" not in c70["2026-09-11"]   # дальше фонд не спрашивали


def test_spans_from_recipe_add_a_second_window(today_0908):
    calls = []
    recipe = recipe_with(spans=[1, 2])
    bo.probe("x", recipe, DAY, date(2026, 9, 9), fetch=live_fetch(calls=calls),
             inventory_date_to=date(2026, 9, 9))
    fund = [(p["dateFrom"], p["dateTo"]) for u, p in calls if u == PRICES_URL]
    assert fund == [("2026-09-08", "2026-09-09"), ("2026-09-08", "2026-09-10"),
                    ("2026-09-09", "2026-09-10"), ("2026-09-09", "2026-09-11")]


# ---------------------------------------------------------------------------
# Справочник категорий: живой entities поверх рецепта
# ---------------------------------------------------------------------------

def test_live_entities_add_new_categories_and_drop_stale_ones(today_0908):
    stale = "00000000-0000-0000-0000-000000000000"
    recipe = recipe_with(room_categories={stale: "Старая", CAT70: NAME70})
    obj, broken = bo.probe("x", recipe, DAY, date(2026, 9, 9),
                           fetch=live_fetch(), inventory=False)
    assert broken is None
    assert set(obj["units"]) == {NAME70, NAME130}     # «130» из entities
    assert "Старая" in obj["reason"] and "нет в живом справочнике" in obj["reason"]
    assert obj["status"] == "ok"


def test_entities_network_failure_falls_back_to_the_recipe(today_0908):
    fetch = live_fetch(entities=ConnectionError("таймаут"))
    obj, broken = bo.probe("x", BO_RECIPE, DAY, date(2026, 9, 9), fetch=fetch,
                           inventory=False)
    assert broken is None
    assert set(obj["units"]) == {NAME70, NAME130}
    assert obj["status"] == "ok"                 # сетка целая
    assert "не обновлён" in obj["reason"]
    assert ENTITIES_URL not in obj["source_urls"]


def test_entities_4xx_falls_back_to_the_recipe_too(today_0908):
    fetch = live_fetch(entities=(404, None))
    obj, broken = bo.probe("x", BO_RECIPE, DAY, date(2026, 9, 9), fetch=fetch,
                           inventory=False)
    assert broken is None
    assert set(obj["units"]) == {NAME70, NAME130}
    assert "404" in obj["reason"]


def test_entities_4xx_without_recipe_directory_marks_broken(today_0908):
    recipe = recipe_with(room_categories={})
    fetch = live_fetch(entities=(404, None))
    obj, broken = bo.probe("x", recipe, DAY, date(2026, 9, 9), fetch=fetch)
    assert broken is not None and "room_categories" in broken
    assert obj["status"] == "insufficient_data"


def test_entities_network_failure_without_recipe_directory_keeps_recipe(
        today_0908):
    recipe = recipe_with(room_categories={})
    fetch = live_fetch(entities=ConnectionError("обрыв"))
    obj, broken = bo.probe("x", recipe, DAY, date(2026, 9, 9), fetch=fetch)
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "сетевой сбой" in obj["reason"]


def test_recipe_without_directory_takes_it_from_entities(today_0908):
    recipe = recipe_with(room_categories={})
    obj, broken = bo.probe("x", recipe, DAY, date(2026, 9, 9),
                           fetch=live_fetch(), inventory=False)
    assert broken is None
    assert set(obj["units"]) == {NAME70, NAME130}


def test_empty_entities_and_empty_recipe_is_broken(today_0908):
    recipe = recipe_with(room_categories={})
    fetch = live_fetch(entities=[])
    obj, broken = bo.probe("x", recipe, DAY, date(2026, 9, 9), fetch=fetch)
    assert broken is not None and "пуст" in broken


def test_duplicate_category_names_stay_separate_units(today_0908):
    a, b = "aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002"
    fetch = fake_fetch(entities=[{"id": a, "name": "Дом"}, {"id": b, "name": "Дом"}],
                       calendars={a: small_calendar({"2026-09-08": 100}),
                                  b: small_calendar({})})
    obj, _ = bo.probe("x", BO_RECIPE, DAY, DAY, fetch=fetch, inventory=False)
    assert set(obj["units"]) == {"Дом [aaaaaaaa]", "Дом [bbbbbbbb]"}
    assert obj["units"]["Дом [aaaaaaaa]"]["2026-09-08"]["state"] == "free"
    assert obj["units"]["Дом [bbbbbbbb]"]["2026-09-08"]["state"] == "busy"


# ---------------------------------------------------------------------------
# Ошибки: сеть не ломает рецепт, схема/4xx ломают, 403/429/заслон — отказ
# ---------------------------------------------------------------------------

def test_missing_room_category_id_204_marks_broken(today_0908):
    """Без roomCategoryId сервер отвечает 204 и пустым телом (проверка
    04.09) — это негодный рецепт, переразведка."""
    fetch = live_fetch(calendars={CAT70: (204, None), CAT130: (204, None)})
    obj, broken = bo.probe("x", BO_RECIPE, DAY, DAY, fetch=fetch)
    assert broken is not None and "204" in broken
    assert obj["status"] == "insufficient_data"


def test_schema_change_in_calendar_marks_broken(today_0908):
    fetch = live_fetch(calendars={CAT70: [1, 2, 3], CAT130: [1, 2, 3]})
    obj, broken = bo.probe("x", BO_RECIPE, DAY, DAY, fetch=fetch)
    assert broken is not None and "схема" in broken
    assert obj["status"] == "insufficient_data"


def test_garbage_calendar_keys_mark_broken(today_0908):
    fetch = live_fetch(calendars={CAT70: {"2026-09-08": {"price": 1}},
                                  CAT130: {}})
    obj, broken = bo.probe("x", BO_RECIPE, DAY, DAY, fetch=fetch)
    assert broken is not None and "epoch" in broken


def test_403_is_refusal_not_broken(today_0908):
    fetch = live_fetch(calendars={CAT70: (403, None), CAT130: (403, None)})
    obj, broken = bo.probe("x", BO_RECIPE, DAY, DAY, fetch=fetch)
    assert broken is None
    assert obj["refusal"]["status"] == 403
    assert obj["status"] == "insufficient_data"


def test_challenge_page_with_http_200_is_refusal(today_0908):
    fetch = live_fetch(calendars={CAT70: (200, None), CAT130: (200, None)})
    fetch.last_body_text = "<html><title>Just a moment...</title></html>"
    obj, broken = bo.probe("x", BO_RECIPE, DAY, DAY, fetch=fetch)
    assert broken is None
    assert "не пустили" in obj["refusal"]["reason"]


def test_refusal_on_entities_stops_before_the_calendar(today_0908):
    calls = []
    fetch = live_fetch(entities=(429, None), calls=calls)
    obj, broken = bo.probe("x", BO_RECIPE, DAY, DAY, fetch=fetch)
    assert broken is None
    assert obj["refusal"]["status"] == 429
    assert len(calls) == 1


def test_transport_refusal_on_entities_is_a_refusal(today_0908):
    fetch = live_fetch(entities=AccessRefused("хост просит подождать",
                                              status=429))
    obj, broken = bo.probe("x", BO_RECIPE, DAY, DAY, fetch=fetch)
    assert broken is None
    assert obj["refusal"]["status"] == 429


def test_502_without_json_keeps_the_recipe_alive(today_0908):
    fetch = live_fetch(calendars={CAT70: (502, None), CAT130: (502, None)})
    obj, broken = bo.probe("x", BO_RECIPE, DAY, DAY, fetch=fetch)
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "502" in obj["reason"]


def test_network_failure_on_one_category_is_partial(today_0908):
    fetch = live_fetch(calendars={CAT70: ConnectionError("обрыв"),
                                  CAT130: load("availabilities_daily_cat130_2026-09-08.json")})
    obj, broken = bo.probe("x", BO_RECIPE, DAY, DAY, fetch=fetch,
                           inventory=False)
    assert broken is None
    assert obj["status"] == "partial"
    assert "сетевой сбой" in obj["reason"]
    assert obj["units"][NAME70]["2026-09-08"] == {"state": "unknown"}
    assert obj["units"][NAME130]["2026-09-08"]["state"] == "free"


def test_403_after_a_category_was_taken_is_not_a_refusal(today_0908):
    fetch = live_fetch(calendars={
        CAT130: load("availabilities_daily_cat130_2026-09-08.json"),
        CAT70: (403, None)})
    obj, broken = bo.probe("x", BO_RECIPE, DAY, DAY, fetch=fetch,
                           inventory=False)
    assert broken is None
    assert "refusal" not in obj
    assert obj["status"] == "partial"
    assert "снято не до конца" in obj["reason"]


def test_fund_step_failures_never_break_the_recipe(today_0908):
    fetch = live_fetch(prices_default=(500, None), prices={})
    obj, broken = bo.probe("x", BO_RECIPE, DAY, date(2026, 9, 9), fetch=fetch,
                           inventory_date_to=date(2026, 9, 9))
    assert broken is None
    assert obj["status"] == "partial"
    assert "остатки" in obj["reason"] and "500" in obj["reason"]
    assert obj["units"][NAME70]["2026-09-08"] == {"state": "free",
                                                  "price": 12000}
    assert core.unit_basis(obj["units"])["basis"] == "type"


def test_fund_schema_change_stops_the_step_not_the_recipe(today_0908):
    calls = []
    fetch = live_fetch(prices_default={"совсем": "не то"}, prices={},
                       calls=calls)
    obj, broken = bo.probe("x", BO_RECIPE, DAY, date(2026, 9, 12), fetch=fetch,
                           inventory_date_to=date(2026, 9, 12))
    assert broken is None
    assert obj["status"] == "partial"
    assert "остаток дальше не снимался" in obj["reason"]
    assert len([u for u, _ in calls if u == PRICES_URL]) == 1


def test_refusal_inside_the_fund_step_goes_to_reason_not_refusal(today_0908):
    calls = []
    fetch = live_fetch(prices_default=(429, None), prices={}, calls=calls)
    obj, broken = bo.probe("x", BO_RECIPE, DAY, date(2026, 9, 12), fetch=fetch,
                           inventory_date_to=date(2026, 9, 12))
    assert broken is None
    assert "refusal" not in obj
    assert obj["status"] == "partial"
    assert "не пустили" in obj["reason"]
    assert len([u for u, _ in calls if u == PRICES_URL]) == 1  # дверь закрыта


def test_empty_calendar_is_all_busy_and_dispatcher_hides_it(today_0908):
    """Пустой {} — законный ответ (окно продаж закрыто): все ночи busy, а
    правило no_free_cells диспетчера прячет выдуманные 100%."""
    fetch = live_fetch(calendars={CAT70: {}, CAT130: {}})
    obj, broken = bo.probe("x", BO_RECIPE, DAY, date(2026, 10, 31), fetch=fetch,
                           inventory=False)
    assert broken is None
    assert all(c["state"] == "busy" for c in obj["units"][NAME70].values())
    probes.apply_sales_window_rules(obj)
    assert obj["sales_window"]["rule"] == "no_free_cells"
    assert obj["status"] == "partial"


def test_recipe_without_hotel_id_is_broken(today_0908):
    recipe = json.loads(json.dumps(BO_RECIPE))
    del recipe["request"]["params"]["hotel_id"]
    obj, broken = bo.probe("x", recipe, DAY, DAY, fetch=live_fetch())
    assert broken is not None and "hotel_id" in broken


# ---------------------------------------------------------------------------
# Разбор ответов: ключи-epoch, копейки, дубли
# ---------------------------------------------------------------------------

def test_night_of_lands_any_hotel_timezone_on_the_same_date():
    assert bo.night_of("1788814800000") == "2026-09-08"     # живой ключ, MSK
    midnight_utc = 1788825600            # 2026-09-08T00:00Z
    for offset_h in (-11, -5, 0, 3, 4, 11):
        assert bo.night_of((midnight_utc - offset_h * 3600) * 1000) == \
            "2026-09-08", offset_h
    assert bo.night_of("2026-09-08") is None
    assert bo.night_of("") is None
    assert bo.night_of(10 ** 30) is None


def test_parse_calendar_prices_with_copecks_and_missing_price():
    payload = {"1788814800000": {"price": {"roubles": 12000, "copecks": 50}},
               "1788901200000": {"price": None}}
    cells = bo.parse_calendar(payload, ["2026-09-08", "2026-09-09",
                                        "2026-09-10"])
    assert cells["2026-09-08"] == {"state": "free", "price": 12000.5}
    assert cells["2026-09-09"] == {"state": "free"}
    assert cells["2026-09-10"] == {"state": "busy"}


def test_parse_calendar_rejects_non_object_cells():
    with pytest.raises(SchemaChanged):
        bo.parse_calendar({"1788814800000": 5}, ["2026-09-08"])


def test_parse_prices_takes_the_larger_remainder_for_duplicates():
    payload = [{"roomCategoryId": CAT70, "availableCount": 2},
               {"roomCategoryId": CAT70, "availableCount": 4},
               {"roomCategoryId": CAT130, "availableCount": 0}]
    assert bo.parse_prices(payload) == {CAT70: 4, CAT130: 0}


def test_parse_prices_rejects_bad_counts():
    with pytest.raises(SchemaChanged):
        bo.parse_prices([{"roomCategoryId": CAT70, "availableCount": True}])
    with pytest.raises(SchemaChanged):
        bo.parse_prices([{"roomCategoryId": CAT70, "availableCount": -1}])
    with pytest.raises(SchemaChanged):
        bo.parse_prices({"not": "a list"})


def test_parse_entities_strips_names_and_rejects_bad_shapes():
    got = bo.parse_entities(load("entities.json"))
    assert got == {CAT130: NAME130, CAT70: NAME70}
    with pytest.raises(SchemaChanged):
        bo.parse_entities({"id": "x"})
    with pytest.raises(SchemaChanged):
        bo.parse_entities([{"name": "без id"}])


def test_endpoints_are_derived_from_the_recipe_and_overridable():
    urls = bo.endpoints(BO_RECIPE["request"])
    assert urls == {"calendar": CALENDAR_URL, "entities": ENTITIES_URL,
                    "prices": PRICES_URL}
    custom = recipe_with(entities_url="https://x/e", prices_url="https://x/p")
    urls = bo.endpoints(custom["request"])
    assert urls["entities"] == "https://x/e" and urls["prices"] == "https://x/p"


# ---------------------------------------------------------------------------
# Регистрация в диспетчере и версия разбора
# ---------------------------------------------------------------------------

def test_probe_version_is_declared():
    assert isinstance(bo.PROBE_VERSION, int) and bo.PROBE_VERSION >= 1


def test_bookonline24_registered_in_dispatcher():
    assert probes.ENGINES["bookonline24"] is bo


def test_dispatcher_runs_the_recipe_end_to_end(today_0908):
    obj, broken = probes.run_recipe("smr_baza_otdyha_briz", BO_RECIPE, DAY,
                                    core.grid_horizon(), fetch=live_fetch(),
                                    inventory_date_to=date(2026, 9, 12))
    assert broken is None
    assert obj["sales_window"]["since"] == "2026-12-01"
    core.validate_object(obj)
