# -*- coding: utf-8 -*-
"""Пробник Frontdesk24: справочник и живость токена, календарь, фонд, каскад.

Разведка 08.09.2026 (отель «Гора», Карелия, gora-hotel.ru/book/, чистый
HTTP): виджет pms.frontdesk24.ru/onlineWidget ходит POST-ами с JSON-телом в
/api/online/*. Три читающих метода — getHotelParams (справочник категорий и
признак живого токена), getAvailableDates (календарь: запись есть только у
продаваемой ночи) и getVariants (подбор на заезд: availableRooms категории —
остаток номеров, это фонд).

Фикстуры — живые ответы объекта 08.09.2026 (tests/fixtures/frontdesk24/):
- hotel_params_gora_2026-09-08.json — 5 категорий: Вилла 52211, Хаусбот 45
  52212, Стандарт 52626, Хаусбот-мини 53924, Хаусбот 40 54654; data[7] —
  отель;
- hotel_params_bad_token.json — тот же каркас по ЧУЖОМУ токену: data[6] и
  data[7] пустые, кода ошибки нет;
- available_dates_gora_2026-09-08_to_2026-10-08.json — 137 записей на 31
  ночь: у Виллы и Стандарта все 31, у хаусботов дыры 10–13.09 (все три),
  18–21.09 и 26.09 (Хаусбот 40), 01.10 (мини);
- variants_gora_2026-09-15_1n.json — будний вторник, все 5 категорий,
  хаусботы по 1, Стандарт 40 (описания и картинки обрезаны);
- variants_gora_2026-09-11_1n.json — пятница из дыры хаусботов: только
  Вилла (1) и Стандарт (36);
- variants_gora_2026-09-19_1n.json — суббота: Хаусбот 40 в подборе нет
  (18–21.09 закрыт), мини и 45 по 1, Стандарт 40;
- error_bad_date.json — ответ движка кодом 200 на dateFrom="abc".
"""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import occupancy_core as core
import probes
from probes import frontdesk24
from probes._common import AccessRefused, SchemaChanged

FIXTURES = Path(__file__).parent / "fixtures" / "frontdesk24"

BASE = "https://pms.frontdesk24.ru/api/online/"
TOKEN = "3D3AED88-3D9E-4B8D-AED8-3FAF58FC1310"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


FD_RECIPE = {
    "site": "https://gora-hotel.ru/",
    "engine": "frontdesk24",
    "status": "ok",
    "request": {
        "url_template": BASE + "getAvailableDates",
        "method": "POST",
        "params": {
            "token": TOKEN,
            "language": "ru",
            "currency": "RUB",
            "room_types": {
                "53924": "Хаусбот-мини",
                "54654": "Хаусбот 40",
                "52212": "Хаусбот 45",
                "52211": "Вилла (берег)",
                "52626": "Стандарт (берег)",
            },
        },
        "headers": {"Referer": "https://gora-hotel.ru/book/"},
        "date_substitution": "dateFrom/dateTo = YYYY-MM-DD в JSON-теле",
    },
    "discovered_at": "2026-09-08T01:10:00+03:00",
    "notes": "",
    "source_urls": [],
}


def recipe_with(**params):
    recipe = json.loads(json.dumps(FD_RECIPE))
    recipe["request"]["params"].update(params)
    return recipe


def recipe_without(*keys):
    recipe = json.loads(json.dumps(FD_RECIPE))
    for key in keys:
        recipe["request"]["params"].pop(key, None)
    return recipe


HP = load("hotel_params_gora_2026-09-08.json")
CAL = load("available_dates_gora_2026-09-08_to_2026-10-08.json")
V_0915 = load("variants_gora_2026-09-15_1n.json")
V_0911 = load("variants_gora_2026-09-11_1n.json")
V_0919 = load("variants_gora_2026-09-19_1n.json")


def fake_fetch(params=HP, dates=CAL, variants=None, calls=None,
               default_variants=None, dates_by_window=None):
    """fetch(url, headers, payload) -> (status, JSON) по методу в URL.

    params/dates — ответ (JSON | (status, JSON) | исключение) на
    getHotelParams / getAvailableDates (календарь спрашивается окнами, и
    dates отвечает на КАЖДОЕ окно; dates_by_window — {dateFrom окна: ответ}
    переопределяет отдельные окна); variants — {ночь dateFrom: ответ} для
    getVariants, незаданная ночь отвечает default_variants (по умолчанию —
    подбор 15.09, все категории свободны).
    """
    variants = variants or {}
    dates_by_window = dates_by_window or {}
    default_variants = V_0915 if default_variants is None else default_variants

    def fetch(url, headers, payload=None):
        method = url.rsplit("/", 1)[-1]
        if calls is not None:
            calls.append((method, payload))
        if method == "getHotelParams":
            answer = params
        elif method == "getAvailableDates":
            answer = dates_by_window.get(payload["dateFrom"], dates)
        elif method == "getVariants":
            answer = variants.get(payload["dateFrom"], default_variants)
        else:
            raise AssertionError(f"неожиданный метод {method}")
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, tuple) else (200, answer)
    return fetch


def run(recipe=FD_RECIPE, date_from=date(2026, 9, 8), date_to=date(2026, 10, 8),
        **kw):
    return frontdesk24.probe("hb_gora_hotel", recipe, date_from, date_to, **kw)


# ---------------------------------------------------------------------------
# Разбор живых ответов
# ---------------------------------------------------------------------------

def test_parse_hotel_params_live_fixture():
    parsed = frontdesk24.parse_hotel_params(HP)
    assert parsed["directory"] == {
        "52211": "Вилла", "52212": "Хаусбот 45", "52626": "Стандарт",
        "53924": "Хаусбот-мини", "54654": "Хаусбот 40"}
    assert parsed["hotel"]["token"] == TOKEN


def test_parse_hotel_params_bad_token_has_no_hotel_and_no_categories():
    parsed = frontdesk24.parse_hotel_params(load("hotel_params_bad_token.json"))
    assert parsed["directory"] == {}
    assert parsed["hotel"] is None


@pytest.mark.parametrize("payload", [
    {"data": []}, {"rooms": []}, [], {"data": [[], [], [], [], [], [], {}, []]},
    {"data": [[], [], [], [], [], [], [{"name": "без id"}], []]},
])
def test_parse_hotel_params_schema_change_raises(payload):
    with pytest.raises(SchemaChanged):
        frontdesk24.parse_hotel_params(payload)


def test_parse_available_dates_live_fixture():
    offers = frontdesk24.parse_available_dates(CAL)
    assert sorted(offers) == ["52211", "52212", "52626", "53924", "54654"]
    assert len(offers["52211"]) == 31 and len(offers["54654"]) == 22
    assert offers["52212"]["2026-09-08"] == 25000.0
    assert "2026-09-12" not in offers["52212"]        # дыра 10–13.09


def test_parse_available_dates_takes_min_price_for_duplicate_night():
    offers = frontdesk24.parse_available_dates({"data": [
        {"roomCategoryID": 1, "date": "2026-09-08", "price": 9000},
        {"roomCategoryID": 1, "date": "2026-09-08", "price": 7000}]})
    assert offers["1"]["2026-09-08"] == 7000


def test_parse_available_dates_bad_price_is_none_not_error():
    offers = frontdesk24.parse_available_dates({"data": [
        {"roomCategoryID": 1, "date": "2026-09-08", "price": None},
        {"roomCategoryID": 1, "date": "2026-09-09T00:00:00", "price": 0}]})
    assert offers["1"] == {"2026-09-08": None, "2026-09-09": None}


@pytest.mark.parametrize("payload", [
    {"rows": []}, {"data": [{"date": "2026-09-08"}]},
    {"data": [{"roomCategoryID": 1, "date": "08.09.2026"}]},
])
def test_parse_available_dates_schema_change_raises(payload):
    with pytest.raises(SchemaChanged):
        frontdesk24.parse_available_dates(payload)


def test_parse_variants_live_fixture():
    assert frontdesk24.parse_variants(V_0915) == {
        "52211": 1, "52212": 1, "52626": 40, "53924": 1, "54654": 1}
    assert frontdesk24.parse_variants(V_0911) == {"52211": 1, "52626": 36}


@pytest.mark.parametrize("payload", [
    {"data": []}, {"data": [{}]}, {"data": [[{"name": "без id"}]]},
    {"data": [[{"id": 1, "availableRooms": "много"}]]},
    {"data": [[{"id": 1, "availableRooms": True}]]},
])
def test_parse_variants_schema_change_raises(payload):
    with pytest.raises(SchemaChanged):
        frontdesk24.parse_variants(payload)


def test_engine_error_reads_live_error_body():
    assert "nvarchar" in frontdesk24.engine_error(load("error_bad_date.json"))
    assert frontdesk24.engine_error(HP) == ""
    assert frontdesk24.engine_error({"data": []}) == ""


def test_endpoint_is_derived_from_the_calendar_url():
    assert frontdesk24.endpoint(BASE + "getAvailableDates", "getVariants") \
        == BASE + "getVariants"


# ---------------------------------------------------------------------------
# Сетка: календарь -> клетки
# ---------------------------------------------------------------------------

def test_grid_reads_missing_night_as_busy_and_present_as_free_with_price():
    obj, broken = run(inventory=False, fetch=fake_fetch())
    assert broken is None
    hb45 = obj["units"]["Хаусбот 45"]
    assert hb45["2026-09-08"] == {"state": "free", "price": 25000.0}
    assert hb45["2026-09-12"] == {"state": "busy"}
    hb40 = obj["units"]["Хаусбот 40"]
    assert hb40["2026-09-19"]["state"] == "busy"          # 18–21.09 закрыт
    assert hb40["2026-09-22"]["state"] == "free"
    assert obj["units"]["Стандарт (берег)"]["2026-09-08"]["state"] == "free"


def test_recipe_names_win_and_shore_marks_survive():
    obj, _ = run(inventory=False, fetch=fake_fetch())
    assert sorted(obj["units"]) == ["Вилла (берег)", "Стандарт (берег)",
                                    "Хаусбот 40", "Хаусбот 45", "Хаусбот-мини"]


def test_tail_beyond_last_offered_night_is_sales_not_open_not_busy():
    """Живой факт 08.09: записи календаря кончаются 29.12.2026 у всех
    категорий разом. 40 «Стандартов», проданных на девять месяцев вперёд,
    — это закрытые продажи, а не аншлаг."""
    obj, _ = run(inventory=False, fetch=fake_fetch())
    std = obj["units"]["Стандарт (берег)"]
    assert obj["grid_until"] > "2026-10-08"              # глубина бесплатна
    assert std["2026-10-08"]["state"] == "free"
    assert std["2026-10-09"]["state"] == "sales_not_open"
    assert std[obj["grid_until"]]["state"] == "sales_not_open"
    assert "продажи открыты до 2026-10-08" in obj["reason"]
    assert obj["status"] == "ok"                          # это пометка, не сбой


def test_dispatcher_wall_rule_leaves_the_engine_edge_alone():
    """Движок сам показал границу окна — правило стены не переписывает
    ближние занятые ночи (условие 1 sales_wall_since)."""
    obj, _ = run(inventory=False, fetch=fake_fetch())
    before = json.loads(json.dumps(obj["units"]))
    probes.apply_sales_window_rules(obj)
    assert obj["units"] == before
    assert "sales_window" not in obj


def test_category_before_common_edge_is_busy_not_sales_not_open():
    """Категория, чья последняя открытая ночь раньше общей границы, до неё
    считается занятой: границу окна движок называет только календарём
    целиком."""
    cal = {"data": [
        {"roomCategoryID": 1, "date": "2026-09-08", "price": 100},
        {"roomCategoryID": 1, "date": "2026-09-09", "price": 100},
        {"roomCategoryID": 2, "date": "2026-09-08", "price": 100}]}
    hp = {"data": [[{}], [], [], [], [], [], [{"id": 1, "name": "А"},
                                             {"id": 2, "name": "Б"}],
                   [{"hotelName": "x", "token": "t"}]]}
    obj, _ = run(recipe_without("room_types"), inventory=False,
                 fetch=fake_fetch(params=hp, dates=cal))
    assert obj["units"]["Б"]["2026-09-09"]["state"] == "busy"
    assert obj["units"]["Б"]["2026-09-10"]["state"] == "sales_not_open"


def test_grid_is_deepened_for_free_and_names_its_edge():
    obj, _ = run(inventory=False, fetch=fake_fetch())
    assert obj["grid_until"] == frontdesk24.deep_date_to(date(2026, 10, 8)).isoformat()
    nights = sorted(obj["units"]["Вилла (берег)"])
    assert nights[0] == "2026-09-08" and nights[-1] == obj["grid_until"]


def test_price_zero_or_missing_does_not_reach_the_cell():
    cal = {"data": [{"roomCategoryID": 52211, "date": "2026-09-08", "price": 0},
                    {"roomCategoryID": 52211, "date": "2026-09-09"}]}
    obj, _ = run(inventory=False, fetch=fake_fetch(dates=cal))
    villa = obj["units"]["Вилла (берег)"]
    assert villa["2026-09-08"] == {"state": "free"}
    assert villa["2026-09-09"] == {"state": "free"}


# ---------------------------------------------------------------------------
# Справочник: рецепт и движок
# ---------------------------------------------------------------------------

def test_engine_category_outside_recipe_is_probed_and_named_in_reason():
    """Новый домик у движка не пропадает из сетки молча."""
    recipe = recipe_with(room_types={"52212": "Хаусбот 45"})
    obj, broken = run(recipe, inventory=False, fetch=fake_fetch())
    assert broken is None
    assert "Стандарт" in obj["units"] and "Хаусбот 45" in obj["units"]
    assert "вне рецепта" in obj["reason"] and "Стандарт [52626]" in obj["reason"]
    assert obj["status"] == "ok"


def test_recipe_category_gone_from_engine_is_unknown_not_100pct_busy():
    recipe = recipe_with(room_types={"52212": "Хаусбот 45",
                                     "99999": "Снесённый домик"})
    obj, _ = run(recipe, inventory=False, fetch=fake_fetch())
    gone = obj["units"]["Снесённый домик"]
    assert set(c["state"] for c in gone.values()) == {"unknown"}
    assert "больше нет" in obj["reason"] and "Снесённый домик" in obj["reason"]


def test_without_recipe_directory_engine_names_are_used():
    obj, _ = run(recipe_without("room_types"), inventory=False,
                 fetch=fake_fetch())
    assert sorted(obj["units"]) == ["Вилла", "Стандарт", "Хаусбот 40",
                                    "Хаусбот 45", "Хаусбот-мини"]


def test_same_names_are_told_apart_by_code():
    recipe = recipe_with(room_types={"52212": "Хаусбот", "54654": "Хаусбот"})
    obj, _ = run(recipe, inventory=False, fetch=fake_fetch())
    assert "Хаусбот [52212]" in obj["units"] and "Хаусбот [54654]" in obj["units"]


def test_hotel_params_network_failure_falls_back_to_recipe_directory():
    """Справочник не дошёл — сетка всё равно строится по рецепту, объект
    partial с причиной, полнота справочника не проверяется."""
    obj, broken = run(inventory=False,
                      fetch=fake_fetch(params=OSError("обрыв")))
    assert broken is None
    assert obj["status"] == "partial"
    assert "getHotelParams" in obj["reason"] and "обрыв" in obj["reason"]
    assert len(obj["units"]) == 5
    assert "больше нет" not in obj["reason"]


def test_hotel_params_failure_without_recipe_directory_is_insufficient():
    obj, broken = run(recipe_without("room_types"), inventory=False,
                      fetch=fake_fetch(params=(503, {})))
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert obj["units"] == {}
    assert "params.room_types" in obj["reason"]


def test_calendar_category_unknown_to_directory_is_named_not_dropped_silently():
    cal = {"data": [{"roomCategoryID": 777, "date": "2026-09-08", "price": 5}]
            + CAL["data"]}
    obj, _ = run(inventory=False, fetch=fake_fetch(dates=cal))
    assert "777" in obj["reason"] and "без имени в справочнике" in obj["reason"]
    assert len(obj["units"]) == 5


# ---------------------------------------------------------------------------
# Живость токена: чужой токен — broken, а не «продаж нет»
# ---------------------------------------------------------------------------

def test_bad_token_breaks_the_recipe_instead_of_reading_empty_sales():
    """По чужому токену календарь отвечает {"data": []} — ровно как при
    закрытых продажах. Отличает их только справочник без отеля."""
    obj, broken = run(fetch=fake_fetch(params=load("hotel_params_bad_token.json"),
                                       dates={"data": []}))
    assert broken and "не нашёл ни отеля, ни категорий" in broken
    assert obj["status"] == "insufficient_data"
    assert obj["units"] == {}


def test_empty_calendar_with_live_token_is_closed_sales_not_sellout():
    obj, broken = run(fetch=fake_fetch(dates={"data": []}))
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "продажи закрыты или модуль пуст" in obj["reason"]
    assert all(c["state"] == "unknown"
               for cells in obj["units"].values() for c in cells.values())


def test_recipe_without_token_breaks():
    obj, broken = run(recipe_without("token"), fetch=fake_fetch())
    assert broken and "params.token" in broken


def test_recipe_without_url_breaks():
    recipe = json.loads(json.dumps(FD_RECIPE))
    recipe["request"]["url_template"] = ""
    obj, broken = run(recipe, fetch=fake_fetch())
    assert broken and "url_template" in broken


# ---------------------------------------------------------------------------
# Фонд: подбор на ночь
# ---------------------------------------------------------------------------

def test_fund_from_variants_marks_free_and_busy_cells():
    fetch = fake_fetch(variants={"2026-09-11": V_0911, "2026-09-15": V_0915})
    obj, broken = run(fetch=fetch, inventory_date_to=date(2026, 9, 15))
    assert broken is None
    std = obj["units"]["Стандарт (берег)"]
    assert std["2026-09-15"] == {"state": "free", "price": 8200.0,
                                 "units_total": 40, "units_free": 40}
    assert std["2026-09-11"]["units_free"] == 36
    assert std["2026-09-11"]["units_total"] == 40         # фонд = максимум
    hb45 = obj["units"]["Хаусбот 45"]
    assert hb45["2026-09-15"] == {"state": "free", "price": 25000.0,
                                  "units_total": 1, "units_free": 1}
    # 11.09 — в дыре календаря и вне подбора: занят весь фонд (1 борт)
    assert hb45["2026-09-11"] == {"state": "busy", "units_total": 1,
                                  "units_free": 0}
    assert core.unit_basis(obj["units"])["basis"] == "unit"


def test_each_houseboat_is_one_boat():
    """Три категории хаусботов — по одному борту в каждой (availableRooms=1
    на свободную ночь): вопрос «3 или 6 бортов» из units JSON закрыт."""
    obj, _ = run(fetch=fake_fetch(), inventory_date_to=date(2026, 9, 9))
    for unit in ("Хаусбот-мини", "Хаусбот 40", "Хаусбот 45"):
        assert obj["units"][unit]["2026-09-08"]["units_total"] == 1


def test_explicit_category_selection_does_not_copy_the_hotel_into_each_boat():
    recipe = recipe_with(include_room_types=["52212"])
    obj, broken = run(recipe=recipe, fetch=fake_fetch(),
                      inventory_date_to=date(2026, 9, 9))
    assert broken is None
    assert set(obj["units"]) == {"Хаусбот 45"}
    assert obj["units"]["Хаусбот 45"]["2026-09-08"]["units_total"] == 1
    assert "выдача ограничена" in obj["reason"]
    assert "категории без имени" not in obj["reason"]


@pytest.mark.parametrize("room_types", [{"99999": "Пропавший борт"}, {}])
def test_missing_selected_category_remains_unknown(room_types):
    recipe = recipe_with(include_room_types=["99999"], room_types=room_types)
    obj, broken = run(recipe=recipe, fetch=fake_fetch(),
                      inventory_date_to=date(2026, 9, 9))
    assert broken is None
    assert len(obj["units"]) == 1
    cells = next(iter(obj["units"].values()))
    assert all(cell == {"state": "unknown"} for cell in cells.values())
    assert "99999" in obj["reason"] and "unknown" in obj["reason"]


def test_selected_boat_keeps_hotel_sales_boundary():
    recipe = recipe_with(include_room_types=["52212"])
    calendar = {"data": [
        {"roomCategoryID": 52212, "date": "2026-09-08", "price": 25000},
        {"roomCategoryID": 52626, "date": "2026-09-20", "price": 8200},
    ]}
    obj, broken = run(recipe=recipe, fetch=fake_fetch(dates=calendar), inventory=False)
    assert broken is None
    assert obj["units"]["Хаусбот 45"]["2026-09-15"]["state"] == "busy"
    assert obj["units"]["Хаусбот 45"]["2026-09-21"]["state"] == "sales_not_open"


@pytest.mark.parametrize("invalid", [[], "52212", None, [None], [True], [{}], [" "]])
def test_invalid_category_selector_fails_before_network(invalid):
    calls = []
    _, broken = run(recipe=recipe_with(include_room_types=invalid),
                    fetch=fake_fetch(calls=calls))
    assert broken and "include_room_types" in broken
    assert calls == []


def test_fund_nights_do_not_get_pairs_beyond_inventory_horizon():
    obj, _ = run(fetch=fake_fetch(), inventory_date_to=date(2026, 9, 10))
    std = obj["units"]["Стандарт (берег)"]
    assert "units_total" in std["2026-09-10"]
    assert "units_total" not in std["2026-09-11"]
    assert obj["inventory_until"] == "2026-09-10"


def test_inventory_horizon_bounds_variant_requests_not_the_grid():
    calls = []
    obj, _ = run(fetch=fake_fetch(calls=calls),
                 inventory_date_to=date(2026, 9, 12))
    nights = sorted(p["dateFrom"] for m, p in calls if m == "getVariants")
    assert nights == ["2026-09-08", "2026-09-09", "2026-09-10",
                      "2026-09-11", "2026-09-12"]
    assert obj["grid_until"] > "2026-10-08"
    windows = [p for m, p in calls if m == "getAvailableDates"]
    assert windows[0]["dateFrom"] == "2026-09-08"
    assert windows[-1]["dateTo"] == obj["grid_until"]
    for left, right in zip(windows, windows[1:]):
        assert date.fromisoformat(left["dateTo"]) + timedelta(days=1) == date.fromisoformat(right["dateFrom"])
    assert all((date.fromisoformat(p["dateTo"]) - date.fromisoformat(p["dateFrom"])).days < 61
               for p in windows)
    assert len([m for m, _ in calls if m == "getHotelParams"]) == 1


def test_variant_window_is_one_night_with_one_adult():
    calls = []
    run(fetch=fake_fetch(calls=calls), inventory_date_to=date(2026, 9, 8))
    body = [p for m, p in calls if m == "getVariants"][0]
    assert body["dateFrom"] == "2026-09-08" and body["dateTo"] == "2026-09-09"
    assert body["rooms"] == [{"adults": 1}]
    assert body["token"] == TOKEN


def test_no_inventory_skips_variants_and_names_zero_fund_nights():
    calls = []
    obj, _ = run(fetch=fake_fetch(calls=calls), inventory=False)
    assert not [m for m, _ in calls if m == "getVariants"]
    assert obj["inventory_until"] == "2026-09-08"
    assert obj["grid_until"] > "2026-10-08"
    assert all("units_total" not in c
               for cells in obj["units"].values() for c in cells.values())
    assert core.unit_basis(obj["units"])["basis"] == "type"


def test_without_inventory_horizon_fund_goes_to_date_to():
    obj, _ = run(fetch=fake_fetch(), date_to=date(2026, 9, 10))
    assert obj["inventory_until"] == "2026-09-10"


def test_calendar_open_but_variants_silent_keeps_cell_binary():
    """Минимальный срок: календарь ночь показывает, подбор на одну ночь
    категорию — нет. Пара не выдумывается, разногласие названо."""
    silent = {"data": [[{"id": 52626, "name": "Стандарт", "availableRooms": 40}],
                       [], [], [], [], []]}
    obj, broken = run(fetch=fake_fetch(default_variants=silent),
                      inventory_date_to=date(2026, 9, 9))
    assert broken is None
    hb45 = obj["units"]["Хаусбот 45"]["2026-09-08"]
    assert hb45["state"] == "free" and "units_total" not in hb45
    assert "разошлись" in obj["reason"]
    assert obj["status"] == "partial"                    # фонд хаусботов не снят
    assert "фонд не снят" in obj["reason"] and "Хаусбот 45" in obj["reason"]


def test_variants_sees_room_where_calendar_is_closed_keeps_cell_binary():
    cal = {"data": [{"roomCategoryID": 52626, "date": "2026-09-09", "price": 1}]}
    hp = {"data": [[{}], [], [], [], [], [], [{"id": 52626, "name": "Стандарт"}],
                   [{"hotelName": "x", "token": TOKEN}]]}
    std_only = {"data": [[{"id": 52626, "name": "Стандарт",
                           "availableRooms": 40}], [], [], [], [], []]}
    obj, _ = run(recipe_with(room_types={"52626": "Стандарт"}),
                 fetch=fake_fetch(params=hp, dates=cal,
                                  default_variants=std_only),
                 inventory_date_to=date(2026, 9, 9))
    closed = obj["units"]["Стандарт"]["2026-09-08"]
    assert closed["state"] == "busy" and "units_total" not in closed
    assert "разошлись на 1 клетках" in obj["reason"]


def test_available_rooms_zero_reads_like_absent_category():
    sold = {"data": [[{"id": 52626, "name": "Стандарт", "availableRooms": 0}],
                     [], [], [], [], []]}
    cal = {"data": [{"roomCategoryID": 52626, "date": "2026-09-09", "price": 1}]}
    obj, _ = run(recipe_with(room_types={"52626": "Стандарт"}),
                 fetch=fake_fetch(dates=cal, variants={"2026-09-08": sold},
                                  default_variants=V_0915),
                 inventory_date_to=date(2026, 9, 9))
    assert obj["units"]["Стандарт"]["2026-09-08"] == {
        "state": "busy", "units_total": 40, "units_free": 0}


def test_category_never_free_in_variants_is_named_without_fund():
    obj, _ = run(fetch=fake_fetch(default_variants=V_0911),
                 inventory_date_to=date(2026, 9, 9))
    mini = obj["units"]["Хаусбот-мини"]["2026-09-08"]
    assert mini["state"] == "free" and "units_total" not in mini
    assert obj["status"] == "partial"
    assert "фонд не снят" in obj["reason"] and "Хаусбот-мини" in obj["reason"]
    assert core.unit_basis(obj["units"])["basis"] == "mixed"


def test_single_bad_night_of_fund_does_not_break_recipe():
    fetch = fake_fetch(variants={"2026-09-09": (404, {"Message": "нет"})})
    obj, broken = run(fetch=fetch, inventory_date_to=date(2026, 9, 10))
    assert broken is None
    assert obj["status"] == "partial"
    assert "404" in obj["reason"]
    std = obj["units"]["Стандарт (берег)"]
    assert "units_total" in std["2026-09-08"]
    assert "units_total" not in std["2026-09-09"]        # ночь осталась бинарной
    assert std["2026-09-09"]["state"] == "free"


def test_engine_error_on_a_fund_night_is_a_failure_not_broken():
    fetch = fake_fetch(variants={"2026-09-08": load("error_bad_date.json")})
    obj, broken = run(fetch=fetch, inventory_date_to=date(2026, 9, 8))
    assert broken is None
    assert "nvarchar" in obj["reason"]


def test_variants_schema_change_keeps_calendar_and_recipe_alive():
    """Ревью 14.09.2026. Смена схемы на ВСПОМОГАТЕЛЬНОМ шаге фонда (getVariants)
    раньше возвращала broken_reason: рецепт помечался broken, а уже снятый календарь
    уходил в insufficient_data и выпадал из сводки. Соседи (TravelLine, Bnovo,
    bookonline24) на этом шаге фонд бросают, а объект считают по типам — так и здесь."""
    fetch = fake_fetch(variants={"2026-09-08": {"data": [{"id": 1}]}})
    obj, broken = run(fetch=fetch, inventory_date_to=date(2026, 9, 8))
    assert broken is None
    assert obj["status"] == "partial"
    assert "схема Frontdesk24 сменилась" in obj["reason"]


def test_refusal_during_fund_step_is_partial_not_refusal_field():
    """403 на хвосте объекта, у которого сетка уже снята, — успех не до
    конца (правило 2 _outcome), счётчик суток не заводится; ночи после
    отказа не идут."""
    calls = []
    fetch = fake_fetch(variants={"2026-09-09": (403, {"error": "forbidden"})},
                       calls=calls)
    obj, broken = run(fetch=fetch, inventory_date_to=date(2026, 9, 12))
    assert broken is None
    assert obj["status"] == "partial"
    assert "снято не до конца" in obj["reason"] and "403" in obj["reason"]
    assert "refusal" not in obj
    assert sorted(p["dateFrom"] for m, p in calls if m == "getVariants") \
        == ["2026-09-08", "2026-09-09"]


# ---------------------------------------------------------------------------
# Каскад ошибок на справочнике и календаре
# ---------------------------------------------------------------------------

def test_403_on_hotel_params_is_refusal_not_broken():
    obj, broken = run(fetch=fake_fetch(params=(403, {"error": "forbidden"})))
    assert broken is None
    assert obj["refusal"]["status"] == 403
    assert obj["status"] == "insufficient_data"


def test_transport_refusal_on_hotel_params_keeps_recipe_alive():
    obj, broken = run(fetch=fake_fetch(
        params=AccessRefused("заслон", status=429, retry_after=5)))
    assert broken is None
    assert obj["refusal"]["status"] == 429


def test_404_on_hotel_params_breaks():
    obj, broken = run(fetch=fake_fetch(params=(404, {"Message": "нет"})))
    assert broken and "404" in broken


def test_405_wrong_method_breaks_with_engine_message():
    """Живой ответ 08.09 на GET: 405 с {"Message": …} — переразведка."""
    obj, broken = run(fetch=fake_fetch(
        dates=(405, {"Message": "Запрошенный ресурс не поддерживает GET"})))
    assert broken and "405" in broken


def test_engine_error_body_with_http_200_on_calendar_breaks():
    obj, broken = run(fetch=fake_fetch(dates=load("error_bad_date.json")))
    assert broken and "nvarchar" in broken


def test_engine_error_body_on_hotel_params_breaks():
    obj, broken = run(fetch=fake_fetch(params={"error": {"code": "0001",
                                                          "message": "Bad token"}}))
    assert broken and "Bad token" in broken


def test_hotel_params_schema_change_breaks():
    obj, broken = run(fetch=fake_fetch(params={"data": [[]]}))
    assert broken and "схема Frontdesk24 сменилась" in broken


def test_calendar_schema_change_breaks():
    obj, broken = run(fetch=fake_fetch(dates={"data": [{"date": "x"}]}))
    assert broken and "схема Frontdesk24 сменилась" in broken


def test_calendar_network_failure_keeps_recipe_alive_with_unknown_grid():
    obj, broken = run(fetch=fake_fetch(dates=OSError("таймаут")))
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "таймаут" in obj["reason"]
    assert len(obj["units"]) == 5
    assert all(c["state"] == "unknown"
               for cells in obj["units"].values() for c in cells.values())


def test_calendar_5xx_with_html_body_keeps_recipe_alive():
    obj, broken = run(fetch=fake_fetch(dates=(502, None)))
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "502" in obj["reason"]


def test_calendar_403_is_refusal_not_broken():
    obj, broken = run(fetch=fake_fetch(dates=(403, {"error": "forbidden"})))
    assert broken is None
    assert obj["refusal"]["status"] == 403


def test_non_json_200_body_is_broken_unless_it_is_a_challenge():
    """Тело не JSON при 200 — смена схемы; но страница-заслон с тем же
    кодом — отказ хоста (контракт волны 3, тело отдаёт fetch)."""
    fetch = fake_fetch(dates=(200, None))
    obj, broken = run(fetch=fetch)
    assert broken and "тело не JSON" in broken

    fetch = fake_fetch(dates=(200, None))
    fetch.last_body_text = "<html>Just a moment... cf-chl</html>"
    obj, broken = run(fetch=fetch)
    assert broken is None
    assert obj["refusal"]["status"] == 200


def test_reason_does_not_grow_to_the_length_of_the_horizon():
    fetch = fake_fetch(default_variants=(503, {}))
    obj, _ = run(fetch=fetch, inventory_date_to=date(2026, 10, 8))
    assert "и ещё" in obj["reason"] or "раз" in obj["reason"]
    assert len(obj["reason"]) < 600


# ---------------------------------------------------------------------------
# Контракт снапшота (как test_probe_contract для остальных движков)
# ---------------------------------------------------------------------------

def test_snapshot_validates_and_names_both_horizons():
    obj, broken = run(fetch=fake_fetch(), inventory_date_to=date(2026, 9, 9))
    assert broken is None
    core.validate_object(obj)
    assert obj["grid_until"] and obj["inventory_until"]
    nights = [n for cells in obj["units"].values() for n in cells]
    assert max(nights) <= obj["grid_until"]
    assert obj["engine"] == "frontdesk24"
    assert obj["granularity"] == "per_unit"
    assert obj["source_urls"] and all("frontdesk24" in u for u in obj["source_urls"])


def test_probe_version_is_declared():
    from probes._common import probe_version_of
    assert probe_version_of("frontdesk24", frontdesk24) == \
        f"frontdesk24@{frontdesk24.PROBE_VERSION}"


def test_dispatcher_runs_the_engine_once_registered():
    """Регистрацию в probes.ENGINES делает оркестратор; до неё диспетчер
    обязан честно отвечать «не поддержан», после — снимать."""
    fetch = fake_fetch()
    if "frontdesk24" in probes.ENGINES:
        obj, broken = probes.run_recipe("hb_gora_hotel", FD_RECIPE,
                                        date(2026, 9, 8), date(2026, 9, 8),
                                        fetch=fetch, inventory=False)
        assert broken is None and obj["status"] == "ok"
    else:
        obj, broken = probes.run_recipe("hb_gora_hotel", FD_RECIPE,
                                        date(2026, 9, 8), date(2026, 9, 8),
                                        fetch=fetch)
        assert broken is None and obj["status"] == "insufficient_data"
        assert "не поддержан" in obj["reason"]


def test_unit_names_helper_reports_extra_and_missing():
    names, extra, missing = frontdesk24.unit_names(
        {"1": "А (берег)", "9": "Нет такого"}, {"1": "А", "2": "Б"})
    assert names == {"1": "А (берег)", "9": "Нет такого", "2": "Б"}
    assert extra == ["2"] and missing == ["9"]
    # пустой справочник движка полноту не проверяет
    assert frontdesk24.unit_names({"1": "А"}, {})[2] == []


def test_apply_inventory_direct():
    units = {"А": {"2026-09-08": {"state": "free"},
                   "2026-09-09": {"state": "busy"},
                   "2026-09-10": {"state": "sales_not_open"}}}
    marked, disagreed, capacity = frontdesk24.apply_inventory(
        units, {"1": "А"}, {"2026-09-08": {"1": 3}, "2026-09-09": {},
                            "2026-09-10": {"1": 5}})
    assert marked == 2 and disagreed == 0 and capacity == {"1": 5}
    assert units["А"]["2026-09-08"] == {"state": "free", "units_total": 5,
                                        "units_free": 3}
    assert units["А"]["2026-09-09"] == {"state": "busy", "units_total": 5,
                                        "units_free": 0}
    assert units["А"]["2026-09-10"] == {"state": "sales_not_open"}


def test_iso_dates_cover_the_whole_fund_window():
    """Дата-подстановка: dateTo подбора = ночь + 1 сутки, без сдвигов."""
    calls = []
    run(fetch=fake_fetch(calls=calls), inventory_date_to=date(2026, 9, 9))
    bodies = [p for m, p in calls if m == "getVariants"]
    for body in bodies:
        assert date.fromisoformat(body["dateTo"]) \
            == date.fromisoformat(body["dateFrom"]) + timedelta(days=1)


# ---------------------------------------------------------------------------
# Календарь окнами: год одним POST-ом отвечает 44 с при таймауте 25 с
# ---------------------------------------------------------------------------

def test_calendar_is_asked_in_windows_that_cover_the_whole_grid():
    calls = []
    obj, _ = run(fetch=fake_fetch(calls=calls), inventory=False)
    windows = [(p["dateFrom"], p["dateTo"]) for m, p in calls
               if m == "getAvailableDates"]
    assert windows[0][0] == "2026-09-08"
    assert windows[-1][1] == obj["grid_until"]
    for (_, end), (start, _) in zip(windows, windows[1:]):
        assert date.fromisoformat(start) == date.fromisoformat(end) + timedelta(days=1)
    for start, end in windows:
        assert (date.fromisoformat(end) - date.fromisoformat(start)).days \
            < frontdesk24.CALENDAR_WINDOW_DAYS
    assert len(windows) >= 6                              # год — не один POST


def test_calendar_windows_helper():
    wins = frontdesk24.calendar_windows(date(2026, 9, 8), date(2026, 11, 20),
                                        days=30)
    assert wins == [(date(2026, 9, 8), date(2026, 10, 7)),
                    (date(2026, 10, 8), date(2026, 11, 6)),
                    (date(2026, 11, 7), date(2026, 11, 20))]
    assert frontdesk24.calendar_windows(date(2026, 9, 8), date(2026, 9, 8)) \
        == [(date(2026, 9, 8), date(2026, 9, 8))]


def test_failed_calendar_window_leaves_its_nights_unknown_and_no_sales_edge():
    """Окно не снялось — его ночи unknown, а не busy; и граница окна продаж
    по неполному календарю не читается: «дальше ничего не продаётся» может
    оказаться «дальше не спросили»."""
    second = (date(2026, 9, 8) + timedelta(days=frontdesk24.CALENDAR_WINDOW_DAYS)
              ).isoformat()
    obj, broken = run(fetch=fake_fetch(dates_by_window={second: OSError("обрыв")}),
                      inventory=False)
    assert broken is None
    assert obj["status"] == "partial" and "обрыв" in obj["reason"]
    std = obj["units"]["Стандарт (берег)"]
    assert std["2026-09-08"]["state"] == "free"
    assert std[second]["state"] == "unknown"
    assert std[obj["grid_until"]]["state"] == "busy"      # хвост не переписан
    assert "sales_not_open" not in {c["state"] for c in std.values()}
    assert "продажи открыты до" not in obj["reason"]


def test_refusal_on_a_later_window_keeps_snapped_nights_and_skips_fund():
    calls = []
    second = (date(2026, 9, 8) + timedelta(days=frontdesk24.CALENDAR_WINDOW_DAYS)
              ).isoformat()
    obj, broken = run(fetch=fake_fetch(
        dates_by_window={second: (429, {"error": "slow down"})}, calls=calls))
    assert broken is None
    assert obj["status"] == "partial"
    assert "снято не до конца" in obj["reason"] and "429" in obj["reason"]
    assert "refusal" not in obj
    assert obj["units"]["Вилла (берег)"]["2026-09-08"]["state"] == "free"
    assert obj["units"]["Вилла (берег)"][second]["state"] == "unknown"
    assert not [m for m, _ in calls if m == "getVariants"]
    assert len([m for m, _ in calls if m == "getAvailableDates"]) == 2
