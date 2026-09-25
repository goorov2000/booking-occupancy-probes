# -*- coding: utf-8 -*-
"""Пробник Bnovo: алиас варианта reservationsteps, живые фикстуры, ошибки.

Разведка 14.08.2026 (тикет 03): «модуль Bnovo» и «вариант reservationsteps»
из travelline.py — одна платформа (см. докстринг probes/bnovo.py), поэтому
пробник bnovo — тонкое переиспользование того же кода. Тесты здесь проверяют
внешнее поведение шва: диспетчер probes.run_recipe знает engine=bnovo,
снапшот несёт engine=bnovo, семантика клеток и ошибок совпадает с
reservationsteps.

Фикстуры — обезличенные живые ответы public-api.reservationsteps.ru,
снятые 14.08.2026 (тела — только даты и цены, без cookies/токенов):
- min_prices_aureki_white_house.json — a_ureki (aureki.ru), категория
  «Белый Дом» (172333), 14.08.2026-31.03.2027: живые тарифы кончаются
  29.12.2026, дальше все ночи null;
- min_prices_chekhov_glamping.json — chekhovapi (chekhovapi.com), категория
  «Глемпинг с красивым видом на закат» (140679), 14.08.2026-31.01.2027.
"""
import json
import re
from datetime import date
from pathlib import Path

import pytest

import occupancy_core as core
import probes
from probes import bnovo, travelline

FIXTURES = Path(__file__).parent / "fixtures" / "bnovo"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


BNOVO_RECIPE = {
    "site": "https://aureki.ru/",
    "engine": "bnovo",
    "status": "ok",
    "request": {
        "url_template": ("https://public-api.reservationsteps.ru/v1/api/"
                         "min_prices?uid={uid}&dfrom={date_from}&dto={date_to}"
                         "&room_type_id={room_type_id}"),
        "method": "GET",
        "params": {
            "uid": "test-uid",
            "room_types": {"172333": "Белый Дом", "324452": "Мятный дом"},
        },
        "headers": {},
        "date_substitution": "dfrom/dto = DD-MM-YYYY",
    },
    "discovered_at": "2026-08-14T19:30:00+03:00",
    "notes": "",
    "source_urls": [],
}


def fake_fetch(responses, inventory=None):
    """Фейковый fetch: room_type_id -> ответ (payload | (status, payload) | исключение).

    inventory — {"YYYY-MM-DD": ответ /v1/api/rooms} для шага фонда типов;
    по умолчанию остатки не отдаются и объект считается по типам.
    """
    def fetch(url, headers, body=None):
        if "/api/rooms" in url:
            m = re.search(r"dfrom=(\d{2})-(\d{2})-(\d{4})", url)
            night = f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""
            return 200, (inventory or {}).get(night, {"rooms": []})
        for room_id, response in responses.items():
            if f"room_type_id={room_id}" in url:
                if isinstance(response, Exception):
                    raise response
                return response if isinstance(response, tuple) else (200, response)
        raise AssertionError(f"неожиданный url: {url}")
    return fetch


# ---------------------------------------------------------------------------
# Живая фикстура a_ureki: сетка по категориям через диспетчер
# ---------------------------------------------------------------------------

def test_run_recipe_dispatches_bnovo_and_builds_per_unit_grid():
    fetch = fake_fetch({
        "172333": load("min_prices_aureki_white_house.json"),
        "324452": {"min_prices": {"2026-08-25": {"p": 14300, "g": 2},
                                  "2026-08-26": None}},
    })
    obj, broken = probes.run_recipe("a_ureki", BNOVO_RECIPE,
                                    date(2026, 8, 25), date(2026, 8, 26),
                                    fetch=fetch)
    assert broken is None
    assert obj["status"] == "ok"
    assert obj["engine"] == "bnovo"
    assert obj["granularity"] == "per_unit"
    # Белый Дом по живой фикстуре: 25-26.08 — единственные августовские ночи с ценой
    assert obj["units"]["Белый Дом"]["2026-08-25"] == {"state": "free",
                                                      "price": 15600}
    assert obj["units"]["Мятный дом"]["2026-08-26"] == {"state": "busy"}
    assert len(obj["source_urls"]) == 2
    assert "dfrom=25-08-2026" in obj["source_urls"][0]


def test_live_fixture_august_pattern_matches_probe_semantics():
    """Август a_ureki из живой фикстуры: null -> busy, цена -> free."""
    fetch = fake_fetch({"172333": load("min_prices_aureki_white_house.json"),
                        "324452": {"min_prices": {}}})
    obj, broken = probes.run_recipe("a_ureki", BNOVO_RECIPE,
                                    date(2026, 8, 14), date(2026, 8, 31),
                                    fetch=fetch)
    assert broken is None
    white = obj["units"]["Белый Дом"]
    # сетка глубже августа (dto расширяется минимум до 2027-03, ревью 14.08),
    # августовский паттерн смотрим срезом по месяцу
    aug_busy = [d for d, c in white.items()
                if d.startswith("2026-08") and c["state"] == "busy"]
    aug_free = [d for d, c in white.items()
                if d.startswith("2026-08") and c["state"] == "free"]
    assert len(aug_busy) == 16 and len(aug_free) == 2
    assert aug_free == ["2026-08-25", "2026-08-26"]
    # пустой min_prices ({}) = тарифов нет вообще -> sales_not_open
    assert obj["units"]["Мятный дом"]["2026-08-14"] == {
        "state": "sales_not_open"}


def test_live_fixture_nulls_beyond_sales_window_read_as_busy_upper_bound():
    """За окном продаж (2027) все ночи null: САМ ПРОБНИК честно даёт busy —
    оценка сверху, потому горизонт сводки держится в окне продаж.

    Ночи ЗА пределом фикстуры (она кончается 31.03.2027) с 04.09 попадают в
    сетку: глубина стала скользящей и уходит на год вперёд. Данных о них в
    ответе нет, поэтому там честный unknown, — проверяем окно, которое
    фикстура покрывает.
    """
    fetch = fake_fetch({"172333": load("min_prices_aureki_white_house.json"),
                        "324452": {"min_prices": {}}})
    obj, _ = travelline.probe("a_ureki", BNOVO_RECIPE, date(2027, 3, 1),
                              date(2027, 3, 3), fetch=fetch)
    covered = {d: c for d, c in obj["units"]["Белый Дом"].items()
               if d <= "2027-03-31"}
    assert covered
    assert all(c["state"] == "busy" for c in covered.values())


def test_wall_of_nulls_is_not_a_sellout_after_dispatcher():
    """Тикет 07: та же сетка через ДИСПЕТЧЕР перестаёт быть 100% занятостью.

    Оценка сверху осталась внутри пробника, а решение «это не аншлаг, а
    закрытое окно» принимает probes.run_recipe: у объекта нет ни одной
    свободной клетки на весь горизонт, и цифра по нему не показывается.
    """
    fetch = fake_fetch({"172333": load("min_prices_aureki_white_house.json"),
                        "324452": {"min_prices": {}}})
    obj, broken = probes.run_recipe("a_ureki", BNOVO_RECIPE,
                                    date(2027, 3, 1), date(2027, 3, 3),
                                    fetch=fetch)
    assert broken is None
    assert obj["status"] == "partial"
    assert "цифра не показывается" in obj["reason"]
    covered = {d: c for d, c in obj["units"]["Белый Дом"].items()
               if d <= "2027-03-31"}
    assert all(c["state"] == "unknown" for c in covered.values())


def test_chekhov_glamping_fixture_prices_and_gaps():
    fetch = fake_fetch({"172333": {"min_prices": {}},
                        "324452": load("min_prices_chekhov_glamping.json")})
    obj, broken = probes.run_recipe("chekhovapi", BNOVO_RECIPE,
                                    date(2026, 8, 14), date(2026, 8, 20),
                                    fetch=fetch)
    assert broken is None
    glamping = obj["units"]["Мятный дом"]  # имя из рецепта, тело — фикстура
    assert glamping["2026-08-14"] == {"state": "busy"}
    assert glamping["2026-08-16"] == {"state": "free", "price": 11600}


# ---------------------------------------------------------------------------
# Ошибки: сеть не ломает рецепт, схема/4xx ломают
# ---------------------------------------------------------------------------

def test_probe_bnovo_network_failure_is_not_broken():
    fetch = fake_fetch({"172333": ConnectionError("таймаут"),
                        "324452": ConnectionError("таймаут")})
    obj, broken = probes.run_recipe("a_ureki", BNOVO_RECIPE,
                                    date(2026, 8, 14), date(2026, 8, 15),
                                    fetch=fetch)
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "сетевой сбой" in obj["reason"]
    assert obj["units"]["Белый Дом"]["2026-08-14"] == {"state": "unknown"}


def test_probe_bnovo_schema_change_marks_broken():
    fetch = fake_fetch({"172333": {"совсем": "не то"},
                        "324452": {"тоже": "не то"}})
    obj, broken = probes.run_recipe("a_ureki", BNOVO_RECIPE,
                                    date(2026, 8, 14), date(2026, 8, 14),
                                    fetch=fetch)
    assert broken is not None and "min_prices" in broken
    assert obj["status"] == "insufficient_data"
    assert obj["reason"] == broken


def test_probe_bnovo_http_403_is_refusal_not_broken():
    """Тикет 03: 403 — отказ хоста, а не смена схемы; рецепт остаётся живым.

    Счётчик суток отказа ведёт CLI (occupancy_core.note_refusal): пометка
    broken ставится, только когда нас не пускают несколько суток подряд.
    """
    fetch = fake_fetch({"172333": (403, {"error": "forbidden"}),
                        "324452": (403, {"error": "forbidden"})})
    obj, broken = probes.run_recipe("a_ureki", BNOVO_RECIPE,
                                    date(2026, 8, 14), date(2026, 8, 14),
                                    fetch=fetch)
    assert broken is None
    assert obj["refusal"]["status"] == 403
    assert obj["status"] == "insufficient_data"


# ---------------------------------------------------------------------------
# Алиас: bnovo не дублирует код reservationsteps
# ---------------------------------------------------------------------------

def test_bnovo_probe_is_travelline_reservationsteps_alias():
    assert bnovo.probe is travelline.probe
    assert probes.ENGINES["bnovo"] is bnovo


# ---------------------------------------------------------------------------
# Фонд типов Bnovo (правка 15.08): min_prices знает только «продаётся ли хоть
# один номер категории», а «Этнодом» у a_ureki держит минимум три. Остаток
# отдаёт /v1/api/rooms полем available; без account_id — страничный фолбэк.
# ---------------------------------------------------------------------------

def load_text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


INVENTORY_RECIPE = json.loads(json.dumps(BNOVO_RECIPE))
INVENTORY_RECIPE["request"]["params"]["account_id"] = 11896
INVENTORY_RECIPE["request"]["params"]["room_types"] = {
    "191755": "Этнодом в современном русском стиле", "172335": "Синий Дом"}


def test_parse_rooms_inventory_live_fixture():
    rest = travelline.parse_rooms_inventory(load("rooms_inventory_aureki.json"))
    assert rest["191755"] == 3      # этнодомов свободно три
    assert rest["172335"] == 1
    assert travelline.parse_rooms_inventory(
        load("rooms_inventory_sold_out.json")) == {}


def test_parse_rooms_inventory_schema_change_raises():
    with pytest.raises(travelline.SchemaChanged):
        travelline.parse_rooms_inventory({"categories": []})
    with pytest.raises(travelline.SchemaChanged):
        travelline.parse_rooms_inventory({"rooms": [{"name": "без id"}]})


def test_parse_rooms_page_inventory_fallback_live_html():
    rest = travelline.parse_rooms_page_inventory(load_text("rooms_page_selects.html"))
    assert rest["191755"] == 3      # то же число, что в JSON-ответе
    assert rest["172335"] == 1
    with pytest.raises(travelline.SchemaChanged):
        travelline.parse_rooms_page_inventory("<div>вёрстка сменилась</div>")


def test_probe_reservationsteps_fills_capacity_from_rooms_api():
    fetch = fake_fetch(
        {"191755": {"min_prices": {"2026-09-15": {"p": 13000, "g": 2}}},
         "172335": {"min_prices": {"2026-09-15": {"p": 12000, "g": 2}}}},
        inventory={"2026-09-15": load("rooms_inventory_aureki.json")})
    obj, broken = probes.run_recipe("a_ureki", INVENTORY_RECIPE,
                                    date(2026, 9, 15), date(2026, 9, 15),
                                    fetch=fetch)
    assert broken is None
    ethno = obj["units"]["Этнодом в современном русском стиле"]["2026-09-15"]
    assert ethno["units_total"] == 3 and ethno["units_free"] == 3
    blue = obj["units"]["Синий Дом"]["2026-09-15"]
    assert blue["units_total"] == 1 and blue["units_free"] == 1
    assert core.unit_basis(obj["units"])["homes"] == 4


def test_probe_reservationsteps_sold_out_night_counts_all_homes_busy():
    """Ночь, где /v1/api/rooms отдаёт пустой список: проданы все номера
    категории — клетка весит весь фонд, а не один домик."""
    fetch = fake_fetch(
        {"191755": {"min_prices": {"2026-09-15": {"p": 13000, "g": 2},
                                   "2026-09-16": None}},
         "172335": {"min_prices": {"2026-09-15": None, "2026-09-16": None}}},
        inventory={"2026-09-15": load("rooms_inventory_aureki.json"),
                   "2026-09-16": load("rooms_inventory_sold_out.json")})
    obj, _ = probes.run_recipe("a_ureki", INVENTORY_RECIPE,
                               date(2026, 9, 15), date(2026, 9, 16),
                               fetch=fetch)
    ethno = obj["units"]["Этнодом в современном русском стиле"]
    assert ethno["2026-09-16"] == {"state": "busy", "units_total": 3,
                                   "units_free": 0}
    m = core.aggregate(obj["units"], ["2026-09"])[0]
    # домико-ночи 15-го: этнодом 0 занятых из 3; «Синий Дом» календарь считает
    # проданным, а остаток отдаёт 1 — источники спорят, клетка осталась
    # бинарной (1 из 1). 16-го проданы все: 3 из 3 и 1 из 1.
    assert m["cuts"]["all"] == {"busy": 5, "known": 8, "pct": 62.5}


def test_probe_reservationsteps_page_fallback_when_no_account_id():
    """Рецепт без account_id (у объекта нет формы листа ожидания) — фонд
    снимается со страницы подбора, тяжелее, но снимается."""
    page = load_text("rooms_page_selects.html")
    seen = []

    def text_fetch(url, headers):
        seen.append(url)
        return 200, page

    fetch = fake_fetch(
        {"191755": {"min_prices": {"2026-09-15": {"p": 13000, "g": 2}}},
         "172335": {"min_prices": {"2026-09-15": {"p": 12000, "g": 2}}}})
    obj, _ = travelline.probe("a_ureki", BNOVO_RECIPE_NO_ACCOUNT,
                              date(2026, 9, 15), date(2026, 9, 15),
                              fetch=fetch, text_fetch=text_fetch)
    assert seen and "rooms/index" in seen[0] and "dfrom=15-09-2026" in seen[0]
    ethno = obj["units"]["Этнодом в современном русском стиле"]["2026-09-15"]
    assert ethno["units_total"] == 3


BNOVO_RECIPE_NO_ACCOUNT = json.loads(json.dumps(INVENTORY_RECIPE))
BNOVO_RECIPE_NO_ACCOUNT["request"]["params"].pop("account_id")


def test_sold_out_page_is_empty_inventory_not_schema_change():
    """Ночь без свободных номеров: карточек на странице нет вовсе (живой
    yck_kuzminskoe на 15-16.08.2026). Это пустой остаток, а не поломка."""
    page = '<script>\n    const categories = {"385130":{"name":"Дом"}};\n</script>'
    assert travelline.parse_rooms_page_inventory(page) == {}


# ---------------------------------------------------------------------------
# Объект с минимальным сроком проживания (разведка 15.08, dacha_limerence)
# ---------------------------------------------------------------------------

MIN_STAY_RECIPE = {
    "site": "https://limerence-dacha.ru",
    "engine": "bnovo",
    "status": "ok",
    "request": {
        "url_template": ("https://public-api.reservationsteps.ru/v1/api/"
                         "min_prices?uid={uid}&dfrom={date_from}&dto={date_to}"
                         "&room_type_id={room_type_id}"),
        "method": "GET",
        "params": {
            "uid": "test-uid",
            "min_stay": 2,
            "room_types": {"444541": "White House", "444542": "Black House"},
        },
        "headers": {},
        "date_substitution": "dfrom/dto = DD-MM-YYYY",
    },
    "discovered_at": "2026-08-15T17:05:00+03:00",
    "notes": "",
    "source_urls": [],
}


def rooms_page(available: dict) -> str:
    """Страница подбора с остатками {категория: свободно} (как у Bnovo)."""
    selects = "".join(
        f'<select data-real-room-id="{code}" data-available="{left}"></select>'
        for code, left in available.items())
    return ('<script>\n    const categories = {"444541":{"name":"White House"}};'
            f'\n</script>{selects}')


def window_fetch(pages):
    """text_fetch страницы подбора: дата заезда (YYYY-MM-DD) -> остатки.

    Заодно запоминает окна запросов — по ним проверяем длину окна.
    """
    windows = []

    def text_fetch(url, headers):
        m = re.search(r"dfrom=(\d{2})-(\d{2})-(\d{4})&dto=(\d{2})-(\d{2})-(\d{4})",
                      url)
        assert m, f"неожиданный url страницы: {url}"
        dfrom = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        dto = f"{m.group(6)}-{m.group(5)}-{m.group(4)}"
        windows.append((dfrom, dto))
        return 200, rooms_page(pages.get(dfrom, {}))

    text_fetch.windows = windows
    return text_fetch


def test_min_stay_object_is_read_by_page_windows_not_min_prices():
    """Объект с минимумом 2 ночи: сетка со страницы подбора, не из цен.

    Иначе min_prices отдаёт null на весь горизонт (заезд на одну ночь
    невозможен), и объект показывает ложные 100% занятости.
    """
    text_fetch = window_fetch({
        "2026-09-08": {"444541": 1, "444542": 1},
        "2026-09-09": {"444541": 1},
        "2026-09-10": {},
    })

    def fetch(url, headers, body=None):
        raise AssertionError("у объекта с минимальным сроком min_prices "
                             "спрашивать нельзя")

    obj, broken = travelline.probe("dacha_limerence", MIN_STAY_RECIPE,
                                   date(2026, 9, 8), date(2026, 9, 10),
                                   fetch=fetch, text_fetch=text_fetch)
    assert broken is None
    white = obj["units"]["White House"]
    black = obj["units"]["Black House"]
    assert white["2026-09-08"]["state"] == "free"
    assert white["2026-09-09"]["state"] == "free"
    assert white["2026-09-10"]["state"] == "busy"
    assert black["2026-09-09"]["state"] == "busy"
    # окно запроса — ровно минимальный срок
    assert text_fetch.windows[0] == ("2026-09-08", "2026-09-10")


# ---------------------------------------------------------------------------
# Аккаунт без публичных цен: сетка со страницы подбора окнами по ночи
# (params.grid_source="rooms", живой smr_shvedskie_dachi 09.09.2026)
# ---------------------------------------------------------------------------

ROOMS_GRID_RECIPE = json.loads(json.dumps(MIN_STAY_RECIPE))
ROOMS_GRID_RECIPE["request"]["params"].pop("min_stay")
ROOMS_GRID_RECIPE["request"]["params"]["grid_source"] = "rooms"


def test_rooms_grid_source_reads_windows_of_one_night():
    """grid_source=rooms: сетка со страницы подбора окнами РОВНО в ночь.

    У аккаунта с выключенным показом цен (has_to_show_calendar_prices=0)
    min_prices отвечает пустотой на любые даты, и обычный путь покрасил бы
    весь горизонт sales_not_open — то есть цифр не было бы вовсе. Страница
    подбора при этом отдаёт остатки по каждой категории.
    """
    text_fetch = window_fetch({
        "2026-09-08": {"444541": 1, "444542": 1},
        "2026-09-09": {"444541": 1},
        "2026-09-10": {},
    })

    def fetch(url, headers, body=None):
        raise AssertionError("grid_source=rooms не спрашивает min_prices")

    obj, broken = travelline.probe("smr_shvedskie_dachi", ROOMS_GRID_RECIPE,
                                   date(2026, 9, 8), date(2026, 9, 10),
                                   fetch=fetch, text_fetch=text_fetch)
    assert broken is None
    assert obj["units"]["White House"]["2026-09-09"]["state"] == "free"
    assert obj["units"]["Black House"]["2026-09-09"]["state"] == "busy"
    assert obj["units"]["White House"]["2026-09-10"]["state"] == "busy"
    # окно ровно одна ночь — вопрос «свободна ли ночь», а не «можно ли заезд»
    assert text_fetch.windows[0] == ("2026-09-08", "2026-09-09")


def test_rooms_grid_source_does_not_claim_a_minimum_stay():
    """Окно в одну ночь оговорки про минимальный срок не требует.

    Оговорка «клетка показывает, можно ли НАЧАТЬ заезд» осмысленна только
    при минимуме от двух ночей; на однночном окне она бы врала читателю про
    ограничение, которого у объекта нет.
    """
    text_fetch = window_fetch({"2026-09-08": {"444541": 1}})
    obj, _ = travelline.probe("smr_shvedskie_dachi", ROOMS_GRID_RECIPE,
                              date(2026, 9, 8), date(2026, 9, 8),
                              fetch=None, text_fetch=text_fetch)
    assert obj["status"] == "ok"
    assert "минимальный срок" not in (obj.get("reason") or "")


def test_min_stay_object_says_what_the_cell_means():
    """Оговорка про смысл клетки видна читателю: объект уходит в partial."""
    text_fetch = window_fetch({"2026-09-08": {"444541": 1}})
    obj, _ = travelline.probe("dacha_limerence", MIN_STAY_RECIPE,
                              date(2026, 9, 8), date(2026, 9, 8),
                              fetch=None, text_fetch=text_fetch)
    assert obj["status"] == "partial"
    assert "минимальный срок" in obj["reason"]
    assert "НАЧАТЬ заезд" in obj["reason"]


def test_min_stay_object_is_not_reported_as_fully_booked():
    """Регрессия 15.08: свободные окна не превращаются в 100% занятости."""
    pages = {f"2026-09-{day:02d}": {"444541": 1, "444542": 1}
             for day in range(8, 15)}
    obj, _ = travelline.probe("dacha_limerence", MIN_STAY_RECIPE,
                              date(2026, 9, 8), date(2026, 9, 14),
                              fetch=None, text_fetch=window_fetch(pages))
    metrics = core.aggregate(obj["units"], ["2026-09"])[0]
    assert metrics["cuts"]["all"]["pct"] == 0.0
    assert metrics["cuts"]["all"]["known"] == 14  # 7 ночей * 2 домика


def test_min_stay_without_page_access_is_honest_not_hundred_percent():
    """Страницу снять нечем — честное «нет данных», а не выдуманная сетка."""
    obj, broken = travelline.probe("dacha_limerence", MIN_STAY_RECIPE,
                                   date(2026, 9, 8), date(2026, 9, 9),
                                   fetch=lambda *_: pytest.fail("JSON не нужен без account_id"),
                                   text_fetch=None)
    assert broken is None  # отсутствие транспорта не доказывает смену схемы
    assert obj["status"] == "insufficient_data"
    assert obj["units"] == {}
    assert obj["reason"]


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
    fetch = fake_fetch({"172333": (200, None), "324452": (200, None)})
    fetch.last_body_text = CHALLENGE_PAGE
    obj, broken = probes.run_recipe("a_ureki", BNOVO_RECIPE,
                                    date(2026, 8, 14), date(2026, 8, 14),
                                    fetch=fetch)
    assert broken is None
    assert "не пустили" in obj["refusal"]["reason"]
    assert obj["status"] == "insufficient_data"


# ---------------------------------------------------------------------------
# Тикет 06 + ревью волны 2: у ветки min_stay сетка кончается вместе с фондом
# ---------------------------------------------------------------------------
# Здесь каждая ночь — отдельный запрос страницы подбора, поэтому горизонт
# фонда режет и сетку. Молчать об этом нельзя: месяц за границей иначе
# читается сводкой как обычное «нет данных».

def test_min_stay_branch_names_the_grid_boundary():
    text_fetch = window_fetch({})
    obj, broken = travelline.probe(
        "dacha_limerence", MIN_STAY_RECIPE, date(2026, 9, 4),
        date(2027, 9, 4), fetch=fake_fetch({}),
        text_fetch=text_fetch, inventory_date_to=date(2026, 9, 13))
    assert broken is None
    assert obj["grid_until"] == "2026-09-13"
    assert obj["inventory_until"] == "2026-09-13"
    assert "сетка снята до 2026-09-13" in obj["reason"]
    assert len(text_fetch.windows) == 10        # ночей фонда ровно 10


# ---------------------------------------------------------------------------
# Ревью волны 3: заслон на HTML-пути опознаётся, а не читается как вёрстка
# ---------------------------------------------------------------------------
# Тем же путём (страница подбора вместо /v1/api/rooms) ходят четыре живые
# цели без account_id. Cloudflare отдаёт «Just a moment…» с кодом 200, и
# каскад до правки уходил в ветку ok: страница доезжала до парсера и
# вылетала как «на странице rooms/index нет блока categories — вёрстка
# сменилась», то есть ломала живой рецепт.

CHALLENGE_HTML = ("<!DOCTYPE html><html><head><title>Just a moment...</title>"
                  "</head><body>Checking your browser</body></html>")


def test_challenge_on_the_page_fallback_is_not_a_layout_change():
    def text_fetch(url, headers):
        return 200, CHALLENGE_HTML

    fetch = fake_fetch(
        {"191755": {"min_prices": {"2026-09-15": {"p": 13000, "g": 2}}},
         "172335": {"min_prices": {"2026-09-15": {"p": 12000, "g": 2}}}})
    obj, broken = travelline.probe("a_ureki", BNOVO_RECIPE_NO_ACCOUNT,
                                   date(2026, 9, 15), date(2026, 9, 15),
                                   fetch=fetch, text_fetch=text_fetch)
    assert broken is None                      # рецепт жив
    assert "вёрстка" not in obj["reason"]
    assert "не пустили" in obj["reason"]
    # сетка из min_prices осталась: фонд типов — шаг вспомогательный
    assert obj["units"]["Этнодом в современном русском стиле"]["2026-09-15"][
        "state"] == "free"


def test_challenge_on_the_min_stay_branch_does_not_break_the_recipe():
    """У объекта min_stay страница подбора несёт ВСЮ сетку — тем важнее."""
    def text_fetch(url, headers):
        return 200, CHALLENGE_HTML

    def fetch(url, headers, body=None):
        raise AssertionError("ветка min_stay ходит только на страницу подбора")

    obj, broken = travelline.probe("dacha_limerence", MIN_STAY_RECIPE,
                                   date(2026, 9, 4), date(2026, 9, 6),
                                   fetch=fetch, text_fetch=text_fetch)
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "не пустили" in obj["reason"]
