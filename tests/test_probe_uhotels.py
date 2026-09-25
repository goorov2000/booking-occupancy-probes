# -*- coding: utf-8 -*-
"""Пробник UHotels: окна съёма, фонд домиков, каскад ошибок.

Разведка 16.08.2026 (pineriver_hotel, чистый HTTP): виджет artDg на
api.uhotels.app берёт данные из POST /api/widget/v1/booking/rooms — на
запрошенный ОТРЕЗОК список категорий, где поле max это остаток домиков.

Главный урок аудита того же дня, ради которого здесь половина тестов:
движок отвечает не «свободна ли ночь», а «можно ли забронировать такой
заезд». У объекта с минимумом в 2 ночи однночный запрос на выходные
возвращает пустой список — и первая редакция пробника записала в субботу
22.08 полную распродажу (180 из 180), тогда как двухночное окно на те же
даты отдаёт 27 категорий и 28 свободных домиков.

Фикстуры — живые ответы объекта, обрезанные до полей id/code/name/places/
area/price/max:
- rooms_2026-08-21.json — пятница, окно 1 ночь: 23 категории, свободен
  один домик, четырёх категорий в выдаче нет вовсе;
- rooms_2026-08-22_1n.json — суббота, окно 1 ночь: ПУСТОЙ список;
- rooms_2026-08-22_2n.json — та же суббота, окно 2 ночи: 27 категорий,
  28 свободных домиков;
- rooms_2026-09-15.json, rooms_2026-10-13.json — будни, окно 1 ночь.
"""
import json
from datetime import date
from pathlib import Path

import pytest

import probes
from probes import uhotels
from probes._common import SchemaChanged

FIXTURES = Path(__file__).parent / "fixtures" / "uhotels"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


UH_RECIPE = {
    "site": "https://pineriver.ru/",
    "engine": "uhotels",
    "status": "ok",
    "request": {
        "url_template": "https://api.uhotels.app/api/widget/v1/booking/rooms",
        "method": "POST",
        "params": {"hotel": "227:test", "lang": "ru", "currency": "RUB"},
        "headers": {"Referer": "https://pineriver.ru/booking"},
        "date_substitution": "dateIn = ночь, dateOut = ночь+длина окна",
    },
    "discovered_at": "2026-08-16T13:40:00+03:00",
    "notes": "",
    "source_urls": [],
}


def recipe_with(**params):
    recipe = json.loads(json.dumps(UH_RECIPE))
    recipe["request"]["params"].update(params)
    return recipe


def fake_fetch(by_window, calls=None, default=None):
    """fetch(url, headers, payload) -> (status, JSON) по (дате, длине окна).

    by_window — {(dateIn, days): ответ | (status, ответ) | исключение}.
    Незаданное окно отвечает default (по умолчанию — пустой список, то есть
    «такой заезд не продаётся»).
    """
    def fetch(url, headers, payload=None):
        if calls is not None:
            calls.append((payload["dateIn"], payload["days"]))
        answer = by_window.get((payload["dateIn"], payload["days"]),
                               [] if default is None else default)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, tuple) else (200, answer)
    return fetch


# ---------------------------------------------------------------------------
# Главное: минимальный срок не должен превращаться в выдуманную загрузку
# ---------------------------------------------------------------------------

def test_empty_one_night_answer_is_not_a_sellout():
    """Живой случай 22.08: окно в 1 ночь пусто (минимум 2 ночи), окно в 2
    ночи отдаёт 28 свободных домиков. Ночь обязана читаться свободной."""
    fetch = fake_fetch({("2026-08-22", 1): load("rooms_2026-08-22_1n.json"),
                        ("2026-08-22", 2): load("rooms_2026-08-22_2n.json")})
    obj, broken = probes.run_recipe("pineriver_hotel", UH_RECIPE,
                                    date(2026, 8, 22), date(2026, 8, 22),
                                    fetch=fetch)
    assert broken is None
    free = sum(c["2026-08-22"].get("units_free", 0)
               for c in obj["units"].values())
    assert free == 28
    busy_cells = [u for u, c in obj["units"].items()
                  if c["2026-08-22"]["state"] == "busy"]
    assert len(busy_cells) < len(obj["units"])   # НЕ полная распродажа


def test_night_is_covered_by_window_that_started_earlier():
    """Двухночное окно с 21.08 накрывает и 22.08: ночь получает оценку,
    даже если её собственные окна пусты."""
    fetch = fake_fetch({("2026-08-21", 2): load("rooms_2026-08-22_2n.json")})
    obj, _ = probes.run_recipe("pineriver_hotel", UH_RECIPE,
                               date(2026, 8, 21), date(2026, 8, 22),
                               fetch=fetch)
    typi = obj["units"]["Нео-Типи"]
    assert typi["2026-08-21"]["state"] == "free"
    assert typi["2026-08-22"]["state"] == "free"


def test_best_of_windows_wins():
    """По ночи берётся МАКСИМУМ остатка среди накрывших её окон: каждое
    окно — своя нижняя оценка, и меньшая не должна побеждать большую."""
    small = [{"code": "A", "name": "Дом", "max": 1, "price": 100}]
    big = [{"code": "A", "name": "Дом", "max": 5, "price": 300}]
    fetch = fake_fetch({("2026-09-01", 1): small, ("2026-09-01", 2): big})
    obj, _ = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1),
                               date(2026, 9, 1), fetch=fetch)
    assert obj["units"]["Дом"]["2026-09-01"]["units_free"] == 5


def test_missing_category_is_not_read_as_sold_out_when_другое_окно_видит_её():
    """Категории нет в однночной выдаче 21.08, но двухночная её видит
    свободной — записывать распродажу нельзя (это был дефект 16.08)."""
    fetch = fake_fetch({("2026-08-21", 1): load("rooms_2026-08-21.json"),
                        ("2026-08-21", 2): load("rooms_2026-08-22_2n.json")})
    obj, _ = probes.run_recipe("pineriver_hotel", UH_RECIPE,
                               date(2026, 8, 21), date(2026, 8, 21),
                               fetch=fetch)
    # «Нео-Типи» в однночной выдаче отсутствует, в двухночной свободна
    assert obj["units"]["Нео-Типи"]["2026-08-21"]["state"] == "free"


def test_zero_remainder_everywhere_is_busy():
    """Распродажу движок сообщает полем max=0 в самой выдаче — вот её и
    читаем как «занято», причём на весь известный фонд категории."""
    sold = [{"code": "A", "name": "Дом", "max": 0, "price": 0}]
    free = [{"code": "A", "name": "Дом", "max": 4, "price": 100}]
    fetch = fake_fetch({("2026-09-01", 1): sold, ("2026-09-01", 2): sold,
                        ("2026-09-02", 1): free})
    obj, _ = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1),
                               date(2026, 9, 2), fetch=fetch)
    sold_night = obj["units"]["Дом"]["2026-09-01"]
    assert sold_night["state"] == "busy"
    assert sold_night["units_free"] == 0
    assert sold_night["units_total"] == 4     # фонд взят с соседней ночи


# ---------------------------------------------------------------------------
# Окна: сколько запросов и какие
# ---------------------------------------------------------------------------

def test_two_windows_per_night_by_default():
    calls = []
    probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1), date(2026, 9, 3),
                      fetch=fake_fetch({}, calls=calls))
    assert calls == [("2026-09-01", 1), ("2026-09-01", 2),
                     ("2026-09-02", 1), ("2026-09-02", 2),
                     ("2026-09-03", 1), ("2026-09-03", 2)]


def test_recipe_can_override_windows():
    calls = []
    probes.run_recipe("x", recipe_with(spans=[2]), date(2026, 9, 1),
                      date(2026, 9, 2), fetch=fake_fetch({}, calls=calls))
    assert calls == [("2026-09-01", 2), ("2026-09-02", 2)]


def test_broken_spans_fall_back_to_default():
    calls = []
    probes.run_recipe("x", recipe_with(spans="две"), date(2026, 9, 1),
                      date(2026, 9, 1), fetch=fake_fetch({}, calls=calls))
    assert calls == [("2026-09-01", 1), ("2026-09-01", 2)]


def test_windows_are_capped_to_protect_the_host():
    calls = []
    probes.run_recipe("x", recipe_with(spans=[1, 2, 3, 4, 5, 6, 7]),
                      date(2026, 9, 1), date(2026, 9, 1),
                      fetch=fake_fetch({}, calls=calls))
    assert len(calls) == uhotels.MAX_SPANS


def test_horizon_is_not_deepened():
    """Глубина платная (запрос на окно), поэтому до DEEP_DATE_TO не лезем."""
    calls = []
    probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1), date(2026, 9, 2),
                      fetch=fake_fetch({}, calls=calls))
    assert max(d for d, _ in calls) == "2026-09-02"


# ---------------------------------------------------------------------------
# Фонд домиков
# ---------------------------------------------------------------------------

def test_fund_is_max_remainder_over_horizon():
    fetch = fake_fetch({("2026-09-15", 1): load("rooms_2026-09-15.json"),
                        ("2026-10-13", 1): load("rooms_2026-10-13.json")})
    obj, _ = probes.run_recipe("pineriver_hotel", UH_RECIPE,
                               date(2026, 9, 15), date(2026, 10, 13),
                               fetch=fetch)
    typi = obj["units"]["Нео-Типи"]
    assert typi["2026-09-15"]["units_free"] == 16
    assert typi["2026-10-13"]["units_free"] == 18
    assert typi["2026-09-15"]["units_total"] == 18      # фонд = максимум


def test_category_never_free_is_named_in_reason():
    """Фонд такой категории неизвестен, она весит один домик — и об этом
    надо сказать вслух: именно из этих весов складывается ответ «сколько
    всего домиков»."""
    rooms = [{"code": "A", "name": "Свободный", "max": 2, "price": 100},
             {"code": "B", "name": "Всегда занятый", "max": 0, "price": 0}]
    fetch = fake_fetch({("2026-09-01", 1): rooms, ("2026-09-01", 2): rooms})
    obj, broken = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 1), fetch=fetch)
    assert broken is None
    assert obj["status"] == "partial"
    assert "фонд не снят" in obj["reason"]
    assert "Всегда занятый" in obj["reason"]
    assert "units_total" not in obj["units"]["Всегда занятый"]["2026-09-01"]


def test_price_is_per_night_and_short_window_wins():
    """Цена движка — за весь отрезок (сверено 16.08: двухночное окно вдвое
    дороже), поэтому в клетку кладётся цена за ночь."""
    one = [{"code": "A", "name": "Дом", "max": 1, "price": 10000}]
    two = [{"code": "A", "name": "Дом", "max": 3, "price": 24000}]
    fetch = fake_fetch({("2026-09-01", 1): one, ("2026-09-01", 2): two})
    obj, _ = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1),
                               date(2026, 9, 1), fetch=fetch)
    cell = obj["units"]["Дом"]["2026-09-01"]
    assert cell["units_free"] == 3          # доступность — по лучшему окну
    assert cell["price"] == 10000           # цена — по самому точному


def test_price_from_long_window_is_divided():
    two = [{"code": "A", "name": "Дом", "max": 3, "price": 24000}]
    fetch = fake_fetch({("2026-09-01", 2): two})
    obj, _ = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1),
                               date(2026, 9, 1), fetch=fetch)
    assert obj["units"]["Дом"]["2026-09-01"]["price"] == 12000


def test_no_price_where_nothing_is_free():
    sold = [{"code": "A", "name": "Дом", "max": 0, "price": 0}]
    fetch = fake_fetch({("2026-09-01", 1): sold, ("2026-09-01", 2): sold})
    obj, _ = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1),
                               date(2026, 9, 1), fetch=fetch)
    assert "price" not in obj["units"]["Дом"]["2026-09-01"]


def test_same_names_are_told_apart_by_code():
    rooms = [{"code": "A1", "name": "А-фрейм", "max": 1, "price": 100},
             {"code": "A2", "name": "А-фрейм", "max": 2, "price": 200}]
    obj, _ = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1),
                               date(2026, 9, 1),
                               fetch=fake_fetch({("2026-09-01", 1): rooms}))
    assert sorted(obj["units"]) == ["А-фрейм [A1]", "А-фрейм [A2]"]


def test_category_without_name_is_named_by_code():
    rooms = [{"code": "ZZ", "name": "", "max": 2, "price": 500}]
    obj, _ = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1),
                               date(2026, 9, 1),
                               fetch=fake_fetch({("2026-09-01", 1): rooms}))
    assert list(obj["units"]) == ["Категория ZZ"]


def test_inventory_flag_changes_nothing_here():
    """Фонд приходит тем же ответом: --no-inventory не экономит запросов."""
    payload = {("2026-09-15", 1): load("rooms_2026-09-15.json")}
    calls_on, calls_off = [], []
    on, _ = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 15),
                              date(2026, 9, 15),
                              fetch=fake_fetch(payload, calls=calls_on),
                              inventory=True)
    off, _ = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 15),
                               date(2026, 9, 15),
                               fetch=fake_fetch(payload, calls=calls_off),
                               inventory=False)
    assert calls_on == calls_off
    assert on["units"] == off["units"]


# ---------------------------------------------------------------------------
# Каскад ошибок
# ---------------------------------------------------------------------------

def test_single_4xx_does_not_break_the_recipe():
    """77+ запросов подряд к одному хосту: одиночный 429 не повод хоронить
    объект до ручной переразведки (урок аудита 16.08)."""
    rooms = [{"code": "A", "name": "Дом", "max": 2, "price": 100}]
    fetch = fake_fetch({("2026-09-01", 1): rooms,
                        ("2026-09-01", 2): (429, {"error": "too many"}),
                        ("2026-09-02", 1): rooms})
    obj, broken = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 2), fetch=fetch)
    assert broken is None
    assert obj["status"] == "partial"
    assert "429" in obj["reason"]
    assert obj["units"]["Дом"]["2026-09-01"]["state"] == "free"


def test_total_4xx_breaks_the_recipe():
    """А вот если не снялось НИ ОДНО окно — это уже переразведка.

    Правка 04.09 (тикет 03): 403 и 429 из этого правила выведены — они
    означают «нас не пустили», лечатся временем и рецепт не ломают
    (см. test_403_on_every_window_is_refusal_not_broken). Здесь остаётся
    ответ, который переразведки и правда требует, — 404 на весь объект.
    """
    fetch = fake_fetch({}, default=(404, {"error": "нет"}))
    obj, broken = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 2), fetch=fetch)
    assert broken and "404" in broken
    assert obj["status"] == "insufficient_data"


def test_schema_change_breaks_the_recipe():
    fetch = fake_fetch({("2026-09-01", 1): {"rooms": []}})
    obj, broken = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 1), fetch=fetch)
    assert broken and "схема UHotels сменилась" in broken


def test_max_not_a_number_breaks_the_recipe():
    fetch = fake_fetch({("2026-09-01", 1): [{"code": "A", "name": "Дом",
                                             "max": "много"}]})
    obj, broken = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 1), fetch=fetch)
    assert broken and "max" in broken


def test_network_failure_keeps_recipe_alive():
    fetch = fake_fetch({}, default=OSError("обрыв"))
    obj, broken = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 2), fetch=fetch)
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "обрыв" in obj["reason"]


def test_reason_does_not_grow_to_the_length_of_the_horizon():
    """Список сбоев режется: иначе человеческая сводка становится
    нечитаемой ровно тогда, когда её надо прочесть."""
    fetch = fake_fetch({}, default=(503, {}))
    obj, _ = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1),
                               date(2026, 9, 30), fetch=fetch)
    assert "и ещё" in obj["reason"]
    assert len(obj["reason"]) < 400


def test_empty_answers_everywhere_are_not_called_a_sellout():
    """Окна снялись, категорий нет ни в одном: модуль выключен или продаж
    нет. Ни распродажи, ни поломки рецепта — честный insufficient_data."""
    obj, broken = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 2), fetch=fake_fetch({}))
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "ни одной категории" in obj["reason"]
    assert obj["units"] == {}


def test_network_failure_reason_does_not_claim_module_is_off():
    """Диагноз «модуль выключен» нельзя приписывать к прогону, который
    объект ни разу не проверил."""
    fetch = fake_fetch({}, default=OSError("обрыв"))
    obj, _ = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1),
                               date(2026, 9, 1), fetch=fetch)
    assert "модуль выключен" not in obj["reason"]


def test_recipe_without_hotel_token_breaks():
    recipe = json.loads(json.dumps(UH_RECIPE))
    recipe["request"]["params"].pop("hotel")
    obj, broken = probes.run_recipe("x", recipe, date(2026, 9, 1),
                                    date(2026, 9, 1), fetch=fake_fetch({}))
    assert broken and "params.hotel" in broken


def test_parse_rooms_rejects_non_list():
    with pytest.raises(SchemaChanged):
        uhotels.parse_rooms({"rooms": []})


def test_parse_rooms_keeps_larger_remainder_for_duplicate_code():
    parsed = uhotels.parse_rooms([
        {"code": "A", "name": "Дом", "max": 1, "price": 100},
        {"code": "A", "name": "Дом", "max": 3, "price": 90}])
    assert parsed["A"]["left"] == 3


def test_windows_do_not_leak_past_the_horizon():
    covered = uhotels.windows(["2026-09-01", "2026-09-02"], (1, 2))
    assert ("2026-09-02", 2, ["2026-09-02"]) in covered
    assert all(all(d in ("2026-09-01", "2026-09-02") for d in c)
               for _, _, c in covered)


# ---------------------------------------------------------------------------
# Тикет 06: горизонт фонда — единственный рычаг цены этого пробника
# ---------------------------------------------------------------------------
# У UHotels сетка и фонд — один и тот же запрос: остаток домиков приходит тем
# же ответом, что доступность, и стоит он одного POST на ночь × число окон.
# Поэтому граница фонда режет здесь и список ночей: 366 ночей × 2 окна — это
# 732 запроса к одному хосту, то есть прогон длиной в четверть суток.

def test_inventory_horizon_bounds_windows_and_grid():
    calls = []
    fetch = fake_fetch({}, calls=calls,
                       default=[{"code": "A", "name": "Дом", "max": 2,
                                 "price": 100}])
    obj, broken = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 4),
                                    date(2027, 9, 4), fetch=fetch,
                                    inventory_date_to=date(2026, 9, 13))
    assert broken is None
    nights = sorted({night for night, _ in calls})
    assert nights[0] == "2026-09-04" and nights[-1] == "2026-09-13"
    assert len(nights) == 10
    cells = obj["units"]["Дом"]
    assert len(cells) == 10
    assert obj["inventory_until"] == "2026-09-13"
    assert "горизонт фонда" in obj["reason"]


def test_without_inventory_horizon_uhotels_keeps_old_behaviour():
    calls = []
    fetch = fake_fetch({}, calls=calls,
                       default=[{"code": "A", "name": "Дом", "max": 2,
                                 "price": 100}])
    obj, _ = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1),
                               date(2026, 9, 3), fetch=fetch)
    assert sorted({night for night, _ in calls}) == ["2026-09-01",
                                                     "2026-09-02",
                                                     "2026-09-03"]
    assert len(obj["units"]["Дом"]) == 3
    assert obj["inventory_until"] == "2026-09-03"


def test_403_on_every_window_is_refusal_not_broken():
    """Тикет 03: хост не пустил — рецепт жив, снимок несёт refusal.

    У этого пробника десятки запросов подряд к одному хосту, и 429/403 тут
    самый вероятный исход всплеска нагрузки: ломать рецепт на нём — значит
    гонять агента на переразведку из-за чужой минуты.
    """
    calls = []
    fetch = fake_fetch({}, calls=calls, default=(403, {"error": "forbidden"}))
    obj, broken = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 5), fetch=fetch)
    assert broken is None
    assert obj["refusal"]["status"] == 403
    assert obj["status"] == "insufficient_data"
    # в хост, который нас не пускает, не долбимся: окна после отказа не идут
    assert len(calls) == 1


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
    calls = []
    fetch = fake_fetch({}, calls=calls, default=(200, None))
    fetch.last_body_text = CHALLENGE_PAGE
    obj, broken = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 5), fetch=fetch)
    assert broken is None
    assert "не пустили" in obj["refusal"]["reason"]
    assert len(calls) == 1                     # в закрытую дверь не долбимся


# ---------------------------------------------------------------------------
# Тикет 03: частичный съём с данными — это УСПЕШНЫЙ съём
# ---------------------------------------------------------------------------
# У этого пробника десятки запросов подряд к одному хосту, и 429 на середине
# горизонта — рабочая ситуация, а не поломка. Поле refusal здесь копило бы
# сутки отказа живому рецепту (cli._account_recipe до clear_refusals в этой
# ветке не доходит), и три таких дня подряд отправили бы агента на
# переразведку, которая ничего не чинит.

def test_refusal_after_the_grid_was_taken_is_not_counted_as_a_refusal():
    calls = []
    rooms = [{"code": "A", "name": "Дом", "max": 2, "price": 100}]

    def fetch(url, headers, payload=None):
        calls.append((payload["dateIn"], payload["days"]))
        if len(calls) > 4:
            return 429, None
        return 200, rooms

    obj, broken = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 5), fetch=fetch)
    assert broken is None
    assert "refusal" not in obj             # рецепт не приближается к broken
    assert obj["status"] == "partial"
    assert "снято не до конца" in obj["reason"]
    assert obj["units"]["Дом"]["2026-09-01"]["state"] == "free"


def test_uhotels_names_the_grid_boundary_it_asked_for():
    """Сетка кончается вместе с фондом — объект говорит об этом прямо."""
    fetch = fake_fetch({}, default=[{"code": "A", "name": "Дом", "max": 2,
                                     "price": 100}])
    obj, _ = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 4),
                               date(2027, 9, 4), fetch=fetch,
                               inventory_date_to=date(2026, 9, 13))
    assert obj["grid_until"] == "2026-09-13"
    assert obj["inventory_until"] == "2026-09-13"
    assert "сетка снята до 2026-09-13" in obj["reason"]


# ---------------------------------------------------------------------------
# Ревью волны 3: авария чужого хостера рецепт не ломает
# ---------------------------------------------------------------------------

def test_502_without_json_body_keeps_the_recipe_alive():
    """Страница nginx с кодом 502 не JSON — но это сбой хоста, не схема."""
    fetch = fake_fetch({}, default=(502, None))
    obj, broken = probes.run_recipe("x", UH_RECIPE, date(2026, 9, 1),
                                    date(2026, 9, 2), fetch=fetch)
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "502" in obj["reason"]
