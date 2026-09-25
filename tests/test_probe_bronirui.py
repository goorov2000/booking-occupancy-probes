# -*- coding: utf-8 -*-
"""Пробник «Бронируй Онлайн» (bronirui-online): живые фикстуры, ошибки.

Разведка 14.08.2026 (тикет 04, чистый HTTP без браузера): виджет znms
(widget.bronirui-online.ru) ходит POST-ами в api.bronirui-online.ru/v2.
Календарь ночей — POST widget/calendar с JSON-телом {module_id, number_id,
dateFrom, dateTo, ...}; поля тела — snake_case module_id (волна 2 слала
camelCase moduleId и упиралась в 401). Ответ: dates.elements{дата: {isClosed,
price, ...}}. isClosed=true — ночь не продаётся (занята/закрыта — неотличимо,
оценка сверху), isClosed=false — свободна, price — цена за ночь.

Фикстуры — обезличенные живые ответы api.bronirui-online.ru, снятые
14.08.2026 (тела — только даты/цены/флаги, без cookies/токенов):
- widget_calendar_vlesu_aframe15.json — vlesu_glamping (bronirui.online/
  vlesu-glamp, module_id=3844), номер 10043 «A-frame дом (с 15:00 до 12:00)»,
  14.08-30.09.2026: август закрыт 12/18, сентябрь закрыт только 04-06.09;
- unauthorized_module_off.json — живой 401 модуля shale_aframe (7130):
  «Отель временно не принимает бронирования онлайн» (телефон замаскирован).
"""
import json
from datetime import date, timedelta
from pathlib import Path

import occupancy_core as core
import probes
from probes import bronirui

FIXTURES = Path(__file__).parent / "fixtures" / "bronirui"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


BRONIRUI_RECIPE = {
    "site": "https://bronirui.online/vlesu-glamp",
    "engine": "bronirui",
    "status": "ok",
    "request": {
        "url_template": "https://api.bronirui-online.ru/v2/widget/calendar",
        "method": "POST",
        "params": {
            "module_id": 3844,
            "numbers": {"10043": "A-frame дом (с 15:00 до 12:00)",
                        "731": "A-frame дом (с 16:00 до 13:00)"},
        },
        "headers": {"Referer": "https://bronirui.online/vlesu-glamp",
                    "Origin": "https://bronirui.online"},
        "date_substitution": "dateFrom/dateTo = YYYY-MM-DD в JSON-теле; "
                             "один POST на каждый номер из params.numbers",
    },
    "discovered_at": "2026-08-14T20:00:00+03:00",
    "notes": "",
    "source_urls": [],
}


def fake_fetch(responses):
    """Фейковый fetch: number_id из тела -> ответ (payload | (status, payload) | исключение)."""
    def fetch(url, headers, payload):
        number_id = str(payload.get("number_id"))
        if number_id not in responses:
            raise AssertionError(f"неожиданный number_id: {number_id}")
        answer = responses[number_id]
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, tuple) else (200, answer)
    return fetch


def small_calendar(cells):
    """Мини-ответ движка: {дата: (isClosed, price)} -> dates.elements."""
    return {"dates": {"elements": {
        d: {"isClosed": closed, "price": price}
        for d, (closed, price) in cells.items()
    }, "firstAvailableDate": None}}


# ---------------------------------------------------------------------------
# Живая фикстура vlesu_glamping: сетка по номерам через диспетчер
# ---------------------------------------------------------------------------

def test_run_recipe_dispatches_bronirui_and_builds_per_unit_grid():
    fetch = fake_fetch({
        "10043": load("widget_calendar_vlesu_aframe15.json"),
        "731": small_calendar({"2026-08-20": (True, None),
                               "2026-08-21": (False, 9000)}),
    })
    obj, broken = probes.run_recipe("vlesu_glamping", BRONIRUI_RECIPE,
                                    date(2026, 8, 14), date(2026, 8, 31),
                                    fetch=fetch)
    assert broken is None
    assert obj["status"] == "ok"
    assert obj["engine"] == "bronirui"
    assert obj["granularity"] == "per_unit"
    aframe15 = obj["units"]["A-frame дом (с 15:00 до 12:00)"]
    # Справочник рецепта в старой плоской форме: фонда движок не дал, и
    # клетка идёт БЕЗ пары units_total/units_free — иначе снимок выдавал бы
    # догадку «один домик» за снятый фонд (ревью волны 3).
    assert aframe15["2026-08-14"] == {"state": "busy"}
    assert aframe15["2026-08-20"] == {"state": "free", "price": 9000}
    assert obj["units"]["A-frame дом (с 16:00 до 13:00)"]["2026-08-20"] == {
        "state": "busy"}
    assert len(obj["source_urls"]) == 2


def test_live_fixture_august_and_september_pattern():
    """Август vlesu по живой фикстуре: закрыто 12/18; сентябрь — только 04-06."""
    fetch = fake_fetch({"10043": load("widget_calendar_vlesu_aframe15.json"),
                        "731": small_calendar({})})
    obj, broken = probes.run_recipe("vlesu_glamping", BRONIRUI_RECIPE,
                                    date(2026, 8, 14), date(2026, 9, 30),
                                    fetch=fetch)
    assert broken is None
    cells = obj["units"]["A-frame дом (с 15:00 до 12:00)"]
    aug_busy = [d for d, c in cells.items()
                if d.startswith("2026-08") and c["state"] == "busy"]
    aug_free = [d for d, c in cells.items()
                if d.startswith("2026-08") and c["state"] == "free"]
    assert len(aug_busy) == 12 and len(aug_free) == 6
    assert aug_free == ["2026-08-20", "2026-08-24", "2026-08-25",
                        "2026-08-26", "2026-08-30", "2026-08-31"]
    sep_busy = [d for d, c in cells.items()
                if d.startswith("2026-09") and c["state"] == "busy"]
    assert sep_busy == ["2026-09-04", "2026-09-05", "2026-09-06"]
    # свободная ночь несёт цену
    assert cells["2026-09-12"]["price"] == 13000


def test_dates_missing_from_elements_become_unknown():
    """Ночи, которых нет в dates.elements, честно unknown (не busy и не free)."""
    fetch = fake_fetch({"10043": small_calendar({"2026-10-01": (False, 9000)}),
                        "731": small_calendar({"2026-10-01": (True, None)})})
    obj, broken = probes.run_recipe("vlesu_glamping", BRONIRUI_RECIPE,
                                    date(2026, 10, 1), date(2026, 10, 3),
                                    fetch=fetch)
    assert broken is None
    cells = obj["units"]["A-frame дом (с 15:00 до 12:00)"]
    assert cells["2026-10-01"] == {"state": "free", "price": 9000}
    assert cells["2026-10-02"] == {"state": "unknown"}
    assert cells["2026-10-03"] == {"state": "unknown"}


def test_probe_deepens_date_to_in_payload_and_grid(monkeypatch):
    """Ревью 14.08: dateTo расширяется — тот же набор POST-ов (по одному на
    номер), глубина живёт в снапшоте, не в сводке.

    Правка 04.09 (тикет 01): глубина больше не константа 2027-03-31, а
    скользящий горизонт от сегодня (core.grid_horizon) — константа протухала
    01.11.2026. Поэтому «сегодня» здесь подменено, а ожидаемый конец
    горизонта берётся из ядра, а не выписан годом."""
    monkeypatch.setattr(core, "today", lambda: date(2026, 8, 14))
    deep_end = core.grid_horizon().isoformat()
    seen = []

    def fetch(url, headers, payload):
        seen.append(payload)
        return 200, small_calendar({"2027-03-01": (False, 9000)})

    obj, broken = probes.run_recipe("vlesu_glamping", BRONIRUI_RECIPE,
                                    date(2026, 8, 14), date(2026, 10, 31),
                                    fetch=fetch)
    assert broken is None
    assert len(seen) == 2  # запросов не прибавилось: один POST на номер
    assert all(p["dateFrom"] == "2026-08-14" for p in seen)
    assert all(p["dateTo"] == deep_end for p in seen)
    cells = obj["units"]["A-frame дом (с 15:00 до 12:00)"]
    assert cells["2027-03-01"] == {"state": "free", "price": 9000}
    assert cells[deep_end] == {"state": "unknown"}
    assert (date.fromisoformat(deep_end) + timedelta(days=1)).isoformat() \
        not in cells


# ---------------------------------------------------------------------------
# Ошибки: сеть не ломает рецепт, схема/4xx/выключенный модуль ломают
# ---------------------------------------------------------------------------

def test_module_off_401_marks_broken_with_hotel_message():
    """Живой 401 shale_aframe: модуль выключен отелем -> broken с его текстом."""
    err = load("unauthorized_module_off.json")
    fetch = fake_fetch({"10043": (401, err), "731": (401, err)})
    obj, broken = probes.run_recipe("shale_aframe", BRONIRUI_RECIPE,
                                    date(2026, 8, 14), date(2026, 8, 15),
                                    fetch=fetch)
    assert broken is not None
    assert "401" in broken
    assert "не принимает бронирования" in broken
    assert obj["status"] == "insufficient_data"
    assert obj["reason"] == broken


def test_403_is_refusal_not_broken():
    """Тикет 03: 403 — отказ хоста; 401 выключенного модуля по-прежнему
    ломает рецепт (тест выше), потому что это ответ ОТЕЛЯ, а не заслон."""
    fetch = fake_fetch({"10043": (403, {"message": "forbidden"}),
                        "731": (403, {"message": "forbidden"})})
    obj, broken = probes.run_recipe("vlesu_glamping", BRONIRUI_RECIPE,
                                    date(2026, 8, 14), date(2026, 8, 15),
                                    fetch=fetch)
    assert broken is None
    assert obj["refusal"]["status"] == 403
    assert obj["status"] == "insufficient_data"


def test_network_failure_on_one_number_is_partial_not_broken():
    fetch = fake_fetch({"10043": ConnectionError("обрыв"),
                        "731": small_calendar({"2026-08-14": (False, 9000)})})
    obj, broken = probes.run_recipe("vlesu_glamping", BRONIRUI_RECIPE,
                                    date(2026, 8, 14), date(2026, 8, 14),
                                    fetch=fetch)
    assert broken is None
    assert obj["status"] == "partial"
    assert "сетевой сбой" in obj["reason"]
    assert obj["units"]["A-frame дом (с 15:00 до 12:00)"]["2026-08-14"] == {
        "state": "unknown"}


def test_network_failure_on_all_numbers_is_insufficient_data():
    fetch = fake_fetch({"10043": ConnectionError("таймаут"),
                        "731": ConnectionError("таймаут")})
    obj, broken = probes.run_recipe("vlesu_glamping", BRONIRUI_RECIPE,
                                    date(2026, 8, 14), date(2026, 8, 15),
                                    fetch=fetch)
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "сетевой сбой" in obj["reason"]


def test_schema_change_marks_broken():
    fetch = fake_fetch({"10043": {"совсем": "не то"},
                        "731": {"тоже": "не то"}})
    obj, broken = probes.run_recipe("vlesu_glamping", BRONIRUI_RECIPE,
                                    date(2026, 8, 14), date(2026, 8, 14),
                                    fetch=fetch)
    assert broken is not None and "dates" in broken
    assert obj["status"] == "insufficient_data"


def test_non_json_body_marks_broken():
    fetch = fake_fetch({"10043": (200, None), "731": (200, None)})
    obj, broken = probes.run_recipe("vlesu_glamping", BRONIRUI_RECIPE,
                                    date(2026, 8, 14), date(2026, 8, 14),
                                    fetch=fetch)
    assert broken is not None and "не JSON" in broken
    assert obj["status"] == "insufficient_data"


def test_recipe_without_numbers_is_honest_insufficient_data():
    """Без params.numbers сетки нет: календарь с number_id=null отдаёт всё
    закрытым (живая проверка 14.08) — агрегатной выдачи у движка нет."""
    recipe = json.loads(json.dumps(BRONIRUI_RECIPE))
    del recipe["request"]["params"]["numbers"]
    obj, broken = probes.run_recipe("vlesu_glamping", recipe,
                                    date(2026, 8, 14), date(2026, 8, 15),
                                    fetch=fake_fetch({}))
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "numbers" in obj["reason"]
    assert obj["units"] == {}


def test_bronirui_registered_in_dispatcher():
    assert probes.ENGINES["bronirui"] is bronirui


# ---------------------------------------------------------------------------
# Несколько домиков одного названия (вопрос заказчика 15.08): до правки второй
# номер молча затирал первый в сетке — занятый домик пропадал вместе с уже
# оплаченным запросом
# ---------------------------------------------------------------------------

DUP_RECIPE = {
    "site": "https://bronirui.online/dup",
    "engine": "bronirui",
    "status": "ok",
    "request": {
        "url_template": "https://api.bronirui-online.ru/v2/widget/calendar",
        "method": "POST",
        "params": {"module_id": 42,
                   "numbers": {"111": "A-frame дом", "222": "A-frame дом",
                               "333": "Купол"}},
        "headers": {},
        "date_substitution": "dateFrom/dateTo = YYYY-MM-DD",
    },
    "discovered_at": "2026-08-15T12:00:00+03:00",
    "notes": "",
    "source_urls": [],
}


def test_same_named_numbers_stay_separate_units():
    def fetch(url, headers, payload):
        closed = payload["number_id"] == 111
        return 200, {"dates": {"elements": {
            "2026-08-20": {"isClosed": closed,
                           "price": None if closed else 9000}}}}

    obj, broken = probes.run_recipe("dup_obj", DUP_RECIPE,
                                    date(2026, 8, 20), date(2026, 8, 20),
                                    fetch=fetch)
    assert broken is None
    # три номера — три юнита; дубли различаются number_id, уникальное имя не трогаем
    assert set(obj["units"]) == {"A-frame дом [111]", "A-frame дом [222]",
                                 "Купол"}
    assert obj["units"]["A-frame дом [111]"]["2026-08-20"]["state"] == "busy"
    assert obj["units"]["A-frame дом [222]"]["2026-08-20"]["state"] == "free"
    # занятость считается по домикам: 1 занят из 3
    m = core.aggregate(obj["units"], ["2026-08"])[0]
    assert m["cuts"]["all"] == {"busy": 1, "known": 3, "pct": 33.3}


def test_number_without_rooms_count_is_not_called_a_home():
    """Справочник без rooms_count -> объект считается по ТИПАМ.

    Ревью волны 3: движок про фонд номера не сказал ничего, и называть
    номер домиком нельзя — под одним number_id живая разведка 04.09 видела
    rooms_count=3. Цифры при этом прежние (клетка весит один домик), меняется
    только подпись, которую читает человек.
    """
    def fetch(url, headers, payload):
        return 200, {"dates": {"elements": {
            "2026-08-20": {"isClosed": False, "price": 9000}}}}

    obj, _ = probes.run_recipe("dup_obj", DUP_RECIPE,
                               date(2026, 8, 20), date(2026, 8, 20),
                               fetch=fetch)
    assert core.unit_basis(obj["units"])["basis"] == "type"
    assert "фонд не снят" in core.basis_note(obj["units"])
    assert "по домикам" not in core.basis_note(obj["units"])


# ---------------------------------------------------------------------------
# Тикет 04 + ревью волны 4: rooms_count — это ФОНД номера, а не остаток
# ---------------------------------------------------------------------------
# До 04.09 пробник ставил units_total=1 на каждую клетку, а поле rooms_count
# из ответа /v2/numbers не читал никто (grep по репозиторию был пуст). Живая
# разведка 04.09 показала rooms_count=3 у одного number_id.
#
# Ревью волны 4 поймало на этом обещание, которого движок не выполняет:
# widget/calendar отвечает про number_id одним булевым isClosed — «продаётся
# ли под этим номером ХОТЬ ЧТО-ТО», — и пара units_total=3/units_free=3
# выдавала эту единственную известную нам вещь за поштучный остаток. Живой
# ответ движка (фикстура widget_calendar_vlesu_aframe15.json, снята 14.08)
# несёт по ночи ровно шесть полей: date, minNight, minNightArrival, maxNight,
# price, isClosed (+isClosedOnArrival/Departure). Числа свободных домиков
# среди них нет — доказать поштучный счёт нечем, и мы его не заявляем.
#
# Отсюда правило: пара пишется ТОЛЬКО при rooms_count=1 — там бинарный ответ
# движка и есть остаток по домику (свободен ровно один из одного). Фонд
# больше единицы остаётся в рецепте и в пометке причины, но клетка считается
# по типу, и сводка говорит это вслух. Самарских объектов на движке восемь,
# фонд больше единицы у трёх (smr_dvoryanovka 2+2, smr_eto_baza 3,
# smr_glemping_lakeville 4).

def recipe_with_numbers(numbers, **params):
    recipe = json.loads(json.dumps(BRONIRUI_RECIPE))
    recipe["request"]["params"]["numbers"] = numbers
    recipe["request"]["params"].update(params)
    return recipe


def test_single_home_number_is_counted_by_homes():
    """rooms_count=1 — единственный фонд, который календарь и правда знает.

    Под номером один домик, и бинарный ответ движка о нём — это и есть
    остаток: свободен один из одного, занят ноль из одного.
    """
    recipe = recipe_with_numbers({"731": {"name": "Купол", "rooms_count": 1}})
    fetch = fake_fetch({"731": small_calendar({"2026-08-20": (True, None),
                                               "2026-08-21": (False, 5000)})})
    obj, broken = probes.run_recipe("smr_test", recipe, date(2026, 8, 20),
                                    date(2026, 8, 21), fetch=fetch)
    assert broken is None
    kupol = obj["units"]["Купол"]
    assert kupol["2026-08-20"] == {"state": "busy", "units_total": 1,
                                   "units_free": 0}
    assert kupol["2026-08-21"] == {"state": "free", "units_total": 1,
                                   "units_free": 1, "price": 5000}
    assert core.unit_basis(obj["units"])["basis"] == "unit"


def test_fund_above_one_does_not_become_a_per_home_count():
    """rooms_count=3: движок не говорит, сколько из трёх свободно.

    Главный дефект ревью волны 4. Пара units_total=3/units_free=3 обещала
    читателю поштучный счёт, которого widget/calendar не даёт: его isClosed —
    это «продаётся ли хоть один», а не «сколько». Клетка идёт без пары, а
    причина объекта называет номер и его фонд.
    """
    recipe = recipe_with_numbers({
        "10043": {"name": "A-frame дом", "rooms_count": 3},
        "731": {"name": "Купол", "rooms_count": 1}})
    fetch = fake_fetch({"10043": small_calendar({"2026-08-20": (False, 9000),
                                                 "2026-08-21": (True, None)}),
                        "731": small_calendar({"2026-08-20": (True, None),
                                               "2026-08-21": (False, 5000)})})
    obj, broken = probes.run_recipe("smr_test", recipe, date(2026, 8, 20),
                                    date(2026, 8, 21), fetch=fetch)
    assert broken is None
    aframe = obj["units"]["A-frame дом"]
    assert aframe["2026-08-20"] == {"state": "free", "price": 9000}
    assert aframe["2026-08-21"] == {"state": "busy"}
    assert obj["units"]["Купол"]["2026-08-20"] == {"state": "busy",
                                                   "units_total": 1,
                                                   "units_free": 0}
    assert "поштучного остатка" in obj["reason"]
    assert "10043" in obj["reason"] and "×3" in obj["reason"]  # фонд назван


def test_rooms_count_can_live_in_a_separate_params_map():
    """Фонд можно положить и отдельной картой — рецепт правится руками."""
    recipe = recipe_with_numbers({"10043": "A-frame дом", "731": "Купол"},
                                 rooms_count={"10043": 1})
    fetch = fake_fetch({"10043": small_calendar({"2026-08-20": (False, 9000)}),
                        "731": small_calendar({"2026-08-20": (True, None)})})
    obj, _ = probes.run_recipe("smr_test", recipe, date(2026, 8, 20),
                              date(2026, 8, 20), fetch=fetch)
    assert obj["units"]["A-frame дом"]["2026-08-20"]["units_total"] == 1
    assert "units_total" not in obj["units"]["Купол"]["2026-08-20"]


def test_numbers_without_rooms_count_keep_previous_numbers():
    """Обратная совместимость: справочник без rooms_count — прежние цифры,
    но объект честно говорит, что фонд не проверялся."""
    fetch = fake_fetch({"10043": small_calendar({"2026-08-20": (False, 9000)}),
                        "731": small_calendar({"2026-08-20": (True, None)})})
    obj, broken = probes.run_recipe("vlesu_glamping", BRONIRUI_RECIPE,
                                    date(2026, 8, 20), date(2026, 8, 20),
                                    fetch=fetch)
    assert broken is None
    assert obj["status"] == "ok"               # это не «неполный снимок»
    assert obj["units"]["A-frame дом (с 15:00 до 12:00)"]["2026-08-20"] == {
        "state": "free", "price": 9000}
    assert "без фонда" in obj["reason"]


def test_non_integer_rooms_count_falls_back_to_one():
    """Мусор в поле — не повод выдумывать фонд: остаётся один домик."""
    recipe = recipe_with_numbers({"10043": {"name": "Дом", "rooms_count": "три"},
                                  "731": {"name": "Купол",
                                          "rooms_count": True}})
    fetch = fake_fetch({"10043": small_calendar({"2026-08-20": (False, 900)}),
                        "731": small_calendar({"2026-08-20": (False, 900)})})
    obj, _ = probes.run_recipe("smr_test", recipe, date(2026, 8, 20),
                               date(2026, 8, 20), fetch=fetch)
    assert "units_total" not in obj["units"]["Дом"]["2026-08-20"]
    assert "units_total" not in obj["units"]["Купол"]["2026-08-20"]
    assert "без фонда" in obj["reason"]


def test_duplicate_names_with_rooms_count_are_still_told_apart():
    recipe = recipe_with_numbers({
        "10043": {"name": "A-frame", "rooms_count": 2},
        "731": {"name": "A-frame", "rooms_count": 1}})
    fetch = fake_fetch({"10043": small_calendar({"2026-08-20": (False, 900)}),
                        "731": small_calendar({"2026-08-20": (False, 900)})})
    obj, _ = probes.run_recipe("smr_test", recipe, date(2026, 8, 20),
                               date(2026, 8, 20), fetch=fetch)
    assert sorted(obj["units"]) == ["A-frame [10043]", "A-frame [731]"]
    # фонд 2 остатка не даёт (ревью волны 4), фонд 1 — даёт
    assert "units_total" not in obj["units"]["A-frame [10043]"]["2026-08-20"]
    assert obj["units"]["A-frame [731]"]["2026-08-20"]["units_total"] == 1


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
    fetch = fake_fetch({"10043": (200, None), "731": (200, None)})
    fetch.last_body_text = CHALLENGE_PAGE
    obj, broken = probes.run_recipe("vlesu_glamping", BRONIRUI_RECIPE,
                                    date(2026, 8, 14), date(2026, 8, 15),
                                    fetch=fetch)
    assert broken is None                      # рецепт остаётся живым
    assert "не пустили" in obj["refusal"]["reason"]
    assert obj["status"] == "insufficient_data"


# ---------------------------------------------------------------------------
# Контракт волны 3 со скаутом: канонический вид справочника номеров
# ---------------------------------------------------------------------------
# Пробник читает params.numbers в виде {"<number_id>": {"name": ...,
# "rooms_count": N}} — ровно то, что отдаёт /v2/numbers движка. Старый
# плоский вид {"<number_id>": "имя"} тоже читается: живые рецепты 04.09
# написаны им, и переписывать их разом никто не будет.

def test_canonical_and_flat_directories_live_side_by_side():
    """Скаут мигрирует рецепты по одному — обе формы обязаны работать разом."""
    recipe = recipe_with_numbers({
        "10043": {"name": "A-frame дом", "rooms_count": 1},   # канонический
        "731": "Купол"})                                      # старый плоский
    fetch = fake_fetch({"10043": small_calendar({"2026-08-20": (False, 9000)}),
                        "731": small_calendar({"2026-08-20": (False, 5000)})})
    obj, broken = probes.run_recipe("smr_test", recipe, date(2026, 8, 20),
                                    date(2026, 8, 20), fetch=fetch)
    assert broken is None
    assert obj["units"]["A-frame дом"]["2026-08-20"]["units_total"] == 1
    assert "units_total" not in obj["units"]["Купол"]["2026-08-20"]
    # предупреждение называет ТОЛЬКО номер без фонда, а не весь объект
    assert "731" in obj["reason"] and "10043" not in obj["reason"]


def test_directory_reads_the_shape_scout_writes():
    """Проверка стыка: что скаут кладёт в рецепт, то пробник и читает.

    Тикет 04 в бою был no-op именно на этом стыке — читать rooms_count
    научились, а писать его в рецепт было некому.
    """
    numbers = {"10043": {"name": "A-frame дом", "rooms_count": 3},
               "731": {"name": "Купол", "rooms_count": 1}}
    names, capacity, without = bronirui.directory(numbers)
    assert names == {"10043": "A-frame дом", "731": "Купол"}
    assert capacity == {"10043": 3, "731": 1}
    assert without == []


# ---------------------------------------------------------------------------
# Ревью волны 3: авария чужого хостера и частичный съём рецепт не ломают
# ---------------------------------------------------------------------------
# 502/503 приходят страницей nginx, а не JSON-ом: общий каскад читал негодное
# тело как смену схемы раньше, чем смотрел на код, и одна пятиминутная авария
# хостера помечала broken все цели этого хоста разом. Правила исхода —
# probes/_outcome.py.

def test_502_without_json_body_keeps_the_recipe_alive():
    fetch = fake_fetch({"10043": (502, None), "731": (502, None)})
    obj, broken = probes.run_recipe("vlesu_glamping", BRONIRUI_RECIPE,
                                    date(2026, 8, 14), date(2026, 8, 15),
                                    fetch=fetch)
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "502" in obj["reason"]


def test_403_after_a_number_was_taken_is_not_counted_as_a_refusal():
    """Съём состоялся, просто не до конца: счётчик суток отказа не заводим."""
    fetch = fake_fetch({"10043": small_calendar({"2026-08-14": (False, 9000)}),
                        "731": (403, None)})
    obj, broken = probes.run_recipe("vlesu_glamping", BRONIRUI_RECIPE,
                                    date(2026, 8, 14), date(2026, 8, 14),
                                    fetch=fetch)
    assert broken is None
    assert "refusal" not in obj
    assert obj["status"] == "partial"
    assert "снято не до конца" in obj["reason"]


# ---------------------------------------------------------------------------
# Ревью волны 3: тип без фонда нельзя называть домиком
# ---------------------------------------------------------------------------
# Вопрос заказчика 15.08 — «сколько домиков, а не типов». Справочник в старой
# плоской форме фонда не отдаёт, и заглушка units_total=1 делала снимок
# неотличимым от объекта со снятым фондом: сводка подписывала строку
# «по домикам: 6 в 6 типах» ровно там, где движок про фонд не сказал ничего.
# Признак теперь один и машинный: нет rooms_count -> нет пары
# units_total/units_free в клетке -> core.unit_basis говорит "type".

def test_directory_without_rooms_count_leaves_the_fund_unknown():
    names, capacity, unknown = bronirui.directory(
        BRONIRUI_RECIPE["request"]["params"]["numbers"])
    assert capacity == {}                     # заглушки «один домик» нет
    assert sorted(unknown) == ["10043", "731"]


def test_flat_directory_object_is_counted_by_types_not_homes():
    fetch = fake_fetch({"10043": small_calendar({"2026-08-14": (False, 9000)}),
                        "731": small_calendar({"2026-08-14": (True, None)})})
    obj, broken = probes.run_recipe("glamping_iva_spa", BRONIRUI_RECIPE,
                                    date(2026, 8, 14), date(2026, 8, 14),
                                    fetch=fetch)
    assert broken is None
    cells = obj["units"]["A-frame дом (с 15:00 до 12:00)"]
    assert cells["2026-08-14"] == {"state": "free", "price": 9000}
    basis = core.unit_basis(obj["units"])
    assert basis["basis"] == "type"           # сводка подпишет «фонд не снят»
    assert basis["units_with_capacity"] == 0
    assert "считано по номерам без фонда" in obj["reason"]


def test_one_home_per_number_is_counted_by_homes():
    """Фонд справочника равен единице — объект честно считается по домикам."""
    recipe = json.loads(json.dumps(BRONIRUI_RECIPE))
    recipe["request"]["params"]["numbers"] = {
        "10043": {"name": "A-frame дом", "rooms_count": 1}}
    fetch = fake_fetch({"10043": small_calendar({"2026-08-14": (False, 9000)})})
    obj, broken = probes.run_recipe("vlesu_glamping", recipe,
                                    date(2026, 8, 14), date(2026, 8, 14),
                                    fetch=fetch)
    assert broken is None
    assert obj["units"]["A-frame дом"]["2026-08-14"] == {
        "state": "free", "units_total": 1, "units_free": 1, "price": 9000}
    basis = core.unit_basis(obj["units"])
    assert basis["basis"] == "unit" and basis["homes"] == 1
    assert "считано по номерам без фонда" not in obj["reason"]


def test_multi_home_number_is_counted_by_types_and_says_so():
    """Три домика под одним номером: цифра занижена, и объект это говорит."""
    recipe = json.loads(json.dumps(BRONIRUI_RECIPE))
    recipe["request"]["params"]["numbers"] = {
        "10043": {"name": "A-frame дом", "rooms_count": 3}}
    fetch = fake_fetch({"10043": small_calendar({"2026-08-14": (False, 9000)})})
    obj, broken = probes.run_recipe("vlesu_glamping", recipe,
                                    date(2026, 8, 14), date(2026, 8, 14),
                                    fetch=fetch)
    assert broken is None
    assert obj["units"]["A-frame дом"]["2026-08-14"] == {
        "state": "free", "price": 9000}
    basis = core.unit_basis(obj["units"])
    assert basis["basis"] == "type"           # сводка подпишет «по типам»
    assert "поштучного остатка" in obj["reason"]
    assert "занижена" in obj["reason"]


def test_mixed_directory_is_neither_homes_nor_types():
    """Фонд у одного номера из двух: сводка обязана сказать «частично»."""
    recipe = json.loads(json.dumps(BRONIRUI_RECIPE))
    recipe["request"]["params"]["numbers"] = {
        "10043": {"name": "A-frame дом", "rooms_count": 1},
        "731": "Баня"}
    fetch = fake_fetch({"10043": small_calendar({"2026-08-14": (False, 9000)}),
                        "731": small_calendar({"2026-08-14": (False, 5000)})})
    obj, _ = probes.run_recipe("x", recipe, date(2026, 8, 14),
                               date(2026, 8, 14), fetch=fetch)
    assert core.unit_basis(obj["units"])["basis"] == "mixed"
    assert "731" in obj["reason"]
