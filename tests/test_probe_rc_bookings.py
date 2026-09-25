# -*- coding: utf-8 -*-
"""Пробник RC Bookings (старый виджет RealtyCalendar): окна съёма, справочник
из рецепта, закрытое окно продаж по выборочной проверке, каскад ошибок.

Разведка 04.09 и 08.09.2026 (smr_zagorod, zagorod-samara.ru, чистый HTTP):
виджет RC_BOOKINGS_WIDGET ходит на GET
realtycalendar.ru/widgets/bookings/search/{token}.json?humans=N&begin_date=
ДД.ММ.ГГГГ&end_date=ДД.ММ.ГГГГ и отдаёт ТОЛЬКО те домики, которые на этот
заезд можно забронировать. Посуточного календаря у API нет: ночь = запрос.

Главная ловушка, ради которой здесь половина тестов: пустая выдача — это
«ни один домик не бронируется», и изнутри ответа «все три проданы» от
«календарь закрыт на зиму» не отличить. На 08.09 продажи объекта обрываются
между 29.10 и 01.11 — через десять дней хвост 45-ночной сетки стал бы
фиктивными 100% ноября. Пробник добывает свидетельство сам: выборочной
проверкой за сеткой, и переписывает такой хвост в sales_not_open порогами
правила стены диспетчера.

Фикстуры — живые ответы search объекта, обрезанные до id/title/price/
humans/sleeps/rooms (описания, фото, адрес и услуги выброшены):
- search_2026-09-12_1n.json — суббота 12.09, окно 1 ночь: все три домика
  (11000/11000/16000 — тариф выходного);
- search_2026-09-12_2n.json — 12-14.09, окно 2 ночи: те же три, цена за ночь
  средняя по отрезку (10000 = (11000 сб + 9000 вс) / 2);
- search_2026-09-08_1n.json — 08.09 (сегодня на день разведки): два домика,
  «озеро дом 2» (166851) занят;
- search_2026-09-19_1n.json — суббота 19.09: только «озеро дом 1»;
- search_2026-11-01_1n.json — 01.11: ПУСТОЙ список, продажи закрыты;
- settings.json — settings/{token}.json: агентство «ЗАгород», Самара
  (evidence, что токен — наш объект; контакты выброшены).
"""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import occupancy_core as core
import probes
from probes import rc_bookings
from probes._common import BudgetExceeded, SchemaChanged

FIXTURES = Path(__file__).parent / "fixtures" / "rc_bookings"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _register_engine(monkeypatch):
    """Движок в диспетчере — до регистрации оркестратором в probes/__init__.py.

    Через run_recipe идут и правила окна продаж диспетчера, и подпись
    версии; гонять пробник в обход диспетчера значило бы проверять не то,
    что пойдёт в прогон.
    """
    monkeypatch.setitem(probes.ENGINES, "rc-bookings", rc_bookings)


TOKEN = "a821b1678f24008617bd63de64618795"
RC_RECIPE = {
    "site": "https://zagorod-samara.ru",
    "engine": "rc-bookings",
    "status": "ok",
    "request": {
        "url_template": ("https://realtycalendar.ru/widgets/bookings/search/"
                         "{token}.json?humans={guests}&begin_date={date_from}"
                         "&end_date={date_to}"),
        "method": "GET",
        "params": {"token": TOKEN, "humans": 1,
                   "apartment_ids": [166848, 166851, 194974],
                   "apartment_titles": {"166848": "озеро дом 1",
                                        "166851": "озеро дом 2",
                                        "194974": "дом 3"}},
        "headers": {"Referer": f"https://realtycalendar.ru/booking-widget/{TOKEN}"},
        "date_substitution": "begin_date/end_date = ДД.ММ.ГГГГ, одно окно = один GET",
    },
    "discovered_at": "2026-09-08T01:00:00+03:00",
    "notes": "",
    "source_urls": [],
}

EMPTY = {"apartments": []}
ALL3 = load("search_2026-09-12_1n.json")


def recipe_with(**params):
    recipe = json.loads(json.dumps(RC_RECIPE))
    recipe["request"]["params"].update(params)
    return recipe


def recipe_without(*names):
    recipe = json.loads(json.dumps(RC_RECIPE))
    for name in names:
        recipe["request"]["params"].pop(name, None)
    return recipe


def _window_of(url: str) -> tuple[str, int]:
    """URL search -> (заезд ISO, длина окна в ночах)."""
    query = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))
    begin = date(*reversed([int(x) for x in query["begin_date"].split(".")]))
    end = date(*reversed([int(x) for x in query["end_date"].split(".")]))
    return begin.isoformat(), (end - begin).days


def fake_fetch(by_window, calls=None, default=None):
    """fetch(url, headers) -> (status, JSON) по (заезду ISO, длине окна).

    by_window — {(заезд, ночей): ответ | (status, ответ) | исключение}.
    Незаданное окно отвечает default (по умолчанию — пустой список, то есть
    «такой заезд не продаётся»).
    """
    def fetch(url, headers, payload=None):
        key = _window_of(url)
        if calls is not None:
            calls.append(key)
        answer = by_window.get(key, EMPTY if default is None else default)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, tuple) else (200, answer)
    fetch.last_body_text = ""
    return fetch


def run(recipe, date_from, date_to, fetch, **kw):
    return probes.run_recipe("smr_zagorod", recipe, date_from, date_to,
                             fetch=fetch, **kw)


# ---------------------------------------------------------------------------
# Живые фикстуры: сетка по домикам через диспетчер
# ---------------------------------------------------------------------------

def test_live_fixtures_build_per_unit_grid_with_named_houses():
    fetch = fake_fetch({("2026-09-12", 1): ALL3,
                        ("2026-09-12", 2): load("search_2026-09-12_2n.json")},
                       default=ALL3)
    obj, broken = run(RC_RECIPE, date(2026, 9, 12), date(2026, 9, 13), fetch,
                      inventory_date_to=date(2026, 9, 13))
    assert broken is None
    assert obj["status"] == "ok"
    assert obj["engine"] == "rc-bookings"
    assert obj["granularity"] == "per_unit"
    assert list(obj["units"]) == ["озеро дом 1", "озеро дом 2", "дом 3"]
    sat = {u: c["2026-09-12"] for u, c in obj["units"].items()}
    assert all(cell["state"] == "free" for cell in sat.values())
    assert sat["озеро дом 1"]["price"] == 11000
    assert sat["дом 3"]["price"] == 16000
    core.validate_object(obj)


def test_house_missing_from_search_is_busy_and_weighs_one_house():
    """08.09: «озеро дом 2» в выдаче нет — занят; фонд домика всегда 1."""
    fetch = fake_fetch({("2026-09-08", 1): load("search_2026-09-08_1n.json"),
                        ("2026-09-08", 2): load("search_2026-09-08_1n.json")})
    obj, broken = run(RC_RECIPE, date(2026, 9, 8), date(2026, 9, 8), fetch)
    assert broken is None
    night = {u: c["2026-09-08"] for u, c in obj["units"].items()}
    assert night["озеро дом 2"] == {"state": "busy", "units_total": 1,
                                    "units_free": 0}
    assert night["озеро дом 1"]["state"] == "free"
    assert night["озеро дом 1"]["units_free"] == 1
    assert night["дом 3"]["state"] == "free"
    assert core.unit_basis(obj["units"])["basis"] == "unit"


def test_saturday_19_09_two_houses_taken():
    fetch = fake_fetch({("2026-09-19", 1): load("search_2026-09-19_1n.json"),
                        ("2026-09-19", 2): load("search_2026-09-19_1n.json")})
    obj, _ = run(RC_RECIPE, date(2026, 9, 19), date(2026, 9, 19), fetch)
    states = {u: c["2026-09-19"]["state"] for u, c in obj["units"].items()}
    assert states == {"озеро дом 1": "free", "озеро дом 2": "busy",
                      "дом 3": "busy"}


# ---------------------------------------------------------------------------
# Окна съёма: минимальный срок не превращается в выдуманную загрузку
# ---------------------------------------------------------------------------

def test_empty_one_night_window_is_not_a_sellout_when_two_nights_sell():
    """Ловушка uhotels 22.08: окно в 1 ночь пусто (минимум 2 ночи), окно
    в 2 ночи отдаёт домики — ночь обязана читаться свободной."""
    fetch = fake_fetch({("2026-09-12", 1): EMPTY,
                        ("2026-09-12", 2): load("search_2026-09-12_2n.json")})
    obj, _ = run(RC_RECIPE, date(2026, 9, 12), date(2026, 9, 12), fetch)
    assert all(c["2026-09-12"]["state"] == "free"
               for c in obj["units"].values())


def test_night_is_covered_by_window_that_started_earlier():
    fetch = fake_fetch({("2026-09-11", 2): ALL3})
    obj, _ = run(RC_RECIPE, date(2026, 9, 11), date(2026, 9, 12), fetch)
    house = obj["units"]["озеро дом 1"]
    assert house["2026-09-11"]["state"] == "free"
    assert house["2026-09-12"]["state"] == "free"


def test_shortest_window_price_wins():
    """Цена окна — за ночь, средняя по отрезку: однночная точнее двухночной."""
    fetch = fake_fetch({("2026-09-12", 1): ALL3,
                        ("2026-09-12", 2): load("search_2026-09-12_2n.json")})
    obj, _ = run(RC_RECIPE, date(2026, 9, 12), date(2026, 9, 12), fetch)
    assert obj["units"]["озеро дом 1"]["2026-09-12"]["price"] == 11000


def test_night_without_a_single_answered_window_stays_unknown():
    fetch = fake_fetch({("2026-09-12", 1): OSError("таймаут"),
                        ("2026-09-12", 2): OSError("таймаут"),
                        ("2026-09-13", 1): ALL3, ("2026-09-13", 2): ALL3})
    obj, broken = run(RC_RECIPE, date(2026, 9, 12), date(2026, 9, 13), fetch)
    assert broken is None
    assert obj["units"]["дом 3"]["2026-09-12"] == {"state": "unknown"}
    assert obj["units"]["дом 3"]["2026-09-13"]["state"] == "free"
    assert obj["status"] == "partial"
    assert "сетевой сбой" in obj["reason"]


def test_spans_come_from_recipe_with_a_guard():
    calls = []
    fetch = fake_fetch({}, calls=calls, default=ALL3)
    run(recipe_with(spans=[3, 1, 99, "x", True]), date(2026, 9, 12),
        date(2026, 9, 12), fetch)
    assert sorted(calls) == [("2026-09-12", 1), ("2026-09-12", 3)]


# ---------------------------------------------------------------------------
# Справочник домиков — из рецепта (API его не отдаёт)
# ---------------------------------------------------------------------------

def test_house_never_seen_in_horizon_still_gets_a_row_as_busy():
    """Домик, занятый весь горизонт, в search не появится ни разу — строку
    ему даёт рецепт, иначе он исчез бы из снимка вместе со своей занятостью."""
    two = load("search_2026-09-08_1n.json")          # без 166851
    fetch = fake_fetch({}, default=two)
    obj, _ = run(RC_RECIPE, date(2026, 9, 8), date(2026, 9, 10), fetch)
    assert all(c["state"] == "busy"
               for c in obj["units"]["озеро дом 2"].values())


def test_without_apartment_ids_only_seen_houses_are_listed_and_it_is_said():
    two = load("search_2026-09-08_1n.json")
    fetch = fake_fetch({}, default=two)
    obj, broken = run(recipe_without("apartment_ids", "apartment_titles"),
                      date(2026, 9, 8), date(2026, 9, 8), fetch)
    assert broken is None
    assert sorted(obj["units"]) == ["дом 3", "озеро дом 1"]
    assert obj["status"] == "partial"
    assert "apartment_ids" in obj["reason"]


def test_recipe_titles_beat_live_titles_for_stable_unit_names():
    """Имя из рецепта стабильно от снимка к снимку; живое — только там, где
    рецепт имени не дал."""
    live = {"apartments": [{"id": 166848, "title": "Дом у озера №1", "price": 1},
                           {"id": 194974, "title": "Большой дом", "price": 1}]}
    fetch = fake_fetch({}, default=live)
    obj, _ = run(recipe_with(apartment_titles={"166848": "озеро дом 1"}),
                 date(2026, 9, 8), date(2026, 9, 8), fetch)
    assert list(obj["units"]) == ["озеро дом 1", "Домик 166851", "Большой дом"]


def test_foreign_houses_of_the_agency_are_ignored():
    live = {"apartments": [{"id": 166848, "title": "озеро дом 1", "price": 1},
                           {"id": 999, "title": "Чужая квартира", "price": 1}]}
    fetch = fake_fetch({}, default=live)
    obj, _ = run(RC_RECIPE, date(2026, 9, 8), date(2026, 9, 8), fetch)
    assert "Чужая квартира" not in obj["units"]


def test_same_titles_are_told_apart_by_id():
    recipe = recipe_with(apartment_ids=[11, 12],
                         apartment_titles={"11": "А-фрейм", "12": "А-фрейм"})
    fetch = fake_fetch({}, default=EMPTY)
    obj, _ = run(recipe, date(2026, 9, 8), date(2026, 9, 8), fetch)
    assert sorted(obj["units"]) == ["А-фрейм [11]", "А-фрейм [12]"]


# ---------------------------------------------------------------------------
# Запрос: формат дат виджета, состав гостей, глубина сетки
# ---------------------------------------------------------------------------

def test_url_uses_widget_date_format_and_humans():
    urls = []

    def fetch(url, headers, payload=None):
        urls.append(url)
        return 200, ALL3
    fetch.last_body_text = ""
    run(recipe_with(humans=2), date(2026, 9, 5), date(2026, 9, 5), fetch)
    one, two = sorted(urls)[:2]
    assert one.startswith(f"https://realtycalendar.ru/widgets/bookings/search/{TOKEN}.json?")
    assert "humans=2&begin_date=05.09.2026&end_date=06.09.2026" in one
    assert "begin_date=05.09.2026&end_date=07.09.2026" in two


def test_grid_is_cut_at_inventory_horizon_and_the_boundary_is_named():
    """Ночь = запрос, поэтому inventory_date_to режет и сетку (как uhotels):
    за границей клеток нет, объект называет её сам."""
    calls = []
    fetch = fake_fetch({}, calls=calls, default=ALL3)
    obj, broken = run(RC_RECIPE, date(2026, 9, 8), date(2027, 9, 8), fetch,
                      inventory_date_to=date(2026, 9, 10))
    assert broken is None
    assert obj["grid_until"] == "2026-09-10"
    assert obj["inventory_until"] == "2026-09-10"
    nights = {n for cells in obj["units"].values() for n in cells}
    assert nights == {"2026-09-08", "2026-09-09", "2026-09-10"}
    dense = [c for c in calls if c[0] <= "2026-09-10"]
    assert len(dense) == 3 * 2
    assert "горизонт фонда" in obj["reason"]
    assert obj["status"] == "ok"                 # пометка, а не неполнота


def test_without_inventory_horizon_grid_goes_to_date_to():
    calls = []
    fetch = fake_fetch({}, calls=calls, default=ALL3)
    obj, _ = run(RC_RECIPE, date(2026, 9, 8), date(2026, 9, 9), fetch)
    assert obj["grid_until"] == "2026-09-09"
    assert "sales_scan" not in obj
    assert len(calls) == 2 * 2


# ---------------------------------------------------------------------------
# Закрытое окно продаж: хвост без бронируемых домиков + проверка за сеткой
# ---------------------------------------------------------------------------

ORIGIN = date(2026, 9, 8)
FUND_UNTIL = date(2026, 10, 23)                     # 46 ночей сетки
HORIZON = date(2027, 9, 8)


def _closing(from_night: str, bookable_beyond=None):
    """Выдача: ночи до from_night свободны у всех домиков, дальше пусто —
    включая выборку за сеткой, кроме заездов из bookable_beyond.

    Как у живого API: домик пропадает из выдачи, если закрыта ХОТЬ ОДНА ночь
    отрезка, поэтому окно судится по своей последней ночи, а не по заезду.
    """
    bookable_beyond = set(bookable_beyond or ())

    def fetch(url, headers, payload=None):
        start, span = _window_of(url)
        last_night = (date.fromisoformat(start)
                      + timedelta(days=span - 1)).isoformat()
        if last_night < from_night or start in bookable_beyond:
            return 200, ALL3
        return 200, EMPTY
    fetch.last_body_text = ""
    return fetch


def test_closed_tail_confirmed_beyond_the_grid_becomes_sales_not_open():
    """Живой случай 08.09: продажи обрываются 01.11 и дальше пусто до конца
    горизонта. Хвост сетки — закрытые продажи, а не 100% занятости."""
    obj, broken = run(RC_RECIPE, ORIGIN, HORIZON, _closing("2026-10-10"),
                      inventory_date_to=FUND_UNTIL)
    assert broken is None
    assert obj["status"] == "ok"
    house = obj["units"]["дом 3"]
    assert house["2026-10-09"]["state"] == "free"
    assert house["2026-10-10"] == {"state": "sales_not_open",
                                   "relabeled_from": "busy",
                                   "relabeled_by": "closed_horizon",
                                   "units_total": 1, "units_free": 0}
    assert house["2026-10-23"]["state"] == "sales_not_open"
    assert obj["sales_window"]["rule"] == "closed_horizon"
    assert obj["sales_window"]["since"] == "2026-10-10"
    assert obj["sales_window"]["closed_until"] == "2027-09-08"
    assert obj["sales_window"]["relabeled_nights"] == 14
    assert obj["sales_window"]["relabeled_cells"] == 14 * 3
    assert obj["sales_scan"]["bookable_until"] is None
    assert "продажи закрыты с 2026-10-10" in obj["reason"]
    core.validate_object(obj)


def test_tail_stays_busy_when_sales_are_open_beyond_the_grid():
    """За сеткой домики бронируются — хвост это брони (или закрытые дни),
    а не закрытое окно продаж."""
    obj, _ = run(RC_RECIPE, ORIGIN, HORIZON,
                 _closing("2026-10-10", bookable_beyond={"2026-11-02"}),
                 inventory_date_to=FUND_UNTIL)
    assert obj["units"]["дом 3"]["2026-10-15"]["state"] == "busy"
    assert "sales_window" not in obj
    assert obj["sales_scan"]["bookable_until"] == "2026-11-02"


def test_short_wall_is_not_relabeled():
    """Хвост в 7 ночей плюс пустота за сеткой до первой бронируемой выборки
    (17.10..10.11 = 25 ночей) — короче порога стены в 30: это брони."""
    obj, _ = run(RC_RECIPE, ORIGIN, HORIZON,
                 _closing("2026-10-17", bookable_beyond={"2026-11-11"}),
                 inventory_date_to=FUND_UNTIL)
    assert "sales_window" not in obj
    assert obj["units"]["дом 3"]["2026-10-20"]["state"] == "busy"
    assert obj["sales_scan"]["bookable_until"] == "2026-11-11"


def test_wall_start_is_clamped_to_the_floor_of_the_dispatcher_rule():
    """Ближе 21 ночи от начала сетки стена не переписывает: ближний уик-энд
    и правда бывает выкуплен целиком (порог — из диспетчера)."""
    obj, _ = run(RC_RECIPE, ORIGIN, HORIZON, _closing("2026-09-24"),
                 inventory_date_to=FUND_UNTIL)
    floor = (ORIGIN.toordinal() + probes.SALES_WALL_FLOOR_DAYS)
    since = date.fromordinal(floor).isoformat()
    assert obj["sales_window"]["since"] == since
    house = obj["units"]["дом 3"]
    assert house["2026-09-24"]["state"] == "busy"
    assert house[since]["state"] == "sales_not_open"


def test_object_without_live_sales_is_left_to_the_dispatcher():
    """Ни одной бронируемой ночи нигде — не догадываемся: правило
    no_free_cells диспетчера прячет цифру (unknown, partial)."""
    obj, broken = run(RC_RECIPE, ORIGIN, HORIZON, _closing("2026-09-01"),
                      inventory_date_to=FUND_UNTIL)
    assert broken is None
    assert obj["sales_window"]["rule"] == "no_free_cells"
    assert obj["status"] == "partial"
    assert all(c["state"] == "unknown"
               for cells in obj["units"].values() for c in cells.values())


def test_unknown_night_inside_the_tail_blocks_the_guess():
    """Неснятая ночь в хвосте — снимок неполный, стену не выдумываем."""
    closing = _closing("2026-10-10")

    def fetch(url, headers, payload=None):
        start, _ = _window_of(url)
        # Ночь 15.10 накрывают три окна: 14.10 на 2 ночи и оба окна 15.10 —
        # роняем все, иначе её снимет соседнее окно и unknown не будет.
        if start in ("2026-10-14", "2026-10-15"):
            raise OSError("обрыв")
        return closing(url, headers)
    fetch.last_body_text = ""
    obj, _ = run(RC_RECIPE, ORIGIN, HORIZON, fetch,
                 inventory_date_to=FUND_UNTIL)
    assert obj["units"]["дом 3"]["2026-10-15"] == {"state": "unknown"}
    assert "sales_window" not in obj
    assert obj["units"]["дом 3"]["2026-10-20"]["state"] == "busy"


def test_scan_failure_cuts_the_evidence_short():
    """Сбой в выборке за сеткой режет свидетельство на последней удачной
    выборке: стена короче порога — хвост остаётся занятостью."""
    closing = _closing("2026-10-15")

    def fetch(url, headers, payload=None):
        start, _ = _window_of(url)
        if start > "2026-10-25":
            return 503, None
        return closing(url, headers)
    fetch.last_body_text = ""
    obj, broken = run(RC_RECIPE, ORIGIN, HORIZON, fetch,
                      inventory_date_to=FUND_UNTIL)
    assert broken is None
    assert "sales_window" not in obj
    assert obj["sales_scan"]["failed"] >= 1
    assert obj["units"]["дом 3"]["2026-10-20"]["state"] == "busy"


def test_scan_walks_weekdays_and_costs_one_request_per_step():
    calls = []
    fetch = fake_fetch({}, calls=calls, default=ALL3)
    obj, _ = run(RC_RECIPE, ORIGIN, date(2026, 12, 31), fetch,
                 inventory_date_to=FUND_UNTIL)
    beyond = [c for c in calls if c[0] > FUND_UNTIL.isoformat()]
    assert beyond[0] == ("2026-10-24", rc_bookings.SCAN_WINDOW_NIGHTS)
    starts = [date.fromisoformat(c[0]) for c in beyond]
    assert all((b - a).days == rc_bookings.SCAN_STEP_NIGHTS
               for a, b in zip(starts, starts[1:]))
    assert len({d.weekday() for d in starts}) == 7
    assert obj["sales_scan"]["samples"] == len(beyond)
    assert obj["sales_scan"]["bookable_until"] == beyond[-1][0]


def test_nothing_bookable_beyond_the_grid_is_said_even_without_a_tail():
    obj, _ = run(RC_RECIPE, ORIGIN, HORIZON, _closing("2026-10-24"),
                 inventory_date_to=FUND_UNTIL)
    assert "sales_window" not in obj
    assert "за сеткой бронируемых ночей не найдено" in obj["reason"]
    assert obj["status"] == "ok"


def test_closed_horizon_uses_the_dispatcher_thresholds():
    free = {}
    dates = [(ORIGIN.toordinal() + i) for i in range(46)]
    dates = [date.fromordinal(d).isoformat() for d in dates]
    for n in dates:
        free[n] = {"1"} if n < "2026-10-10" else set()
    scan = [("2026-10-24", set()), ("2026-11-02", set())]
    assert rc_bookings.closed_horizon(free, dates, scan, ORIGIN, HORIZON) == \
        ("2026-10-10", "2027-09-08")
    # первая же бронируемая выборка обрывает стену накануне себя
    scan = [("2026-10-24", set()), ("2026-11-02", {"1"})]
    assert rc_bookings.closed_horizon(free, dates, scan, ORIGIN, HORIZON) is None
    # без проверки за сеткой свидетельство кончается краем сетки
    assert rc_bookings.closed_horizon(free, dates, [], ORIGIN, HORIZON) is None


# ---------------------------------------------------------------------------
# Каскад ошибок: 4xx на окне не ломает, 403/429 — отказ, 5xx/сеть — жив
# ---------------------------------------------------------------------------

def test_403_on_first_window_is_refusal_not_broken():
    fetch = fake_fetch({}, default=(403, {"error": "нет"}))
    obj, broken = run(RC_RECIPE, date(2026, 9, 8), date(2026, 9, 9), fetch)
    assert broken is None
    assert obj["refusal"]["status"] == 403
    assert obj["status"] == "insufficient_data"
    assert obj["units"] == {}


def test_429_after_the_grid_started_is_partial_not_refusal():
    fetch = fake_fetch({("2026-09-08", 1): ALL3,
                        ("2026-09-08", 2): (429, {})})
    obj, broken = run(RC_RECIPE, date(2026, 9, 8), date(2026, 9, 9), fetch)
    assert broken is None
    assert "refusal" not in obj
    assert obj["status"] == "partial"
    assert "снято не до конца" in obj["reason"]
    assert obj["units"]["дом 3"]["2026-09-08"]["state"] == "free"
    assert obj["units"]["дом 3"]["2026-09-09"] == {"state": "unknown"}


def test_404_on_every_window_breaks_the_recipe():
    fetch = fake_fetch({}, default=(404, {"error": "not found"}))
    obj, broken = run(RC_RECIPE, date(2026, 9, 8), date(2026, 9, 9), fetch)
    assert broken and "HTTP 404" in broken
    assert obj["status"] == "insufficient_data"


def test_404_on_a_single_window_is_a_failure_not_a_break():
    fetch = fake_fetch({("2026-09-08", 1): (404, {})}, default=ALL3)
    obj, broken = run(RC_RECIPE, date(2026, 9, 8), date(2026, 9, 9), fetch)
    assert broken is None
    assert obj["status"] == "partial"
    assert "HTTP 404" in obj["reason"]
    assert obj["units"]["дом 3"]["2026-09-08"]["state"] == "free"


def test_5xx_everywhere_keeps_the_recipe_alive():
    fetch = fake_fetch({}, default=(503, None))
    obj, broken = run(RC_RECIPE, date(2026, 9, 8), date(2026, 9, 9), fetch)
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "HTTP 503" in obj["reason"]


def test_network_failure_everywhere_keeps_the_recipe_alive():
    fetch = fake_fetch({}, default=OSError("таймаут"))
    obj, broken = run(RC_RECIPE, date(2026, 9, 8), date(2026, 9, 9), fetch)
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "сетевой сбой" in obj["reason"]


def test_schema_change_breaks_the_recipe():
    fetch = fake_fetch({}, default={"items": []})
    obj, broken = run(RC_RECIPE, date(2026, 9, 8), date(2026, 9, 9), fetch)
    assert broken and "apartments" in broken


def test_apartment_without_id_is_schema_change():
    with pytest.raises(SchemaChanged):
        rc_bookings.parse_search({"apartments": [{"title": "x"}]})


def test_challenge_page_with_http_200_is_refusal_not_broken():
    fetch = fake_fetch({}, default=(200, None))
    fetch.last_body_text = ("<html><title>Just a moment...</title>"
                            "<div class=\"cf-browser-verification\"></div>")
    obj, broken = run(RC_RECIPE, date(2026, 9, 8), date(2026, 9, 9), fetch)
    assert broken is None
    assert "не пустили" in obj["refusal"]["reason"]


def test_budget_exhaustion_stops_the_windows_and_skips_the_scan():
    """Бюджет объекта — наше решение: клетки дальше unknown, рецепт жив,
    поле refusal не заводится, выборка за сеткой не идёт."""
    calls = []

    def fetch(url, headers, payload=None):
        calls.append(_window_of(url))
        if len(calls) > 2:
            raise BudgetExceeded("бюджет времени на объект исчерпан")
        return 200, ALL3
    fetch.last_body_text = ""
    obj, broken = run(RC_RECIPE, ORIGIN, HORIZON, fetch,
                      inventory_date_to=date(2026, 9, 12))
    assert broken is None
    assert "refusal" not in obj
    assert len(calls) == 3
    assert obj["units"]["дом 3"]["2026-09-08"]["state"] == "free"
    assert obj["units"]["дом 3"]["2026-09-12"] == {"state": "unknown"}
    assert "sales_scan" not in obj
    assert obj["status"] == "partial"
    assert "бюджет" in obj["reason"]


def test_all_windows_empty_with_recipe_houses_is_not_a_break():
    """Пустая выдача везде — модуль выключен, продаж нет или всё продано:
    рецепт живой, домики из рецепта заняты, диспетчер прячет цифру."""
    fetch = fake_fetch({}, default=EMPTY)
    obj, broken = run(RC_RECIPE, ORIGIN, FUND_UNTIL, fetch)
    assert broken is None
    assert obj["status"] == "partial"
    assert obj["sales_window"]["rule"] == "no_free_cells"


def test_all_windows_empty_without_recipe_houses_is_honest():
    fetch = fake_fetch({}, default=EMPTY)
    obj, broken = run(recipe_without("apartment_ids", "apartment_titles"),
                      date(2026, 9, 8), date(2026, 9, 9), fetch)
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "ни одного домика" in obj["reason"]


# ---------------------------------------------------------------------------
# Рецепт без нужного и подпись пробника
# ---------------------------------------------------------------------------

def test_recipe_without_token_is_honest():
    obj, broken = run(recipe_without("token"), date(2026, 9, 8),
                      date(2026, 9, 9), fake_fetch({}))
    assert broken and "token" in broken
    assert obj["status"] == "insufficient_data"


def test_recipe_without_date_placeholders_is_honest():
    recipe = json.loads(json.dumps(RC_RECIPE))
    recipe["request"]["url_template"] = "https://realtycalendar.ru/x.json"
    obj, broken = run(recipe, date(2026, 9, 8), date(2026, 9, 9),
                      fake_fetch({}))
    assert broken and "date_from" in broken


def test_probe_declares_its_version():
    from probes import _common
    assert isinstance(rc_bookings.PROBE_VERSION, int)
    assert _common.probe_version_of("rc-bookings", rc_bookings) == \
        f"rc-bookings@{rc_bookings.PROBE_VERSION}"


def test_settings_fixture_names_our_object():
    """Evidence разведки: токен — агентство «ЗАгород», Самара."""
    settings = load("settings.json")
    assert settings["agency_name"] == "ЗАгород"
    assert settings["url"].endswith(TOKEN)
