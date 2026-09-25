# -*- coding: utf-8 -*-
"""Пробник HomeReserve: живые фикстуры, поштучный фонд, каскад ошибок.

Разведка 15.08.2026 (хвост топ-24, чистый HTTP): движок realtycalendar за
виджетом homereserve.ru отдаёт справочник домиков и посуточный календарь
двумя POST на https://realtycalendar.ru/v2/widget/{token}/. Единица продажи —
конкретный домик, поэтому фонд типов не нужен: клетка весит один домик.

Фикстуры — обезличенные живые ответы (bani_na_ozerah, token объекта; из
записей домиков оставлены id/title/min_stay/sleeps, календарь — как пришёл):
- apartments_bani_na_ozerah.json — 13 домиков объекта на 08-10.09.2026;
- calendar_bani_house.json — календарь домика 298949 «Дом баня Богатырь» на
  15.08-31.10.2026: 78 дней, недоступны 15.08, 26.08 и 27.08.
"""
import json
from datetime import date
from pathlib import Path

import pytest

import probes
from probes import homereserve
from probes._common import SchemaChanged

FIXTURES = Path(__file__).parent / "fixtures" / "homereserve"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


HR_RECIPE = {
    "site": "https://baninaozerah.ru/",
    "engine": "homereserve",
    "status": "ok",
    "request": {
        "url_template": "https://realtycalendar.ru/v2/widget/{token}/calendar",
        "method": "POST",
        "params": {"token": "test-token"},
        "headers": {"Referer": "https://baninaozerah.ru/"},
        "date_substitution": "begin_date/end_date = YYYY-MM-DD в теле POST",
    },
    "discovered_at": "2026-08-15T17:40:00+03:00",
    "notes": "",
    "source_urls": [],
}


def fake_fetch(apartments, calendars, calls=None):
    """Фейковый fetch(url, headers, payload) -> (status, JSON).

    apartments — ответ (или (status, ответ), или исключение) на /apartments;
    calendars — {id домика: ответ} на /calendar.
    """
    def fetch(url, headers, payload=None):
        if calls is not None:
            calls.append((url, payload))
        answer = apartments if url.endswith("/apartments") else \
            calendars[str(payload["apartment_id"])]
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, tuple) else (200, answer)
    return fetch


def recipe_with(**params):
    recipe = json.loads(json.dumps(HR_RECIPE))
    recipe["request"]["params"].update(params)
    return recipe


# ---------------------------------------------------------------------------
# Живые фикстуры: сетка по домикам через диспетчер
# ---------------------------------------------------------------------------

def test_run_recipe_dispatches_homereserve_and_builds_per_unit_grid():
    cal = load("calendar_bani_house.json")
    fetch = fake_fetch(load("apartments_bani_na_ozerah.json"),
                       {"298949": cal})
    obj, broken = probes.run_recipe(
        "bani_na_ozerah", recipe_with(apartment_ids=[298949]),
        date(2026, 8, 15), date(2026, 10, 31), fetch=fetch)
    assert broken is None
    assert obj["status"] == "ok"
    assert obj["engine"] == "homereserve"
    assert obj["granularity"] == "per_unit"
    grid = obj["units"]["г. Ногинск, Дом баня Богатырь"]
    # живая фикстура: недоступны ровно три ночи августа
    assert grid["2026-08-15"]["state"] == "busy"
    assert grid["2026-08-26"]["state"] == "busy"
    assert grid["2026-08-16"]["state"] == "free"
    assert grid["2026-08-16"]["price"] == 23000


def test_apartment_is_one_house_so_cell_weighs_one():
    """Домик движка — физический дом: фонд клетки всегда 1 (не «тип»)."""
    fetch = fake_fetch(load("apartments_bani_na_ozerah.json"),
                       {"298949": load("calendar_bani_house.json")})
    obj, _ = probes.run_recipe("bani_na_ozerah",
                               recipe_with(apartment_ids=[298949]),
                               date(2026, 8, 15), date(2026, 8, 20),
                               fetch=fetch)
    cells = list(obj["units"]["г. Ногинск, Дом баня Богатырь"].values())
    assert all(c["units_total"] == 1 for c in cells if c["state"] != "unknown")
    assert all(c["units_free"] == (1 if c["state"] == "free" else 0)
               for c in cells if c["state"] != "unknown")


def test_horizon_deepens_for_free_one_request_per_house():
    """Глубина бесплатна: один POST на домик отдаёт весь диапазон."""
    calls = []
    fetch = fake_fetch(load("apartments_bani_na_ozerah.json"),
                       {"298949": load("calendar_bani_house.json")},
                       calls=calls)
    probes.run_recipe("bani_na_ozerah", recipe_with(apartment_ids=[298949]),
                      date(2026, 8, 15), date(2026, 10, 31), fetch=fetch)
    calendar_calls = [c for c in calls if c[0].endswith("/calendar")]
    assert len(calendar_calls) == 1
    assert calendar_calls[0][1]["end_date"] >= "2027-03-31"


def test_recipe_ids_select_houses_and_report_missing():
    """Рецепт называет домики: берём ровно их, пропавшие — в reason."""
    fetch = fake_fetch(load("apartments_bani_na_ozerah.json"),
                       {"298949": load("calendar_bani_house.json")})
    obj, broken = probes.run_recipe(
        "bani_na_ozerah", recipe_with(apartment_ids=[298949, 999999]),
        date(2026, 8, 15), date(2026, 8, 20), fetch=fetch)
    assert broken is None
    assert obj["status"] == "partial"
    assert "999999" in obj["reason"]
    assert list(obj["units"]) == ["г. Ногинск, Дом баня Богатырь"]


def test_all_houses_taken_when_recipe_is_silent():
    """Без apartment_ids снимаются все домики выдачи."""
    apartments = {"apartments": [{"id": 1, "title": "Первый"},
                                 {"id": 2, "title": "Второй"}]}
    day = {"calendar": [{"date": "2026-09-01", "available": True,
                         "price": 5000, "min_stay": 2}]}
    fetch = fake_fetch(apartments, {"1": day, "2": day})
    obj, broken = probes.run_recipe("x", HR_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 1), fetch=fetch)
    assert broken is None
    assert sorted(obj["units"]) == ["Второй", "Первый"]


def test_same_titles_are_told_apart_by_id():
    apartments = {"apartments": [{"id": 11, "title": "А-фрейм"},
                                 {"id": 12, "title": "А-фрейм"}]}
    day = {"calendar": [{"date": "2026-09-01", "available": False,
                         "price": None}]}
    fetch = fake_fetch(apartments, {"11": day, "12": day})
    obj, _ = probes.run_recipe("x", HR_RECIPE, date(2026, 9, 1),
                              date(2026, 9, 1), fetch=fetch)
    assert sorted(obj["units"]) == ["А-фрейм [11]", "А-фрейм [12]"]


def test_house_without_title_is_named_by_id():
    apartments = {"apartments": [{"id": 77, "title": ""}]}
    fetch = fake_fetch(apartments, {"77": {"calendar": []}})
    obj, _ = probes.run_recipe("x", HR_RECIPE, date(2026, 9, 1),
                              date(2026, 9, 1), fetch=fetch)
    assert list(obj["units"]) == ["Домик 77"]


def test_dates_outside_answer_stay_unknown():
    apartments = {"apartments": [{"id": 5, "title": "Дом"}]}
    fetch = fake_fetch(apartments, {"5": {"calendar": [
        {"date": "2026-09-02", "available": True, "price": 100}]}})
    obj, _ = probes.run_recipe("x", HR_RECIPE, date(2026, 9, 1),
                              date(2026, 9, 3), fetch=fetch)
    grid = obj["units"]["Дом"]
    assert grid["2026-09-01"] == {"state": "unknown"}
    assert grid["2026-09-02"]["state"] == "free"


# ---------------------------------------------------------------------------
# Каскад ошибок: 4xx -> broken, 5xx/сеть -> рецепт жив
# ---------------------------------------------------------------------------

def test_403_on_apartments_is_refusal_not_broken():
    """Тикет 03: 403 — отказ хоста. Рецепт жив, снимок несёт refusal."""
    fetch = fake_fetch((403, {"message": "нет"}), {})
    obj, broken = probes.run_recipe("x", HR_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 2), fetch=fetch)
    assert broken is None
    assert obj["refusal"]["status"] == 403
    assert obj["status"] == "insufficient_data"


def test_404_on_apartments_still_breaks_recipe():
    """Прочие 4xx — по-прежнему смена схемы/адреса, рецепт на переразведку."""
    fetch = fake_fetch((404, {"message": "нет"}), {})
    obj, broken = probes.run_recipe("x", HR_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 2), fetch=fetch)
    assert broken and "HTTP 404" in broken
    assert "refusal" not in obj


def test_429_on_calendar_leaves_cells_unknown_not_busy():
    apartments = {"apartments": [{"id": 5, "title": "Дом"}]}
    fetch = fake_fetch(apartments, {"5": (429, {})})
    obj, broken = probes.run_recipe("x", HR_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 2), fetch=fetch)
    assert broken is None
    assert obj["refusal"]["status"] == 429
    assert all(c["state"] == "unknown" for c in obj["units"]["Дом"].values())


def test_5xx_on_calendar_keeps_recipe_alive():
    apartments = {"apartments": [{"id": 5, "title": "Дом"}]}
    fetch = fake_fetch(apartments, {"5": (503, {})})
    obj, broken = probes.run_recipe("x", HR_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 2), fetch=fetch)
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "HTTP 503" in obj["reason"]
    assert all(c["state"] == "unknown" for c in obj["units"]["Дом"].values())


def test_network_failure_keeps_recipe_alive():
    apartments = {"apartments": [{"id": 5, "title": "Дом"}]}
    fetch = fake_fetch(apartments, {"5": OSError("таймаут")})
    obj, broken = probes.run_recipe("x", HR_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 2), fetch=fetch)
    assert broken is None
    assert "сетевой сбой" in obj["reason"]


def test_recipe_without_token_is_honest():
    recipe = json.loads(json.dumps(HR_RECIPE))
    recipe["request"]["params"] = {}
    obj, broken = probes.run_recipe("x", recipe, date(2026, 9, 1),
                                    date(2026, 9, 2), fetch=fake_fetch({}, {}))
    assert broken and "token" in broken
    assert obj["status"] == "insufficient_data"


def test_schema_change_breaks_recipe():
    fetch = fake_fetch({"items": []}, {})
    obj, broken = probes.run_recipe("x", HR_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 2), fetch=fetch)
    assert broken and "apartments" in broken


def test_non_boolean_available_is_schema_change():
    with pytest.raises(SchemaChanged):
        homereserve.parse_calendar(
            {"calendar": [{"date": "2026-09-01", "available": "yes"}]},
            ["2026-09-01"])


def test_empty_apartments_list_is_not_a_crash():
    fetch = fake_fetch({"apartments": []}, {})
    obj, broken = probes.run_recipe("x", HR_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 2), fetch=fetch)
    assert broken and "ни одного домика" in broken


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
    fetch = fake_fetch((200, None), {})
    fetch.last_body_text = CHALLENGE_PAGE
    obj, broken = probes.run_recipe("bani_na_ozerah", HR_RECIPE,
                                    date(2026, 8, 15), date(2026, 8, 16),
                                    fetch=fetch)
    assert broken is None
    assert "не пустили" in obj["refusal"]["reason"]
    assert obj["status"] == "insufficient_data"


# ---------------------------------------------------------------------------
# Ревью волны 3: авария хостера, сетевой сбой справочника, частичный съём
# ---------------------------------------------------------------------------

def test_502_without_json_body_on_apartments_keeps_the_recipe_alive():
    """Страница nginx с кодом 502 — сетевой сбой, а не смена схемы."""
    fetch = fake_fetch((502, None), {})
    obj, broken = probes.run_recipe("x", HR_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 2), fetch=fetch)
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "502" in obj["reason"]


def test_network_failure_on_the_directory_does_not_break_the_recipe():
    """Обрыв на справочнике домиков — повод повторить прогон, а не звать
    агента: рецепт, помеченный broken, в следующие прогоны не идёт вовсе."""
    fetch = fake_fetch(ConnectionError("обрыв"), {})
    obj, broken = probes.run_recipe("x", HR_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 2), fetch=fetch)
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "сетевой сбой" in obj["reason"]
    assert "снимать нечего" not in obj["reason"]


def test_empty_directory_without_a_failure_still_breaks_the_recipe():
    """Движок ответил и домиков не отдал — это разъехавшийся рецепт."""
    fetch = fake_fetch({"apartments": []}, {})
    obj, broken = probes.run_recipe("x", HR_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 2), fetch=fetch)
    assert broken and "ни одного домика" in broken


def test_403_after_the_first_house_was_taken_is_not_a_refusal():
    apartments = {"apartments": [{"id": 5, "title": "Дом"},
                                 {"id": 6, "title": "Баня"}]}
    fetch = fake_fetch(apartments, {
        "5": {"calendar": [{"date": "2026-09-01", "available": True}]},
        "6": (403, None)})
    obj, broken = probes.run_recipe("x", HR_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 1), fetch=fetch)
    assert broken is None
    assert "refusal" not in obj
    assert obj["status"] == "partial"
    assert "снято не до конца" in obj["reason"]
