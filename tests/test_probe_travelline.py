# -*- coding: utf-8 -*-
"""Пробник TravelLine: парсеры на живых фикстурах, ошибки сети/схемы, диспетчер.

Фикстуры — обезличенные живые ответы, снятые 14.08.2026:
- reservationsteps_min_prices.json — юнит 385130 yck_kuzminskoe,
  14.08-31.10.2026 (без cookies/токенов: тело — только даты и цены);
- reservationsteps_min_prices_empty.json — живой ответ ok_reka (модуль
  подключён, тарифов нет ни на одну дату);
- tl_hotel_info.json / tl_room_type_availability.json /
  tl_booking_rules.json — istra-cottage.ru (hotel 11269), обрезаны до
  структурных полей, которые читает парсер.
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

import occupancy_core as core
import probes
from probes import travelline
from probes._common import AccessRefused

FIXTURES = Path(__file__).parent / "fixtures" / "travelline"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


RS_RECIPE = {
    "site": "https://kuzminskoeglamp.ru/",
    "engine": "travelline",
    "status": "ok",
    "request": {
        "url_template": ("https://public-api.reservationsteps.ru/v1/api/"
                         "min_prices?uid={uid}&dfrom={date_from}&dto={date_to}"
                         "&room_type_id={room_type_id}"),
        "method": "GET",
        "params": {
            "uid": "test-uid",
            "room_types": {"385130": "Стандарт", "385131": "Приват"},
        },
        "headers": {},
        "date_substitution": "dfrom/dto = DD-MM-YYYY",
    },
    "discovered_at": "2026-08-14T18:00:00+03:00",
    "notes": "",
    "source_urls": [],
}

TL_RECIPE = {
    "site": "https://istra-cottage.ru",
    "engine": "travelline",
    "status": "ok",
    "request": {
        "url_template": ("https://ru-ibe.tlintegration.ru/ApiWebDistribution/"
                         "AvailabilityCalendar/room_type_availability_2"
                         "?aggregate_dates=false&currency=RUB&max_nights=1"
                         "&hotel={hotel_code}&shared=false"
                         "&start_date={date_from}&end_date={date_to}"),
        "method": "GET",
        "params": {"hotel_code": "11269"},
        "headers": {},
        "date_substitution": "start_date/end_date = YYYY-MM-DD",
    },
    "discovered_at": "2026-08-14T18:00:00+03:00",
    "notes": "",
    "source_urls": [],
}


def rs_fetch(responses, inventory=None):
    """Фейковый fetch варианта reservationsteps: room_type_id -> ответ.

    inventory — {"YYYY-MM-DD": ответ /v1/api/rooms} для шага фонда типов
    (сколько номеров категории свободно); по умолчанию фонд не отдаётся.
    """
    def fetch(url, headers, body=None):
        if "/api/rooms" in url:
            night = _night_from_ddmmyyyy(url)
            return 200, (inventory or {}).get(night, {"rooms": []})
        for room_id, response in responses.items():
            if f"room_type_id={room_id}" in url:
                if isinstance(response, Exception):
                    raise response
                return response if isinstance(response, tuple) else (200, response)
        raise AssertionError(f"неожиданный url: {url}")
    return fetch


def _night_from_ddmmyyyy(url):
    """dfrom=DD-MM-YYYY в url -> ISO-дата ночи (для фейкового фонда Bnovo)."""
    m = re.search(r"dfrom=(\d{2})-(\d{2})-(\d{4})", url)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def tl_fetch(info=None, calendar=None, rules=None, inventory=None):
    """Фейковый fetch варианта TL API.

    inventory — {"YYYY-MM-DD": ответ hotel_availability} для шага фонда
    типов; по умолчанию остатки не отдаются (объект считается по типам).
    """
    routes = {"hotel_info": info, "room_type_availability_2": calendar,
              "hotel_booking_rules": rules}
    def fetch(url, headers, body=None):
        if "hotel_availability" in url:
            night = ((body or {}).get("criterions") or [{}])[0].get("start_date")
            return 200, (inventory or {}).get(night, {"room_stays": []})
        for marker, response in routes.items():
            if marker in url:
                if isinstance(response, Exception):
                    raise response
                return response if isinstance(response, tuple) else (200, response)
        raise AssertionError(f"неожиданный url: {url}")
    return fetch


# ---------------------------------------------------------------------------
# Парсеры на живых фикстурах
# ---------------------------------------------------------------------------

def test_parse_min_prices_live_fixture():
    dates = [(date(2026, 8, 14) + timedelta(days=i)).isoformat()
             for i in range(79)]
    cells = travelline.parse_min_prices(load("reservationsteps_min_prices.json"),
                                        dates)
    assert len(cells) == 79
    assert cells["2026-08-14"] == {"state": "busy"}
    assert cells["2026-08-16"] == {"state": "free", "price": 8912.17}
    assert cells["2026-10-31"] == {"state": "free", "price": 8000}


def test_parse_min_prices_date_outside_response_is_unknown():
    payload = load("reservationsteps_min_prices.json")
    cells = travelline.parse_min_prices(payload, ["2027-01-01"])
    assert cells == {"2027-01-01": {"state": "unknown"}}


def test_parse_min_prices_empty_means_sales_not_open():
    cells = travelline.parse_min_prices(
        load("reservationsteps_min_prices_empty.json"),
        ["2026-08-14", "2026-08-15"])
    assert cells == {"2026-08-14": {"state": "sales_not_open"},
                     "2026-08-15": {"state": "sales_not_open"}}


def test_parse_min_prices_schema_change_raises():
    with pytest.raises(travelline.SchemaChanged):
        travelline.parse_min_prices({"prices": {}}, ["2026-08-14"])
    with pytest.raises(travelline.SchemaChanged):
        travelline.parse_min_prices(
            {"min_prices": {"2026-08-14": "дорого"}}, ["2026-08-14"])


def test_parse_hotel_info_names_and_window():
    names, window_days = travelline.parse_hotel_info(load("tl_hotel_info.json"))
    assert len(names) == 11
    assert names["128544"] == "Двухкомнатный дом с горячей купелью"
    assert window_days == 366  # availability_max_date: 1 year


def test_parse_hotel_info_schema_change_raises():
    with pytest.raises(travelline.SchemaChanged):
        travelline.parse_hotel_info({"hotels": []})
    with pytest.raises(travelline.SchemaChanged):
        travelline.parse_hotel_info({"hotels": [{"room_types": []}]})


def test_parse_booking_rules_live_fixture():
    forbidden = travelline.parse_booking_rules(load("tl_booking_rules.json"))
    assert "2026-07-05" in forbidden          # прошедшая дата закрыта
    assert "2026-08-21" not in forbidden      # будущая дата открыта


def test_parse_room_type_availability_live_fixture():
    by_code = travelline.parse_room_type_availability(
        load("tl_room_type_availability.json"))
    assert len(by_code) == 11
    # 128544 продан 21-23.08 (ночей нет), свободен с 24.08
    assert "2026-08-21" not in by_code["128544"]
    assert by_code["128544"]["2026-08-24"] == 23300.0
    # несколько тарифов на ночь сводятся к минимальной цене
    assert by_code["77168"]["2026-08-21"] == 26000.0


def test_parse_room_type_availability_schema_change_raises():
    with pytest.raises(travelline.SchemaChanged):
        travelline.parse_room_type_availability({"room_stays": []})
    with pytest.raises(travelline.SchemaChanged):
        travelline.parse_room_type_availability(
            {"room_type_availability": [{"availability_date": []}]})
    with pytest.raises(travelline.SchemaChanged):
        # aggregate_dates=true отдаёт date вместо period — это не наш режим
        travelline.parse_room_type_availability(
            {"room_type_availability": [
                {"id_room_type": 1,
                 "availability_date": [{"is_available": True,
                                        "date": "2026-08-21"}]}]})


# ---------------------------------------------------------------------------
# Пробник reservationsteps целиком (фейковая сеть)
# ---------------------------------------------------------------------------

def test_probe_reservationsteps_per_unit_grid():
    fetch = rs_fetch({
        "385130": load("reservationsteps_min_prices.json"),
        "385131": {"min_prices": {"2026-08-14": None,
                                  "2026-08-15": {"p": 5000, "g": 2}}},
    })
    obj, broken = travelline.probe("yck", RS_RECIPE, date(2026, 8, 14),
                                   date(2026, 8, 15), fetch=fetch)
    assert broken is None
    assert obj["status"] == "ok"
    assert obj["granularity"] == "per_unit"
    assert obj["units"]["Стандарт"]["2026-08-14"] == {"state": "busy"}
    assert obj["units"]["Приват"]["2026-08-15"] == {"state": "free",
                                                    "price": 5000}
    assert len(obj["source_urls"]) == 2
    assert "dfrom=14-08-2026" in obj["source_urls"][0]


def test_probe_reservationsteps_without_room_types_is_aggregate():
    recipe = json.loads(json.dumps(RS_RECIPE))
    recipe["request"]["params"].pop("room_types")
    fetch = rs_fetch({"": {"min_prices": {"2026-08-14": {"p": 900, "g": 2}}}})
    obj, broken = travelline.probe("agg", recipe, date(2026, 8, 14),
                                   date(2026, 8, 14), fetch=fetch)
    assert broken is None
    assert obj["granularity"] == "aggregate"
    assert obj["units"][travelline.AGGREGATE_UNIT]["2026-08-14"] == {
        "state": "free", "price": 900}


def test_probe_reservationsteps_network_failure_is_not_broken():
    fetch = rs_fetch({"385130": ConnectionError("таймаут"),
                      "385131": ConnectionError("таймаут")})
    obj, broken = travelline.probe("yck", RS_RECIPE, date(2026, 8, 14),
                                   date(2026, 8, 15), fetch=fetch)
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "сетевой сбой" in obj["reason"]
    assert obj["units"]["Стандарт"]["2026-08-14"] == {"state": "unknown"}


def test_probe_reservationsteps_partial_when_one_unit_fails():
    fetch = rs_fetch({
        "385130": {"min_prices": {"2026-08-14": None}},
        "385131": ConnectionError("обрыв"),
    })
    obj, broken = travelline.probe("yck", RS_RECIPE, date(2026, 8, 14),
                                   date(2026, 8, 14), fetch=fetch)
    assert broken is None
    assert obj["status"] == "partial"
    assert "Приват" in obj["reason"]
    assert obj["units"]["Стандарт"]["2026-08-14"] == {"state": "busy"}
    assert obj["units"]["Приват"]["2026-08-14"] == {"state": "unknown"}


def test_probe_reservationsteps_deepens_dto_and_keeps_deep_nights(monkeypatch):
    """Ревью 14.08: глубина бесплатна — тот же ОДИН запрос на категорию, dto
    расширяется, глубокие ночи сохраняются в снапшот.

    Правка 04.09 (тикет 01): конец расширенного окна считается от сегодня
    (core.grid_horizon), а не календарной константой 2027-03-31 — она
    протухала 01.11.2026."""
    monkeypatch.setattr(core, "today", lambda: date(2026, 8, 14))
    deep_end = core.grid_horizon()
    deep = {"min_prices": {"2026-08-14": None,
                           "2027-03-01": {"p": 7000, "g": 2}}}
    fetch = rs_fetch({"385130": deep, "385131": deep})
    obj, broken = travelline.probe("yck", RS_RECIPE, date(2026, 8, 14),
                                   date(2026, 10, 31), fetch=fetch)
    assert broken is None
    assert f"dto={deep_end.strftime('%d-%m-%Y')}" in obj["source_urls"][0]
    assert len(obj["source_urls"]) == 2  # запросов не прибавилось
    cells = obj["units"]["Стандарт"]
    assert cells["2027-03-01"] == {"state": "free", "price": 7000}
    assert cells[deep_end.isoformat()] == {"state": "unknown"}  # конец окна
    assert (deep_end + timedelta(days=1)).isoformat() not in cells


def test_probe_reservationsteps_schema_change_marks_broken():
    fetch = rs_fetch({"385130": {"совсем": "не то"},
                      "385131": {"тоже": "не то"}})
    obj, broken = travelline.probe("yck", RS_RECIPE, date(2026, 8, 14),
                                   date(2026, 8, 14), fetch=fetch)
    assert broken is not None and "min_prices" in broken
    assert obj["status"] == "insufficient_data"
    assert obj["reason"] == broken


def test_probe_reservationsteps_http_403_is_refusal_not_broken():
    """Тикет 03: 403 — «нас не пустили», а не смена схемы.

    Прежняя редакция ломала рецепт первым же 403, и при 53 целях временный
    WAF чужого хостера выбивал бы рецепты пачками. Теперь снимок несёт
    refusal, а решение «пора ломать» принимает CLI по счётчику суток.
    """
    fetch = rs_fetch({"385130": (403, {"error": "forbidden"}),
                      "385131": (403, {"error": "forbidden"})})
    obj, broken = travelline.probe("yck", RS_RECIPE, date(2026, 8, 14),
                                   date(2026, 8, 14), fetch=fetch)
    assert broken is None
    assert obj["refusal"]["status"] == 403
    assert "нас не пустили" in obj["reason"]
    assert obj["status"] == "insufficient_data"


def test_probe_reservationsteps_http_404_still_marks_broken():
    """Прочие 4xx по-прежнему ломают рецепт: там нужна переразведка."""
    fetch = rs_fetch({"385130": (404, {"error": "gone"}),
                      "385131": (404, {"error": "gone"})})
    obj, broken = travelline.probe("yck", RS_RECIPE, date(2026, 8, 14),
                                   date(2026, 8, 14), fetch=fetch)
    assert broken is not None and "404" in broken
    assert "refusal" not in obj
    assert obj["status"] == "insufficient_data"


def test_probe_reservationsteps_429_does_not_paint_cells_busy():
    """429 на второй категории: её ночи unknown, а не выдуманные busy.

    И счётчик суток отказа объекту не заводится (ревью волны 3): первая
    категория сетку отдала, значит съём состоялся — просто не до конца.
    """
    fetch = rs_fetch({"385130": load("reservationsteps_min_prices.json"),
                      "385131": (429, {"error": "slow down"})})
    obj, broken = travelline.probe("yck", RS_RECIPE, date(2026, 8, 14),
                                   date(2026, 8, 15), fetch=fetch)
    assert broken is None
    assert "refusal" not in obj
    assert obj["status"] == "partial"          # первая категория снялась
    assert "снято не до конца" in obj["reason"] and "429" in obj["reason"]
    assert obj["units"]["Приват"]["2026-08-14"] == {"state": "unknown"}


def test_probe_reservationsteps_transport_refusal_is_not_broken():
    """Отказ, поднятый транспортом (долгий Retry-After), рецепт не ломает."""
    def fetch(url, headers, body=None):
        raise AccessRefused("хост просит подождать 900 с (Retry-After)",
                            status=429, retry_after=900.0)

    obj, broken = travelline.probe("yck", RS_RECIPE, date(2026, 8, 14),
                                   date(2026, 8, 14), fetch=fetch)
    assert broken is None
    assert "Retry-After" in obj["refusal"]["reason"]
    assert obj["status"] == "insufficient_data"


def test_probe_reservationsteps_host_wide_wait_does_not_brand_the_recipe():
    """Ревью волны 4: 429 по СОСЕДНЕЙ цели не заводит счётчик суток этой.

    Просьбу подождать транспорт помнит на хост (_host_not_before) и поднимает
    AccessRefused БЕЗ кода HTTP — запрос по этой цели не отправлялся вовсе.
    На public-api.reservationsteps.ru таких целей 22: считай мы это отказом
    рецепта, один 429 приблизил бы к broken все 22 разом.
    """
    def fetch(url, headers, body=None):
        raise AccessRefused(
            "public-api.reservationsteps.ru просит подождать 900 с "
            "(Retry-After) — объект пропущен, ждать дольше 120 с прогон "
            "не может")

    obj, broken = travelline.probe("yck", RS_RECIPE, date(2026, 8, 14),
                                   date(2026, 8, 14), fetch=fetch)
    assert broken is None
    assert "refusal" not in obj
    assert obj["status"] == "insufficient_data"
    assert "900" in obj["reason"] and "запрос не отправлен" in obj["reason"]


def test_probe_tl_api_403_on_hotel_info_is_refusal_not_broken():
    fetch = tl_fetch(info=(403, {"error": "forbidden"}))
    obj, broken = travelline.probe("istracottage", TL_RECIPE,
                                   date(2026, 8, 21), date(2026, 8, 21),
                                   fetch=fetch)
    assert broken is None
    assert obj["refusal"]["status"] == 403
    assert obj["status"] == "insufficient_data"


def test_probe_tl_api_403_on_calendar_is_refusal_not_broken():
    fetch = tl_fetch(info=load("tl_hotel_info.json"),
                     calendar=(403, {"error": "forbidden"}))
    obj, broken = travelline.probe("istracottage", TL_RECIPE,
                                   date(2026, 8, 21), date(2026, 8, 21),
                                   fetch=fetch)
    assert broken is None
    assert obj["refusal"]["status"] == 403


# ---------------------------------------------------------------------------
# Пробник TL API целиком (фейковая сеть)
# ---------------------------------------------------------------------------

def test_probe_tl_api_per_unit_grid_on_live_fixtures():
    fetch = tl_fetch(info=load("tl_hotel_info.json"),
                     calendar=load("tl_room_type_availability.json"),
                     rules=load("tl_booking_rules.json"))
    obj, broken = travelline.probe("istracottage", TL_RECIPE,
                                   date(2026, 8, 21), date(2026, 8, 27),
                                   fetch=fetch, inventory=False)
    assert broken is None
    assert obj["status"] == "ok"
    assert obj["granularity"] == "per_unit"
    assert len(obj["units"]) == 11
    kupel = obj["units"]["Двухкомнатный дом с горячей купелью"]
    assert kupel["2026-08-21"] == {"state": "busy"}
    assert kupel["2026-08-24"] == {"state": "free", "price": 23300.0}
    kottedzh = obj["units"]["Семейный коттедж с горячей купелью"]
    assert kottedzh["2026-08-21"] == {"state": "free", "price": 32900.0}
    assert kottedzh["2026-08-25"] == {"state": "busy"}
    assert len(obj["source_urls"]) == 3


def test_probe_tl_api_empty_night_semantics():
    today = date.today()
    d1, d2, d3 = (today + timedelta(days=i) for i in (1, 2, 3))
    info = {"hotels": [{
        "room_types": [{"code": "A", "name": "Дом"}],
        "booking_rules": {"availability_max_date": {"duration": 2,
                                                    "time_unit": "day"}},
    }]}
    calendar = {"room_type_availability": [
        {"id_room_type": "A", "availability_date": []}]}
    rules = {"booking_rules": [{"forbidden": True, "date": d1.isoformat()}]}
    obj, broken = travelline.probe(
        "x", TL_RECIPE, d1, d3,
        fetch=tl_fetch(info=info, calendar=calendar, rules=rules),
        inventory=False)
    assert broken is None
    cells = obj["units"]["Дом"]
    assert cells[d1.isoformat()] == {"state": "busy"}           # forbidden
    assert cells[d2.isoformat()] == {"state": "unknown"}        # противоречие
    assert cells[d3.isoformat()] == {"state": "sales_not_open"}  # за окном


def test_probe_tl_api_keeps_nights_beyond_date_to():
    """room_type_availability_2 отдаёт всё окно продаж одним ответом —
    снапшот не режется по date_to (ревью 14.08)."""
    info = {"hotels": [{
        "room_types": [{"code": "A", "name": "Дом"}],
        "booking_rules": {"availability_max_date": {"duration": 1,
                                                    "time_unit": "year"}},
    }]}
    calendar = {"room_type_availability": [
        {"id_room_type": "A", "availability_date": [
            {"is_available": True, "period": {"start_date": "2026-08-22"},
             "price": {"price_before_tax": 100.0}},
            {"is_available": True, "period": {"start_date": "2027-02-14"},
             "price": {"price_before_tax": 200.0}},
        ]}]}
    obj, broken = travelline.probe(
        "x", TL_RECIPE, date(2026, 8, 21), date(2026, 8, 22),
        fetch=tl_fetch(info=info, calendar=calendar,
                       rules={"booking_rules": []}))
    assert broken is None
    cells = obj["units"]["Дом"]
    assert cells["2026-08-22"] == {"state": "free", "price": 100.0}
    assert cells["2027-02-14"] == {"state": "free", "price": 200.0}
    assert cells["2026-12-31"] == {"state": "unknown"}  # между ночами — честно
    assert "2027-02-15" not in cells  # дальше последней ночи не выдумываем


def test_probe_tl_api_rules_failure_degrades_to_partial():
    calendar = {"room_type_availability": [
        {"id_room_type": "128544", "availability_date": []}]}
    info = load("tl_hotel_info.json")
    fetch = tl_fetch(info=info, calendar=calendar,
                     rules=ConnectionError("обрыв"))
    obj, broken = travelline.probe("istracottage", TL_RECIPE,
                                   date(2026, 8, 21), date(2026, 8, 22),
                                   fetch=fetch)
    assert broken is None
    assert obj["status"] == "insufficient_data"  # известных клеток нет вовсе
    assert "агрегатный календарь" in obj["reason"]
    assert obj["units"]["Двухкомнатный дом с горячей купелью"][
        "2026-08-21"] == {"state": "unknown"}


def test_probe_tl_api_schema_change_marks_broken():
    fetch = tl_fetch(info=load("tl_hotel_info.json"),
                     calendar={"room_stays": []},
                     rules=load("tl_booking_rules.json"))
    obj, broken = travelline.probe("istracottage", TL_RECIPE,
                                   date(2026, 8, 21), date(2026, 8, 22),
                                   fetch=fetch)
    assert broken is not None and "room_type_availability" in broken
    assert obj["status"] == "insufficient_data"


def test_probe_tl_api_hotel_info_network_failure_is_not_broken():
    fetch = tl_fetch(info=ConnectionError("нет сети"))
    obj, broken = travelline.probe("istracottage", TL_RECIPE,
                                   date(2026, 8, 21), date(2026, 8, 22),
                                   fetch=fetch)
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "сетевой сбой" in obj["reason"]


def test_probe_tl_api_unknown_calendar_codes_mark_broken():
    info = {"hotels": [{"room_types": [{"code": "A", "name": "Дом"}],
                        "booking_rules": {}}]}
    calendar = {"room_type_availability": [
        {"id_room_type": "B",
         "availability_date": [{"is_available": True,
                                "period": {"start_date": "2026-08-21"},
                                "price": {"price_before_tax": 100.0}}]}]}
    fetch = tl_fetch(info=info, calendar=calendar,
                     rules={"booking_rules": []})
    obj, broken = travelline.probe("x", TL_RECIPE, date(2026, 8, 21),
                                   date(2026, 8, 21), fetch=fetch)
    assert broken is not None and "справочник разъехался" in broken
    assert obj["status"] == "insufficient_data"


# ---------------------------------------------------------------------------
# Диспетчер и реестр
# ---------------------------------------------------------------------------

def test_probe_unknown_template_is_broken():
    recipe = json.loads(json.dumps(RS_RECIPE))
    recipe["request"]["url_template"] = "https://example.com/api?x={date_from}"
    obj, broken = travelline.probe("x", recipe, date(2026, 8, 14),
                                   date(2026, 8, 14), fetch=lambda u, h: (200, {}))
    assert broken is not None
    assert obj["status"] == "insufficient_data"


def test_run_recipe_unsupported_engine_is_honest_not_broken():
    # тикет 03 держал здесь bnovo, тикет 04 — litepms; оба теперь поддержаны,
    # непокрытым остался агрегаторный фоллбек (тикет 05)
    recipe = {"engine": "aggregator-ostrovok", "site": "https://example.com",
              "status": "ok", "request": {}}
    obj, broken = probes.run_recipe("x", recipe, date(2026, 8, 14),
                                    date(2026, 8, 14))
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "aggregator-ostrovok" in obj["reason"]


def test_run_recipe_broken_recipe_is_skipped():
    recipe = json.loads(json.dumps(RS_RECIPE))
    recipe["status"] = "broken"
    obj, broken = probes.run_recipe("x", recipe, date(2026, 8, 14),
                                    date(2026, 8, 14))
    assert broken is None
    assert obj["status"] == "insufficient_data"
    # причины в рецепте нет — остаётся дежурная фраза, но с датой проверки
    assert obj["reason"] == ("рецепт помечен broken (проверено 14.08.2026) — "
                             "ждёт разведки")


def test_broken_recipe_prints_its_own_reason_not_a_duty_phrase():
    """У рецепта с причиной сводка печатает ПРИЧИНУ, а не «переразведка».

    Три из десяти broken-рецептов с живым движком (shale_aframe,
    smr_domik_u_ozera, air.glamping95 на 09.09.2026) — это выключенные самим
    отелем онлайн-продажи. Дежурное «нужна переразведка» звало к ним человека
    каждый день, хотя делать ему там нечего.
    """
    recipe = json.loads(json.dumps(RS_RECIPE))
    recipe["status"] = "broken"
    recipe["broken_reason"] = ("онлайн-продажи выключены самим отелем:\n"
                               "hotel-info отвечает 401")
    obj, broken = probes.run_recipe("x", recipe, date(2026, 8, 14),
                                    date(2026, 8, 14))
    assert broken is None
    assert obj["reason"] == ("рецепт не снимается (проверено 14.08.2026): "
                             "онлайн-продажи выключены самим отелем: "
                             "hotel-info отвечает 401")
    assert "переразведка" not in obj["reason"]


def test_mark_recipe_broken_sets_status_and_reason():
    recipes = {"x": json.loads(json.dumps(RS_RECIPE))}
    probes.mark_recipe_broken(recipes, "x", "схема сменилась")
    assert recipes["x"]["status"] == "broken"
    assert recipes["x"]["broken_reason"] == "схема сменилась"


# ---------------------------------------------------------------------------
# Фонд типов (правка 15.08): календарь знает только «свободно ли хоть что-то
# в типе», а «Кантри дом трёхкомнатный» у istracottage — минимум три номера.
# Остаток снимается hotel_availability: limited_inventory_count.
# ---------------------------------------------------------------------------

def test_parse_hotel_availability_live_fixture():
    rest = travelline.parse_hotel_availability(load("tl_hotel_availability.json"))
    # живой ответ ночи 21-22.08.2026 у istracottage: 9 доступных категорий
    assert rest["128542"] == 2          # «Дом-студия с бочкой фурако» — два номера
    assert rest["77171"] == 1
    assert rest["77169"] is None        # счётчик скрыт: свободных много
    assert "77173" not in rest          # проданной категории в ответе нет вовсе


def test_parse_hotel_availability_schema_change_raises():
    with pytest.raises(travelline.SchemaChanged):
        travelline.parse_hotel_availability({"rooms": []})
    with pytest.raises(travelline.SchemaChanged):
        travelline.parse_hotel_availability(
            {"room_stays": [{"room_types": [{"name": "без кода"}]}]})


def test_apply_inventory_marks_cells_by_homes():
    units = {"А-фрейм": {
        "2026-08-03": {"state": "free"},
        "2026-08-04": {"state": "busy"},
        "2026-08-05": {"state": "free"},
    }}
    by_night = {
        "2026-08-03": {"77168": 1},   # свободен один из трёх
        "2026-08-04": {},             # категории нет -> проданы все
        "2026-08-05": {"77168": 3},   # свободны все три -> это и есть фонд
    }
    units, marked = travelline.apply_inventory(units, {"77168": "А-фрейм"},
                                               by_night)
    assert marked == 3
    assert units["А-фрейм"]["2026-08-03"] == {"state": "free",
                                              "units_total": 3, "units_free": 1}
    assert units["А-фрейм"]["2026-08-04"] == {"state": "busy",
                                              "units_total": 3, "units_free": 0}
    m = core.aggregate(units, ["2026-08"])[0]
    assert m["cuts"]["all"] == {"busy": 5, "known": 9, "pct": 55.6}


def test_apply_inventory_leaves_cell_binary_when_sources_disagree():
    """Календарь и остатки спорят (min-stay, ограничение по гостям) —
    клетку не размечаем: лучше старая бинарная, чем выдуманная."""
    units = {"А-фрейм": {"2026-08-03": {"state": "free"},
                         "2026-08-04": {"state": "busy"}}}
    by_night = {"2026-08-03": {"77168": 0},     # свободно, но остаток 0
                "2026-08-04": {"77168": 2}}     # занято, но остаток 2
    units, marked = travelline.apply_inventory(units, {"77168": "А-фрейм"},
                                               by_night)
    assert marked == 0
    assert units["А-фрейм"]["2026-08-03"] == {"state": "free"}
    assert units["А-фрейм"]["2026-08-04"] == {"state": "busy"}


def test_apply_inventory_skips_types_without_any_count():
    units = {"А-фрейм": {"2026-08-03": {"state": "free"}}}
    units, marked = travelline.apply_inventory(
        units, {"77168": "А-фрейм"}, {"2026-08-03": {"77168": None}})
    assert marked == 0 and units["А-фрейм"]["2026-08-03"] == {"state": "free"}


def test_probe_tl_api_fills_capacity_from_inventory_step():
    info = load("tl_hotel_info.json")
    calendar = load("tl_room_type_availability.json")
    rules = load("tl_booking_rules.json")
    availability = load("tl_hotel_availability.json")
    obj, broken = travelline.probe(
        "istracottage", TL_RECIPE, date(2026, 8, 21), date(2026, 8, 21),
        fetch=tl_fetch(info=info, calendar=calendar, rules=rules,
                       inventory={"2026-08-21": availability}))
    assert broken is None
    studio = obj["units"]["Дом-студия с бочкой фурако"]["2026-08-21"]
    assert studio == {"state": "free", "price": 18200.0,
                      "units_total": 2, "units_free": 2}
    # категория без счётчика осталась бинарной, а не выдуманной
    three_room = obj["units"]["Кантри дом трехкомнатный"]["2026-08-21"]
    assert "units_total" not in three_room and three_room["state"] == "free"
    assert core.unit_basis(obj["units"])["basis"] == "mixed"


def test_probe_tl_api_without_inventory_flag_makes_no_extra_requests():
    calls = []
    base = tl_fetch(info=load("tl_hotel_info.json"),
                    calendar=load("tl_room_type_availability.json"),
                    rules=load("tl_booking_rules.json"))

    def counting(url, headers, body=None):
        calls.append(url)
        return base(url, headers, body)

    obj, _ = travelline.probe("istracottage", TL_RECIPE, date(2026, 8, 21),
                              date(2026, 8, 21), fetch=counting,
                              inventory=False)
    assert not any("hotel_availability" in u for u in calls)
    assert core.unit_basis(obj["units"])["basis"] == "type"


def test_probe_tl_api_inventory_failure_keeps_object_and_says_so():
    def fetch(url, headers, body=None):
        if "hotel_availability" in url:
            return 500, None
        return tl_fetch(info=load("tl_hotel_info.json"),
                        calendar=load("tl_room_type_availability.json"),
                        rules=load("tl_booking_rules.json"))(url, headers, body)

    obj, broken = travelline.probe("istracottage", TL_RECIPE,
                                   date(2026, 8, 21), date(2026, 8, 21),
                                   fetch=fetch)
    assert broken is None                      # шаг вспомогательный, рецепт жив
    assert obj["status"] == "partial"
    assert "фонд типов снят частично" in obj["reason"]
    assert core.unit_basis(obj["units"])["basis"] == "type"


# ---------------------------------------------------------------------------
# Тикет 06: горизонт ФОНДА отдельно от горизонта сетки
# ---------------------------------------------------------------------------
# Шаг фонда — один HTTP-запрос на КАЖДУЮ ночь, то есть почти вся цена
# прогона. После тикета 01 сетка выросла с 58 до 366 ночей, и без отдельного
# горизонта фонда плановый прогон 06:30 не уложился бы в окно таймера.

def _year_hotel_info():
    """hotel_info с окном продаж на год: ночи не уходят в sales_not_open."""
    return {"hotels": [{
        "room_types": [{"code": "A", "name": "Домик"}],
        "booking_rules": {"availability_max_date": {"time_unit": "year",
                                                    "duration": 1}},
    }]}


def _calendar_every_night(code, date_from, date_to):
    """Календарь TL, где категория свободна КАЖДУЮ ночь горизонта."""
    entries = []
    day = date_from
    while day <= date_to:
        entries.append({"period": {"start_date": day.isoformat()},
                        "price": {"price_before_tax": 5000}})
        day += timedelta(days=1)
    return {"room_type_availability": [{"id_room_type": code,
                                        "availability_date": entries}]}


def test_tl_api_inventory_horizon_limits_fund_nights_not_grid():
    """inventory_date_to режет ТОЛЬКО список ночей фонда: сетка остаётся полной."""
    date_from, date_to = date(2026, 9, 4), date(2027, 9, 4)
    inventory_to = date(2026, 9, 13)          # 10 ночей фонда
    calls = []
    base = tl_fetch(info=_year_hotel_info(),
                    calendar=_calendar_every_night("A", date_from, date_to),
                    rules={"booking_rules": []})

    def counting(url, headers, body=None):
        calls.append((url, body))
        return base(url, headers, body)

    obj, broken = probes.run_recipe("x", TL_RECIPE, date_from, date_to,
                                    fetch=counting,
                                    inventory_date_to=inventory_to)
    assert broken is None
    fund = [body for url, body in calls if "hotel_availability" in url]
    assert len(fund) == 10
    nights = sorted(b["criterions"][0]["start_date"] for b in fund)
    assert nights[0] == "2026-09-04" and nights[-1] == "2026-09-13"
    cells = obj["units"]["Домик"]
    assert len(cells) == (date_to - date_from).days + 1   # сетка полная
    assert cells["2027-09-04"]["state"] == "free"
    assert obj["inventory_until"] == "2026-09-13"


def test_tl_api_without_inventory_horizon_keeps_old_behaviour():
    """Обратная совместимость: без inventory_date_to фонд идёт до date_to."""
    calls = []
    base = tl_fetch(info=load("tl_hotel_info.json"),
                    calendar=load("tl_room_type_availability.json"),
                    rules=load("tl_booking_rules.json"))

    def counting(url, headers, body=None):
        calls.append(url)
        return base(url, headers, body)

    obj, _ = travelline.probe("istracottage", TL_RECIPE, date(2026, 8, 21),
                              date(2026, 8, 23), fetch=counting)
    fund = [u for u in calls if "hotel_availability" in u]
    assert len(fund) == 3
    assert obj["inventory_until"] == "2026-08-23"


def test_reservationsteps_inventory_horizon_limits_rooms_requests():
    """Тот же рычаг у Bnovo: /v1/api/rooms спрашивается только до границы."""
    recipe = json.loads(json.dumps(RS_RECIPE))
    recipe["request"]["params"]["account_id"] = "acc-1"
    date_from, date_to = date(2026, 9, 4), date(2027, 9, 4)
    prices = {"min_prices": {
        (date_from + timedelta(days=i)).isoformat(): {"p": 7000, "g": 2}
        for i in range((date_to - date_from).days + 1)}}
    calls = []
    base = rs_fetch({"385130": prices, "385131": prices})

    def counting(url, headers, body=None):
        calls.append(url)
        return base(url, headers, body)

    obj, broken = probes.run_recipe("yck", recipe, date_from, date_to,
                                    fetch=counting,
                                    inventory_date_to=date(2026, 9, 13))
    assert broken is None
    assert len([u for u in calls if "/api/rooms" in u]) == 10
    assert len(obj["units"]["Стандарт"]) >= (date_to - date_from).days + 1
    assert obj["inventory_until"] == "2026-09-13"


# ---------------------------------------------------------------------------
# Тикет 07: минимальный срок проживания не должен молчать
# ---------------------------------------------------------------------------

def test_min_stay_without_room_types_is_said_out_loud():
    """Ветка окон min_stay требует room_types; без них объект молчал.

    Дефект тикета 07: при min_stay>=2 и пустых room_types клетка min_prices
    отвечает «можно ли НАЧАТЬ заезд», и объект показывал почти 100%
    занятости со статусом ok. При 20 самарских bnovo это системный риск.
    """
    recipe = json.loads(json.dumps(RS_RECIPE))
    recipe["request"]["params"] = {"uid": "test-uid", "min_stay": 2}
    prices = {"min_prices": {"2026-09-04": None, "2026-09-05": None,
                             "2026-09-06": {"p": 9000, "g": 2}}}

    def fetch(url, headers, body=None):
        return 200, prices

    obj, broken = travelline.probe("smr_test", recipe, date(2026, 9, 4),
                                   date(2026, 9, 6), fetch=fetch)
    assert broken is None
    assert obj["status"] == "partial"
    assert "минимальный срок" in obj["reason"]
    assert "room_types" in obj["reason"]


def test_min_stay_with_room_types_still_uses_the_windows_branch():
    """Обратная совместимость: с room_types ветка окон работает как раньше."""
    recipe = json.loads(json.dumps(RS_RECIPE))
    recipe["request"]["params"]["min_stay"] = 2
    recipe["request"]["params"]["account_id"] = "acc-1"
    seen = []

    def fetch(url, headers, body=None):
        seen.append(url)
        return 200, {"rooms": [{"id": "385130", "available": 1}]}

    obj, broken = travelline.probe("yck", recipe, date(2026, 9, 4),
                                   date(2026, 9, 5), fetch=fetch)
    assert broken is None
    assert all("/api/rooms" in u for u in seen)
    assert obj["units"]["Стандарт"]["2026-09-04"]["state"] == "free"


# ---------------------------------------------------------------------------
# Тикет 03 + контракт волны 3: страница-заслон приходит с кодом HTTP 200
# ---------------------------------------------------------------------------
# Cloudflare и DDoS-Guard отдают «Just a moment…» с кодом 200 и HTML-телом.
# У JSON-пробника это data=None, то есть «тело не JSON», и до контракта
# волны 3 каскад читал такой ответ как смену схемы и слал живой рецепт на
# переразведку — ровно то, против чего писался тикет 03. Тело последнего
# ответа отдаёт транспорт, пробник передаёт его в classify_response.

CHALLENGE_PAGE = (
    "<!DOCTYPE html><html><head><title>Just a moment...</title></head>"
    "<body><div class=\"cf-browser-verification\">Checking your browser"
    "</div></body></html>")


def test_challenge_page_with_http_200_is_refusal_not_broken():
    """Ветка TL Integration: заслон на первом же из трёх запросов."""
    def fetch(url, headers, body=None):
        return 200, None
    fetch.last_body_text = CHALLENGE_PAGE

    obj, broken = travelline.probe("istracottage", TL_RECIPE,
                                   date(2026, 8, 21), date(2026, 8, 21),
                                   fetch=fetch)
    assert broken is None
    assert "не пустили" in obj["refusal"]["reason"]
    assert obj["status"] == "insufficient_data"


# ---------------------------------------------------------------------------
# Тикет 06 + ревью волны 2: граница съёма названа в самом объекте
# ---------------------------------------------------------------------------
# Сводке нужно отличать «дальше не спрашивали» от «движок не отдал»: это
# разные вещи, и месяц за границей не должен читаться как обычное
# «нет данных». У TravelLine сетка бесплатна и остаётся полной, у соседей с
# поночным шагом (uhotels, ветка min_stay Bnovo) она кончается вместе с
# фондом — поэтому границу объект называет отдельным полем, а не молчит.

def test_tl_api_names_the_grid_horizon_next_to_the_fund_horizon():
    date_from, date_to = date(2026, 9, 4), date(2027, 9, 4)
    fetch = tl_fetch(info=_year_hotel_info(),
                     calendar=_calendar_every_night("A", date_from, date_to),
                     rules={"booking_rules": []})
    obj, broken = probes.run_recipe("x", TL_RECIPE, date_from, date_to,
                                    fetch=fetch,
                                    inventory_date_to=date(2026, 9, 13))
    assert broken is None
    assert obj["grid_until"] == "2027-09-04"      # доступность спрошена на год
    assert obj["inventory_until"] == "2026-09-13"  # фонд — на 10 ночей


# ---------------------------------------------------------------------------
# Ревью волны 4: граница фонда названа ВСЕГДА, даже когда фонд не спрашивали
# ---------------------------------------------------------------------------
# Основание счёта клетки — снят ли у неё фонд — двигается каждый день: фонд
# идёт на ближние 45 ночей, и завтра граница уедет на сутки. Потребитель
# (дифф и сводка) обязан уметь сказать, где эта граница проходила, — иначе
# «вчера тип весил один домик, сегодня три» читается как продажа. Молчание
# поля сводка читала как «фонд снят везде»: объект, у которого шага фонда не
# было вовсе, выглядел как объект с полным фондом.

def test_tl_api_names_the_fund_horizon_even_without_the_fund_step():
    date_from, date_to = date(2026, 9, 4), date(2026, 9, 6)
    fetch = tl_fetch(info=_year_hotel_info(),
                     calendar=_calendar_every_night("A", date_from, date_to),
                     rules={"booking_rules": []})
    obj, broken = probes.run_recipe("x", TL_RECIPE, date_from, date_to,
                                    fetch=fetch, inventory=False)
    assert broken is None
    assert obj["inventory_until"] == "2026-09-04"   # ни одной ночи фонда
    assert core.unit_basis(obj["units"])["basis"] == "type"


def test_reservationsteps_names_the_fund_horizon_without_the_fund_step():
    prices = {"min_prices": {"2026-09-04": {"p": 7000, "g": 2},
                             "2026-09-05": {"p": 7000, "g": 2}}}
    fetch = rs_fetch({"385130": prices, "385131": prices})
    obj, broken = probes.run_recipe("yck", RS_RECIPE, date(2026, 9, 4),
                                    date(2026, 9, 5), fetch=fetch,
                                    inventory=False)
    assert broken is None
    assert obj["inventory_until"] == "2026-09-04"


def test_reservationsteps_without_room_types_still_names_the_fund_horizon():
    """Агрегатный рецепт: категорий нет, фонд спрашивать не у чего."""
    recipe = json.loads(json.dumps(RS_RECIPE))
    recipe["request"]["params"].pop("room_types")
    prices = {"min_prices": {"2026-09-04": {"p": 7000, "g": 2}}}

    def fetch(url, headers, body=None):
        return 200, prices

    obj, broken = probes.run_recipe("yck", recipe, date(2026, 9, 4),
                                    date(2026, 9, 4), fetch=fetch)
    assert broken is None
    assert obj["inventory_until"] == "2026-09-04"


# ---------------------------------------------------------------------------
# Ревью волны 3: авария хостера и сетевой сбой шага фонда рецепт не ломают
# ---------------------------------------------------------------------------
# Правила исхода живут в probes/_outcome.py: 5xx с любым телом — сетевой
# сбой (страница 502 от nginx JSON-ом не приходит НИКОГДА), а рецепт,
# помеченный broken, в следующие прогоны не идёт вовсе, пока агент не
# переразведает объект руками. На одном ru-ibe.tlintegration.ru висят
# восемь живых целей: одна авария хостера снимала их все разом.

def test_reservationsteps_502_without_json_body_keeps_the_recipe_alive():
    fetch = rs_fetch({"385130": (502, None), "385131": (502, None)})
    obj, broken = travelline.probe("yck", RS_RECIPE, date(2026, 8, 14),
                                   date(2026, 8, 15), fetch=fetch)
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "502" in obj["reason"]


def test_tl_api_502_without_json_body_keeps_the_recipe_alive():
    fetch = tl_fetch(info=(502, None))
    obj, broken = travelline.probe("istracottage", TL_RECIPE,
                                   date(2026, 8, 21), date(2026, 8, 22),
                                   fetch=fetch)
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "502" in obj["reason"]


def _min_stay_recipe():
    recipe = json.loads(json.dumps(RS_RECIPE))
    recipe["request"]["params"]["min_stay"] = 2
    recipe["request"]["params"]["account_id"] = "acc-1"
    return recipe


def test_min_stay_network_failure_does_not_break_the_recipe():
    """Ветка окон: обрыв на КАЖДОМ окне — повтор прогона, а не переразведка.

    dacha_limerence — единственный живой объект этой ветки; до правки один
    ночной обрыв снимал его с производства насовсем.
    """
    def fetch(url, headers, body=None):
        raise ConnectionError("обрыв")

    obj, broken = travelline.probe("dacha_limerence", _min_stay_recipe(),
                                   date(2026, 9, 4), date(2026, 9, 6),
                                   fetch=fetch)
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "сетевой сбой" in obj["reason"]
    assert "снимать нечего" not in obj["reason"]


def test_min_stay_5xx_does_not_break_the_recipe():
    def fetch(url, headers, body=None):
        return 503, None

    obj, broken = travelline.probe("dacha_limerence", _min_stay_recipe(),
                                   date(2026, 9, 4), date(2026, 9, 6),
                                   fetch=fetch)
    assert broken is None
    assert "503" in obj["reason"]


def test_min_stay_schema_change_still_breaks_the_recipe():
    """Смена схемы страницы подбора — по-прежнему работа разведчика."""
    def fetch(url, headers, body=None):
        return 200, {"совсем": "не то"}

    obj, broken = travelline.probe("dacha_limerence", _min_stay_recipe(),
                                   date(2026, 9, 4), date(2026, 9, 6),
                                   fetch=fetch)
    assert broken is not None
    assert obj["status"] == "insufficient_data"


# ---------------------------------------------------------------------------
# Предел диапазона у hotel_booking_rules (регресс 04.09 после тикета 01)
# ---------------------------------------------------------------------------

def test_rules_windows_cuts_a_year_into_90_day_chunks():
    from datetime import date as _d
    w = travelline._rules_windows(_d(2026, 9, 4), _d(2027, 9, 4))
    assert w[0] == (_d(2026, 9, 4), _d(2026, 12, 2))
    assert w[-1][1] == _d(2027, 9, 4)
    assert all((b - a).days < travelline.RULES_WINDOW_DAYS for a, b in w)
    # окна идут встык, без дыр и нахлёстов
    for (a1, b1), (a2, _) in zip(w, w[1:]):
        assert (a2 - b1).days == 1


def test_long_horizon_does_not_read_as_a_schema_change():
    """Регресс 04.09: годовой диапазон -> {"errors": ...} без booking_rules.

    До нарезки все 14 рецептов travelline разом отдали insufficient_data со
    словами «схема календаря TL сменилась», хотя у вендора ничего не менялось:
    hotel_booking_rules отказывает на диапазоне длиннее ~91 суток (замер на
    живом отеле 11269: 57 и 91 сутки -> booking_rules, 181 и 365 -> errors).
    Проверяем, что запрос уходит окнами и запрещённые даты из РАЗНЫХ окон
    склеиваются в одну сетку.
    """
    d0 = date.today()
    far = d0 + timedelta(days=200)
    info = {"hotels": [{
        "room_types": [{"code": "A", "name": "Дом"}],
        "booking_rules": {"availability_max_date": {"duration": 1,
                                                    "time_unit": "year"}},
    }]}
    calendar = {"room_type_availability": [
        {"id_room_type": "A", "availability_date": []}]}

    asked = []
    base = tl_fetch(info=info, calendar=calendar, rules={"booking_rules": []})

    def fetch(url, headers, body=None):
        if "hotel_booking_rules" in url:
            asked.append(url)
            start_date = url.split("start_date=")[1][:10]
            # Длинный диапазон вендор отдал бы как errors — проверяем, что
            # пробник длинных и не просит.
            end_date = url.split("end_date=")[1][:10]
            span = (date.fromisoformat(end_date)
                    - date.fromisoformat(start_date)).days
            assert span < travelline.RULES_WINDOW_DAYS, f"окно {span} суток"
            return 200, {"booking_rules": [{"forbidden": True,
                                            "date": start_date}]}
        return base(url, headers, body)

    obj, broken = travelline.probe("x", TL_RECIPE, d0, far,
                                   fetch=fetch, inventory=False)
    assert broken is None
    assert len(asked) >= 3, asked
    busy = {d for d, c in obj["units"]["Дом"].items() if c["state"] == "busy"}
    # первая ночь каждого окна пришла запрещённой — значит склеены все окна
    assert d0.isoformat() in busy
    assert (d0 + timedelta(days=travelline.RULES_WINDOW_DAYS)).isoformat() in busy


# ---------------------------------------------------------------------------
# Предел диапазона у room_type_availability_2 (регресс 05.09 после 50c7f02)
# ---------------------------------------------------------------------------
# Коммит 50c7f02 порезал окнами только hotel_booking_rules, а календарь
# категорий остался одним запросом на весь годовой горизонт. Живой замер
# 05.09 00:05 на отеле 11269: диапазон 2026-09-05..2027-09-05 (366 суток) ->
# HTTP 200, {"room_type_availability": [], "errors": [{"error_code": "320",
# "message": "max 360 days period is allowed in single request"}]};
# 91 сутки -> 137 КБ живого календаря. Парсер читал пустой список как «ни
# одной свободной ночи», а booking_rules поверх красил запретные ночи busy —
# сетка без единой свободной клетки (один TL-объект 05.09 00:00: unknown 3861,
# busy 165, free 0 при 464 free утром на 58 сутках).

def _calendar_window_fetch(info, windows, rules=None):
    """Фейковый fetch TL API, где календарь отвечает ПО ОКНАМ.

    windows — {start_date ISO: ответ | исключение}; окно, которого в словаре
    нет, — ошибка теста (пробник спросил не тот диапазон). Спрошенные
    диапазоны копятся в fetch.asked парами (start, end).
    """
    base = tl_fetch(info=info, rules=rules if rules is not None
                    else {"booking_rules": []})

    def fetch(url, headers, body=None):
        if "room_type_availability_2" in url:
            start = url.split("start_date=")[1][:10]
            end = url.split("end_date=")[1][:10]
            fetch.asked.append((start, end))
            assert start in windows, f"неожиданное окно календаря {start}..{end}"
            response = windows[start]
            if isinstance(response, Exception):
                raise response
            return response if isinstance(response, tuple) else (200, response)
        return base(url, headers, body)
    fetch.asked = []
    return fetch


def _free_night(night, price):
    return {"is_available": True, "period": {"start_date": night},
            "price": {"price_before_tax": price}}


def _info_with_sales_window(days):
    return {"hotels": [{
        "room_types": [{"code": "A", "name": "Дом"},
                       {"code": "B", "name": "Баня"}],
        "booking_rules": {"availability_max_date": {"duration": days,
                                                    "time_unit": "day"}},
    }]}


def test_calendar_year_horizon_is_asked_in_rules_windows_and_glued():
    """Год горизонта -> ровно столько окон календаря, сколько у booking_rules,
    встык и без длинных диапазонов; ответы окон склеиваются по категориям."""
    d0 = date.today()
    far = d0 + timedelta(days=365)
    expected = travelline._rules_windows(d0, far)
    assert len(expected) == 5
    windows = {}
    for win_from, win_to in expected:
        windows[win_from.isoformat()] = {"room_type_availability": [
            {"id_room_type": "A", "availability_date": [
                _free_night(win_from.isoformat(), 100.0),
                _free_night(win_from.isoformat(), 90.0),   # два тарифа: минимум
            ]},
            {"id_room_type": "B", "availability_date": [
                _free_night(win_to.isoformat(), 200.0)]},
        ]}
    fetch = _calendar_window_fetch(_info_with_sales_window(400), windows)
    obj, broken = travelline.probe("x", TL_RECIPE, d0, far,
                                   fetch=fetch, inventory=False)
    assert broken is None
    assert obj["status"] == "ok", obj["reason"]
    assert fetch.asked == [(a.isoformat(), b.isoformat()) for a, b in expected]
    assert all(
        (date.fromisoformat(b) - date.fromisoformat(a)).days
        < travelline.RULES_WINDOW_DAYS for a, b in fetch.asked)
    dom, banya = obj["units"]["Дом"], obj["units"]["Баня"]
    assert len(dom) == 366 and len(banya) == 366
    for win_from, win_to in expected:
        # первая ночь окна свободна у А (минимум из двух тарифов), у Б занята
        assert dom[win_from.isoformat()] == {"state": "free", "price": 90.0}
        assert banya[win_from.isoformat()] == {"state": "busy"}
        # последняя ночь окна свободна у Б, у А занята
        assert banya[win_to.isoformat()] == {"state": "free", "price": 200.0}
        assert dom[win_to.isoformat()] == {"state": "busy"}
    # середина окна: пуста у всех, запрета нет -> unknown
    mid = (expected[1][0] + timedelta(days=10)).isoformat()
    assert dom[mid] == {"state": "unknown"}
    assert obj["grid_until"] == far.isoformat()


def test_calendar_window_rejected_by_vendor_is_unknown_not_busy():
    """«Окно не снялось» и «в окне нет свободных ночей» — разные вещи.

    Отказ вендора (живая форма: пустой список + errors 320) и сетевой сбой
    окна дают unknown даже там, где booking_rules запрещает ночь: календарь
    этих ночей не видел, красить их busy нечем. Соседние окна снимаются как
    обычно, объект — partial с причиной.
    """
    d0 = date.today()
    far = d0 + timedelta(days=365)
    expected = travelline._rules_windows(d0, far)
    rejected = load("tl_room_type_availability_too_long.json")
    windows = {}
    for i, (win_from, win_to) in enumerate(expected):
        if i == 1:
            windows[win_from.isoformat()] = rejected
        elif i == 2:
            windows[win_from.isoformat()] = ConnectionError("обрыв")
        else:
            windows[win_from.isoformat()] = {"room_type_availability": [
                {"id_room_type": "A", "availability_date": [
                    _free_night(win_from.isoformat(), 100.0)]},
                {"id_room_type": "B", "availability_date": []},
            ]}
    # запрет на КАЖДУЮ ночь горизонта: без календаря он красил бы всё busy
    rules = {"booking_rules": [{"forbidden": True, "date": d}
                               for d in core_dates(d0, far)]}
    fetch = _calendar_window_fetch(_info_with_sales_window(400), windows,
                                   rules=rules)
    obj, broken = travelline.probe("x", TL_RECIPE, d0, far,
                                   fetch=fetch, inventory=False)
    assert broken is None                      # рецепт жив: вендор не менялся
    assert obj["status"] == "partial"
    assert "календарь категорий" in obj["reason"]
    assert "320" in obj["reason"]
    assert len(fetch.asked) == 5               # отказ окна не обрывает съём
    dom, banya = obj["units"]["Дом"], obj["units"]["Баня"]
    for i, (win_from, win_to) in enumerate(expected):
        for night in core_dates(win_from, win_to):
            if i in (1, 2):
                assert dom[night] == {"state": "unknown"}, (i, night)
                assert banya[night] == {"state": "unknown"}, (i, night)
            elif night == win_from.isoformat():
                assert dom[night] == {"state": "free", "price": 100.0}
                assert banya[night] == {"state": "busy"}
            else:
                # пусто у всех + forbidden -> busy: окно снялось честно
                assert dom[night] == {"state": "busy"}, (i, night)


def test_calendar_empty_window_is_no_free_nights_not_a_failure():
    """Пустой ответ окна БЕЗ errors — честное «свободных ночей нет»:
    forbidden красит busy, остальное unknown, объект остаётся ok."""
    d0 = date.today()
    far = d0 + timedelta(days=365)
    expected = travelline._rules_windows(d0, far)
    windows = {}
    for i, (win_from, win_to) in enumerate(expected):
        if i == 0:
            windows[win_from.isoformat()] = {"room_type_availability": [
                {"id_room_type": "A", "availability_date": [
                    _free_night(win_from.isoformat(), 100.0)]}]}
        else:
            windows[win_from.isoformat()] = {"room_type_availability": []}
    second = expected[1][0]
    rules = {"booking_rules": [{"forbidden": True,
                                "date": second.isoformat()}]}
    fetch = _calendar_window_fetch(_info_with_sales_window(400), windows,
                                   rules=rules)
    obj, broken = travelline.probe("x", TL_RECIPE, d0, far,
                                   fetch=fetch, inventory=False)
    assert broken is None
    assert obj["status"] == "ok", obj["reason"]
    dom = obj["units"]["Дом"]
    assert dom[second.isoformat()] == {"state": "busy"}
    assert dom[(second + timedelta(days=1)).isoformat()] == {"state": "unknown"}


def test_calendar_sales_not_open_wins_even_in_a_rejected_window():
    """Ночи за availability_max_date — sales_not_open, как и раньше: окно
    продаж знает hotel_info, а не календарь, и отказ окна его не отменяет."""
    d0 = date.today()
    far = d0 + timedelta(days=365)
    expected = travelline._rules_windows(d0, far)
    rejected = load("tl_room_type_availability_too_long.json")
    windows = {w.isoformat(): rejected for w, _ in expected}
    windows[d0.isoformat()] = {"room_type_availability": [
        {"id_room_type": "A", "availability_date": [
            _free_night(d0.isoformat(), 100.0)]}]}
    fetch = _calendar_window_fetch(_info_with_sales_window(120), windows)
    obj, broken = travelline.probe("x", TL_RECIPE, d0, far,
                                   fetch=fetch, inventory=False)
    assert broken is None
    dom = obj["units"]["Дом"]
    assert dom[(d0 + timedelta(days=100)).isoformat()] == {"state": "unknown"}
    assert dom[(d0 + timedelta(days=121)).isoformat()] == {"state": "sales_not_open"}
    assert dom[far.isoformat()] == {"state": "sales_not_open"}


def test_calendar_all_windows_rejected_is_insufficient_data_not_broken():
    d0 = date.today()
    far = d0 + timedelta(days=365)
    rejected = load("tl_room_type_availability_too_long.json")
    windows = {w.isoformat(): rejected
               for w, _ in travelline._rules_windows(d0, far)}
    fetch = _calendar_window_fetch(_info_with_sales_window(400), windows)
    obj, broken = travelline.probe("x", TL_RECIPE, d0, far,
                                   fetch=fetch, inventory=False)
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "320" in obj["reason"]
    assert all(c == {"state": "unknown"}
               for cells in obj["units"].values() for c in cells.values())


def test_calendar_window_4xx_still_breaks_the_recipe():
    """Настоящий отказ схемы (4xx) по-прежнему ломает рецепт — это не про
    диапазон, а про API."""
    d0 = date.today()
    far = d0 + timedelta(days=365)
    windows = {w.isoformat(): (404, None)
               for w, _ in travelline._rules_windows(d0, far)}
    fetch = _calendar_window_fetch(_info_with_sales_window(400), windows)
    obj, broken = travelline.probe("x", TL_RECIPE, d0, far,
                                   fetch=fetch, inventory=False)
    assert broken is not None
    assert obj["status"] == "insufficient_data"


def test_parse_room_type_availability_vendor_errors_are_not_empty_calendar():
    """Живой ответ 05.09 на 366 суток: пустой список + errors 320 — это
    отказ окна, а не календарь без свободных ночей."""
    with pytest.raises(travelline.WindowRejected) as e:
        travelline.parse_room_type_availability(
            load("tl_room_type_availability_too_long.json"))
    assert "320" in str(e.value)
    # пустой errors — не отказ (форма ответа с пустым списком ошибок)
    assert travelline.parse_room_type_availability(
        {"room_type_availability": [], "errors": []}) == {}


def test_tl_grid_58_days_is_byte_identical_to_the_pre_window_code():
    """Регрессия: на 58 сутках (один запрос) сетка та же, что до нарезки.

    Эталон tl_grid_58d_golden.json снят кодом 50c7f02 05.09 00:06 на живых
    фикстурах (horizon 2026-08-21..2026-10-17, inventory=False), без
    checked_at — единственного поля со временем прогона.
    """
    fetch = tl_fetch(info=load("tl_hotel_info.json"),
                     calendar=load("tl_room_type_availability.json"),
                     rules=load("tl_booking_rules.json"))
    obj, broken = travelline.probe("istracottage", TL_RECIPE,
                                   date(2026, 8, 21), date(2026, 10, 17),
                                   fetch=fetch, inventory=False)
    assert broken is None
    obj.pop("checked_at")
    got = json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True)
    golden = (FIXTURES / "tl_grid_58d_golden.json").read_text(encoding="utf-8")
    assert got == golden


def core_dates(date_from, date_to):
    """Ночи date_from..date_to включительно (ISO) — как _iso_dates пробника."""
    return [(date_from + timedelta(days=i)).isoformat()
            for i in range((date_to - date_from).days + 1)]
