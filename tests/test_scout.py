# -*- coding: utf-8 -*-
"""Разведчик scout.py: опознание движка по живому HTML, добыча параметров, три исхода.

Фикстуры — фрагменты РЕАЛЬНЫХ страниц, снятых обычным GET 04.09.2026
(по одному запросу на сайт, пауза 1,5 с, ретраев нет); вырезано всё, кроме
блока модуля бронирования:
- bnovo_dachi63.html — dachi63.ru, Tilda-эмбед Bnovo_Widget.open с uid;
- bronirui_lakeville.html — lakevilleglamping.ru, znmsWidget.init с moduleId;
- travelline_prostory.html — prostory-village.ru, TL head script с setContext;
- litepms_smolarelaks.html — смоларелакс.рф, var litepmsembed_id=11820;
- homereserve_baninaozerah.html — baninaozerah.ru, initWidgetSearch с token
  и списком домиков (JSON внутри Tilda, поэтому кавычки экранированы);
- uhotels_pineriver_booking.html и two_engines_pineriver_booking.html —
  pineriver.ru/booking: во втором оставлены ОБА маркера (мёртвый хвост
  TravelLine 2022 года поверх живого UHotels) — прецедент решения Р5 спеки;
- no_engine_landing.html — начало страницы без единого маркера модуля;
- antibot_challenge.html — единственная СИНТЕТИЧЕСКАЯ фикстура (живой капчи
  04.09 не встретилось, а исход refuse на антиботе проверить надо).

Сеть в тестах не трогается ни разу: везде подменённый fetch по словарю
маршрутов. Рецепты-черновики никуда, кроме указанного out_dir, не пишутся —
это отдельный тест (приёмка тикета 11).
"""
import json
from datetime import date
from pathlib import Path

import pytest

import scout

FIXTURES = Path(__file__).parent / "fixtures" / "scout"
BNOVO_FIXTURES = Path(__file__).parent / "fixtures" / "bnovo"
LITEPMS_FIXTURES = Path(__file__).parent / "fixtures" / "litepms"

TODAY = date(2026, 9, 4)


def load(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def fake_fetch(routes, calls=None):
    """Подменённый fetch: {подстрока url: (status, тело) | тело | исключение}.

    Маршруты сверяются от самого длинного ключа к короткому — иначе ключ
    сайта перехватывал бы и его же страницу /booking.
    """
    order = sorted(routes, key=len, reverse=True)

    def fetch(url, headers, payload=None):
        if calls is not None:
            calls.append((url, payload))
        for key in order:
            if key in url:
                answer = routes[key]
                if isinstance(answer, Exception):
                    raise answer
                return answer if isinstance(answer, tuple) else (200, answer)
        raise AssertionError(f"неожиданный запрос разведчика: {url}")

    return fetch


# ---------------------------------------------------------------------------
# Опознание: шесть движков на живом HTML
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture,engine", [
    ("bnovo_dachi63.html", "bnovo"),
    ("bronirui_lakeville.html", "bronirui"),
    ("travelline_prostory.html", "travelline"),
    ("litepms_smolarelaks.html", "litepms"),
    ("homereserve_baninaozerah.html", "homereserve"),
    ("uhotels_pineriver_booking.html", "uhotels"),
])
def test_detects_engine_in_live_html(fixture, engine):
    found = scout.detect_engines(load(fixture))
    strong = [f["engine"] for f in found if f["strength"] == "strong"]
    assert strong == [engine], found


def test_detect_returns_all_engines_not_the_first_hit():
    """Прецедент pineriver: на одной странице два движка, первый — мёртвый."""
    found = scout.detect_engines(load("two_engines_pineriver_booking.html"))
    strong = {f["engine"] for f in found if f["strength"] == "strong"}
    assert strong == {"travelline", "uhotels"}


def test_detect_finds_nothing_on_plain_landing():
    assert scout.detect_engines(load("no_engine_landing.html")) == []


# ---------------------------------------------------------------------------
# Три исхода
# ---------------------------------------------------------------------------

def test_two_strong_markers_refuse_with_reason():
    fetch = fake_fetch({"pineriver.ru": load("two_engines_pineriver_booking.html")})
    res = scout.scout("https://pineriver.ru", fetch, today=TODAY)
    assert res["verdict"] == "refuse"
    assert res["recipe_draft"] is None
    assert "travelline" in res["refusal_reason"]
    assert "uhotels" in res["refusal_reason"]
    assert sorted(res["engines_found"]) == ["travelline", "uhotels"]


def test_http_403_is_refuse_not_empty_recipe():
    fetch = fake_fetch({"example-glamp.ru": (403, "Forbidden")})
    res = scout.scout("https://example-glamp.ru", fetch, today=TODAY)
    assert res["verdict"] == "refuse"
    assert res["recipe_draft"] is None
    assert "антибот" in res["refusal_reason"]


def test_captcha_body_is_refuse():
    fetch = fake_fetch({"example-glamp.ru": load("antibot_challenge.html")})
    res = scout.scout("https://example-glamp.ru", fetch, today=TODAY)
    assert res["verdict"] == "refuse"
    assert "антибот" in res["refusal_reason"]


def test_no_markers_is_refuse():
    fetch = fake_fetch({"example-glamp.ru": load("no_engine_landing.html")})
    res = scout.scout("https://example-glamp.ru", fetch, today=TODAY)
    assert res["verdict"] == "refuse"
    assert res["recipe_draft"] is None
    assert "маркер" in res["refusal_reason"]


def test_network_failure_is_not_retried():
    calls = []
    fetch = fake_fetch({"example-glamp.ru": OSError("обрыв соединения")}, calls)
    res = scout.scout("https://example-glamp.ru", fetch, today=TODAY,
                      pages=("",))
    assert res["verdict"] == "refuse"
    assert len(calls) == 1, calls
    assert "сетевой сбой" in res["refusal_reason"]


def test_budget_stops_requests_and_downgrades_to_low():
    calls = []
    fetch = fake_fetch({
        "dachi63.ru": load("bnovo_dachi63.html"),
        "reservationsteps.ru/rooms/index": "<html></html>",
    }, calls)
    res = scout.scout("https://dachi63.ru", fetch, today=TODAY,
                      pages=("",), budget=1)
    assert len(calls) == 1, calls
    assert res["verdict"] == "low"
    assert "бюджет" in " ".join(res["missing_params"] + [res.get("note", "")])


# ---------------------------------------------------------------------------
# Добыча параметров по движкам
# ---------------------------------------------------------------------------

ROOMS_INDEX = (BNOVO_FIXTURES / "rooms_page_selects.html").read_text(
    encoding="utf-8")


def test_bnovo_high_with_uid_and_room_types():
    fetch = fake_fetch({
        "dachi63.ru": load("bnovo_dachi63.html"),
        "reservationsteps.ru/rooms/index": ROOMS_INDEX,
    })
    res = scout.scout("https://dachi63.ru", fetch, today=TODAY, pages=("",))
    assert res["verdict"] == "high"
    assert res["engine"] == "bnovo"
    params = res["recipe_draft"]["request"]["params"]
    assert params["uid"] == "5605bc31-4034-4be1-b9c1-1850e1a66c7b"
    # справочник категорий — союз выдач на РАЗНЫЕ месяцы (сервер рендерит
    # только доступные на запрошенные даты)
    assert params["room_types"]["172335"] == "Синий Дом"
    assert res["units_found"] == len(params["room_types"])


def test_bnovo_room_types_skip_subrooms():
    """Селект подкровати несёт ЧУЖОЙ data-room-id при том же data-real-room-id
    (живая страница a_ureki: 174851 под категорией 172335) — в рецепт такие
    id попадать не должны, пробник спросит о них min_prices впустую."""
    room_types = scout.parse_bnovo_room_types(ROOMS_INDEX)
    assert "172335" in room_types
    assert "174851" not in room_types


def test_parse_account_id_reads_waiting_list_input():
    """Разметка — дословно из SKILL.md:96 (скрытое поле листа ожидания)."""
    html = '<form><input type="hidden" name="account_id" value="11896"></form>'
    assert scout.parse_account_id(html) == "11896"


def test_parse_account_id_absent_is_not_an_error():
    """У объекта с выключенным листом ожидания поля нет вовсе (проверка 15.08
    на yck_kuzminskoe) — это не ошибка, просто фонд пойдёт фолбэком."""
    assert scout.parse_account_id(ROOMS_INDEX) is None


def test_bnovo_unit_directory_is_union_of_date_pairs_in_different_months():
    calls = []
    fetch = fake_fetch({
        "dachi63.ru": load("bnovo_dachi63.html"),
        "reservationsteps.ru/rooms/index": ROOMS_INDEX,
    }, calls)
    scout.scout("https://dachi63.ru", fetch, today=TODAY, pages=("",),
                unit_pairs=2)
    dates = [url.split("dfrom=")[1].split("&")[0]
             for url, _ in calls if "dfrom=" in url]
    assert len(dates) == 2
    months = {d.split("-")[1] for d in dates}
    assert len(months) == 2, dates


def test_bnovo_without_unit_directory_is_low():
    fetch = fake_fetch({"dachi63.ru": load("bnovo_dachi63.html")})
    res = scout.scout("https://dachi63.ru", fetch, today=TODAY, pages=("",),
                      units=False)
    assert res["verdict"] == "low"
    assert "room_types" in res["missing_params"]
    # черновик всё равно есть — агенту есть с чем работать
    assert res["recipe_draft"]["request"]["params"]["uid"]
    assert res["recipe_draft"]["status"] == "broken"


def test_bronirui_module_id_and_numbers_over_post():
    numbers = json.dumps({"numbers": [
        {"id": 15001, "name": "A-frame у озера"},
        {"id": 15002, "name": "Купольный шатёр"},
    ]}, ensure_ascii=False)
    fetch = fake_fetch({
        "lakevilleglamping.ru": load("bronirui_lakeville.html"),
        "api.bronirui-online.ru/v2/numbers": numbers,
    })
    res = scout.scout("https://lakevilleglamping.ru", fetch, today=TODAY,
                      pages=("",), allow_post=True)
    assert res["verdict"] == "high"
    params = res["recipe_draft"]["request"]["params"]
    assert params["module_id"] == 7032
    # канонический вид справочника рецепта (тикет 04): имя и фонд номера.
    # Фонда в ответе нет — поле не выдумывается, пробник честно скажет
    # «считано по номерам без фонда».
    assert params["numbers"] == {"15001": {"name": "A-frame у озера"},
                                 "15002": {"name": "Купольный шатёр"}}


def test_bronirui_without_post_is_low_but_keeps_module_id():
    fetch = fake_fetch({"lakevilleglamping.ru": load("bronirui_lakeville.html")})
    res = scout.scout("https://lakevilleglamping.ru", fetch, today=TODAY,
                      pages=("",), allow_post=False)
    assert res["verdict"] == "low"
    assert "numbers" in res["missing_params"]
    assert res["recipe_draft"]["request"]["params"]["module_id"] == 7032


def test_travelline_context_resolves_hotel_code():
    profile = json.dumps({"booking-form": {"provider": "24118"}})
    info = json.dumps({"hotels": [{"room_types": [
        {"code": "1", "name": "Шатёр"}, {"code": "2", "name": "Домик"}]}]})
    fetch = fake_fetch({
        "prostory-village.ru": load("travelline_prostory.html"),
        "integration/profile": profile,
        "BookingForm/hotel_info": info,
    })
    res = scout.scout("https://prostory-village.ru", fetch, today=TODAY,
                      pages=("",))
    assert res["verdict"] == "high"
    assert res["engine"] == "travelline"
    assert res["recipe_draft"]["request"]["params"]["hotel_code"] == "24118"
    assert res["units_found"] == 2
    assert ("https://ru-ibe.tlintegration.ru/integration/profile/"
            "TL-INT-prostory-village-ru_2024-02-19/ru"
            in res["recipe_draft"]["source_urls"])


def test_travelline_403_on_profile_is_low_not_refuse():
    """403 Qrator на tlintegration — известная ложная тревога (SKILL.md:84):
    движок опознан, поэтому исход low (доразведка агентом), а не refuse."""
    fetch = fake_fetch({
        "prostory-village.ru": load("travelline_prostory.html"),
        "integration/profile": (403, "Forbidden"),
    })
    res = scout.scout("https://prostory-village.ru", fetch, today=TODAY,
                      pages=("",))
    assert res["verdict"] == "low"
    assert res["engine"] == "travelline"
    assert "hotel_code" in res["missing_params"]


def test_litepms_property_id_and_unit_count():
    fetch = fake_fetch({
        "смоларелакс.рф": load("litepms_smolarelaks.html"),
        "litepms.ru/widget/calendar": (
            LITEPMS_FIXTURES / "widget_calendar_dachavsosnah.html").read_text(
                encoding="utf-8"),
    })
    res = scout.scout("https://смоларелакс.рф", fetch, today=TODAY, pages=("",))
    assert res["verdict"] == "high"
    assert res["recipe_draft"]["request"]["params"]["property_id"] == "11820"
    assert res["units_found"] == 2


def test_idn_site_goes_to_headers_as_punycode():
    """Живой прогон 04.09 упал на смоларелакс.рф: заголовок кодируется
    latin-1, и кириллический Referer роняет запрос до сети."""
    fetch = fake_fetch({
        "смоларелакс.рф": load("litepms_smolarelaks.html"),
        "litepms.ru/widget/calendar": "<html></html>",
    })
    draft = scout.scout("https://смоларелакс.рф", fetch, today=TODAY,
                        pages=("",))["recipe_draft"]
    headers = draft["request"]["headers"]
    assert headers["Referer"] == "https://xn--80aaovbcdqufj.xn--p1ai/"
    for value in headers.values():
        value.encode("latin-1")  # падает, если в заголовке осталась кириллица
    # человеческий домен при этом остаётся в рецепте как есть
    assert draft["site"] == "https://смоларелакс.рф"


def test_homereserve_token_and_apartments_from_html():
    """Список домиков лежит в самом initWidgetSearch — лишнего запроса не надо."""
    fetch = fake_fetch({"baninaozerah.ru": load("homereserve_baninaozerah.html")})
    res = scout.scout("https://baninaozerah.ru", fetch, today=TODAY, pages=("",))
    assert res["verdict"] == "high"
    params = res["recipe_draft"]["request"]["params"]
    assert params["token"] == "Sh0gLTkBgP"
    assert params["apartment_ids"][:2] == [71049, 89311]
    assert res["units_found"] == 12


def test_homereserve_min_stay_from_apartments_when_post_allowed():
    apartments = json.dumps({"apartments": [
        {"id": 71049, "title": "Дом у воды", "min_stay": 2},
        {"id": 89311, "title": "Баня", "min_stay": 1},
    ]}, ensure_ascii=False)
    fetch = fake_fetch({
        "baninaozerah.ru": load("homereserve_baninaozerah.html"),
        "realtycalendar.ru": apartments,
    })
    res = scout.scout("https://baninaozerah.ru", fetch, today=TODAY,
                      pages=("",), allow_post=True)
    # берём САМОЕ строгое ограничение объекта: именно оно решает, врёт ли клетка
    assert res["min_stay"] == 2
    assert res["recipe_draft"]["min_stay"] == 2


def test_uhotels_hotel_token():
    fetch = fake_fetch({"pineriver.ru": load("uhotels_pineriver_booking.html")})
    res = scout.scout("https://pineriver.ru", fetch, today=TODAY, pages=("",))
    assert res["verdict"] == "high"
    assert res["engine"] == "uhotels"
    assert (res["recipe_draft"]["request"]["params"]["hotel"]
            == "227:eb7f316cebb6eb120859f27c5085b552")


def test_min_stay_key_is_always_in_draft():
    """Тикет 07 ждёт поле в рецепте всегда — движок молчит, значит null."""
    fetch = fake_fetch({"pineriver.ru": load("uhotels_pineriver_booking.html")})
    res = scout.scout("https://pineriver.ru", fetch, today=TODAY, pages=("",))
    assert "min_stay" in res["recipe_draft"]
    assert res["recipe_draft"]["min_stay"] is None


def test_unsupported_engine_draft_is_broken_with_reason():
    html = "<html><script src='/vendor/livewire/livewire.js'></script></html>"
    fetch = fake_fetch({"lafa-like.ru": html})
    res = scout.scout("https://lafa-like.ru", fetch, today=TODAY, pages=("",))
    assert res["verdict"] == "low"
    assert res["engine"] == "custom-livewire"
    assert res["recipe_draft"]["status"] == "broken"
    assert "не поддержан" in res["recipe_draft"]["broken_reason"]


# ---------------------------------------------------------------------------
# Черновик ложится в SCHEMA (а) без переделки
# ---------------------------------------------------------------------------

def test_draft_matches_recipe_schema_keys():
    fetch = fake_fetch({
        "смоларелакс.рф": load("litepms_smolarelaks.html"),
        "litepms.ru/widget/calendar": (
            LITEPMS_FIXTURES / "widget_calendar_dachavsosnah.html").read_text(
                encoding="utf-8"),
    })
    draft = scout.scout("https://смоларелакс.рф", fetch, today=TODAY,
                        pages=("",))["recipe_draft"]
    assert set(draft) >= {"site", "engine", "status", "request", "discovered_at",
                          "notes", "source_urls", "min_stay"}
    assert set(draft["request"]) == {"url_template", "method", "params",
                                     "headers", "date_substitution"}
    assert draft["status"] in ("ok", "broken", "no_module", "aggregator")
    assert draft["site"] == "https://смоларелакс.рф"


def test_draft_is_runnable_by_the_probe_as_is():
    """Главная проверка шва: черновик уходит в пробник без единой правки."""
    import probes

    page = (LITEPMS_FIXTURES / "widget_calendar_dachavsosnah.html").read_text(
        encoding="utf-8")
    scout_fetch = fake_fetch({
        "смоларелакс.рф": load("litepms_smolarelaks.html"),
        "litepms.ru/widget/calendar": page,
    })
    draft = scout.scout("https://смоларелакс.рф", scout_fetch, today=TODAY,
                        pages=("",))["recipe_draft"]

    def probe_fetch(url, headers):  # у litepms пробник ходит двумя аргументами
        assert "id=11820" in url
        return 200, page

    obj, broken = probes.run_recipe("smr_smolarelaks", draft,
                                    date(2026, 8, 14), date(2026, 10, 31),
                                    fetch=probe_fetch)
    assert broken is None
    assert obj["status"] == "ok"
    assert obj["granularity"] == "per_unit"


# ---------------------------------------------------------------------------
# Ключ цели и запись черновика
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,key", [
    ("https://dachi63.ru", "smr_dachi63"),
    ("https://piskali.ru/", "smr_piskali"),
    ("https://prostory-village.ru", "smr_prostory_village"),
    ("https://смоларелакс.рф", "smr_smolarelaks"),
    ("https://xn--80aaovbcdqufj.xn--p1ai", "smr_smolarelaks"),
])
def test_site_key_transliterates_and_prefixes(url, key):
    assert scout.site_key(url) == key


def test_cli_writes_draft_and_never_touches_recipes(tmp_path, monkeypatch):
    recipes = tmp_path / "recipes.json"
    before = json.dumps({"already_here": {"engine": "litepms"}},
                        ensure_ascii=False)
    recipes.write_text(before, encoding="utf-8")
    monkeypatch.setattr(scout.core, "DEFAULT_RECIPES", recipes, raising=False)

    out_dir = tmp_path / "scout-out"
    fetch = fake_fetch({"dachi63.ru": load("bnovo_dachi63.html"),
                        "reservationsteps.ru/rooms/index": ROOMS_INDEX})
    code = scout.main(["--url", "https://dachi63.ru", "--out-dir", str(out_dir),
                       "--pages", "", "--today", TODAY.isoformat()],
                      fetch=fetch)
    assert code == 0
    written = json.loads((out_dir / "smr_dachi63.json").read_text(
        encoding="utf-8"))
    assert written["verdict"] == "high"
    assert written["recipe_draft"]["engine"] == "bnovo"
    assert written["evidence"], "evidence обязателен (правило №1)"
    # реестр не тронут ни при каком исходе — приёмка тикета 11
    assert recipes.read_text(encoding="utf-8") == before


def test_cli_refusal_also_writes_a_draft_file(tmp_path):
    out_dir = tmp_path / "scout-out"
    fetch = fake_fetch({"pineriver.ru": load("two_engines_pineriver_booking.html")})
    code = scout.main(["--url", "https://pineriver.ru", "--out-dir", str(out_dir),
                       "--pages", "", "--key", "smr_two_engines",
                       "--today", TODAY.isoformat()], fetch=fetch)
    assert code == 0
    written = json.loads((out_dir / "smr_two_engines.json").read_text(
        encoding="utf-8"))
    assert written["verdict"] == "refuse"
    assert written["recipe_draft"] is None
    assert written["refusal_reason"]


def test_module_never_references_the_registry():
    """Реестр правит человек и тикет 12, а не разведчик (приёмка тикета 11)."""
    src = (Path(scout.__file__)).read_text(encoding="utf-8")
    body = src.split('"""', 2)[-1]  # докстринг модуля про реестр говорить может
    for forbidden in ("save_recipes", "DEFAULT_RECIPES", "recipes.json"):
        assert forbidden not in body, forbidden


# ---------------------------------------------------------------------------
# Волна 2, ревью wave1-scout: параметры ищутся на ВСЕХ снятых страницах
# ---------------------------------------------------------------------------

def test_params_are_collected_from_every_fetched_page():
    """Маркер на главной, uid — на /booking: обе страницы уже скачаны.

    Раскладка SKILL.md:94 («uid обычно на /booking») — самая частая на живых
    сайтах, а разведка отдавала экстрактору только ту страницу, где нашёлся
    маркер, и объект с полностью добываемым рецептом уходил в low.
    """
    fetch = fake_fetch({
        "dachi63.ru/": load("bnovo_marker_only_main.html"),
        "dachi63.ru/booking": load("bnovo_dachi63.html"),
        "reservationsteps.ru/rooms/index": ROOMS_INDEX,
    })
    res = scout.scout("https://dachi63.ru", fetch, today=TODAY,
                      pages=("", "booking"))
    assert res["verdict"] == "high", res["missing_params"]
    params = res["recipe_draft"]["request"]["params"]
    assert params["uid"] == "5605bc31-4034-4be1-b9c1-1850e1a66c7b"
    # обе страницы — evidence происхождения рецепта (правило №1)
    assert "https://dachi63.ru/booking" in res["recipe_draft"]["source_urls"]


# ---------------------------------------------------------------------------
# Волна 2: регулярки привязаны к вызову виджета, а не к первому совпадению
# ---------------------------------------------------------------------------

def test_homereserve_token_is_taken_from_the_widget_not_from_foreign_json():
    fetch = fake_fetch({
        "tilda-glamp.ru": load("homereserve_tilda_foreign_token.html")})
    res = scout.scout("https://tilda-glamp.ru", fetch, today=TODAY, pages=("",))
    assert res["engine"] == "homereserve"
    assert res["recipe_draft"]["request"]["params"]["token"] == "Sh0gLTkBgP"


def test_bronirui_module_id_is_taken_from_the_widget_not_from_analytics():
    fetch = fake_fetch({
        "znms-glamp.ru": load("bronirui_foreign_module_id.html")})
    res = scout.scout("https://znms-glamp.ru", fetch, today=TODAY, pages=("",))
    assert res["engine"] == "bronirui"
    assert res["recipe_draft"]["request"]["params"]["module_id"] == 7032


def test_uhotels_token_is_taken_from_the_hotel_key():
    """Токен ищется у ключа 'hotel' конфигурации artDg, а не где придётся."""
    html = ("<script>var counter='227:ffffffffffffffffffffffffffffffff';"
            "var q={'container':'adg-booking-widget','hotel':"
            "'228:eb7f316cebb6eb120859f27c5085b552'};w.artDg={};</script>")
    fetch = fake_fetch({"uh-glamp.ru": html})
    res = scout.scout("https://uh-glamp.ru", fetch, today=TODAY, pages=("",))
    assert (res["recipe_draft"]["request"]["params"]["hotel"]
            == "228:eb7f316cebb6eb120859f27c5085b552")


# ---------------------------------------------------------------------------
# Волна 2: «нас не пустили» — один предикат на весь скилл
# ---------------------------------------------------------------------------

def test_challenge_with_http_200_is_refuse_not_no_module():
    """Заслон Cloudflare отдаётся со статусом 200 — и должен быть опознан."""
    fetch = fake_fetch({"example-glamp.ru": load("cloudflare_challenge_200.html")})
    res = scout.scout("https://example-glamp.ru", fetch, today=TODAY)
    assert res["verdict"] == "refuse"
    assert res["access_refused"] is True
    assert "антибот" in res["refusal_reason"]
    assert "маркеров" not in res["refusal_reason"]


def test_challenge_markers_are_the_shared_ones():
    """Свой список маркеров разошёлся с общим — второго списка быть не должно."""
    from probes import _common

    for marker in _common.CHALLENGE_MARKERS:
        body = f"<html><body>{marker}</body></html>"
        assert scout.looks_like_antibot(200, body), marker


def test_refused_page_is_named_in_the_refusal_reason():
    """403 на /booking при живой главной: причина «модуля нет» — ложь.

    Тикет 12 раскладывает объекты по веткам доразведки именно по причине:
    объект за антиботом должен уехать к агенту с браузером, а не в «нет
    данных навсегда».
    """
    fetch = fake_fetch({
        "example-glamp.ru/": load("no_engine_landing.html"),
        "example-glamp.ru/booking": (403, "Forbidden"),
    })
    res = scout.scout("https://example-glamp.ru", fetch, today=TODAY,
                      pages=("", "booking"))
    assert res["verdict"] == "refuse"
    assert res["access_refused"] is True
    assert "booking" in res["refusal_reason"]
    assert "403" in res["refusal_reason"]


def test_access_refused_from_the_shared_transport_is_not_a_network_failure():
    """AccessRefused (подкласс OSError) — «хост просит подождать», не обрыв."""
    from probes import _common

    fetch = fake_fetch({"example-glamp.ru": _common.AccessRefused(
        "reservationsteps.ru просит подождать 900 с (Retry-After)")})
    res = scout.scout("https://example-glamp.ru", fetch, today=TODAY,
                      pages=("",))
    assert res["verdict"] == "refuse"
    assert res["access_refused"] is True
    assert "сетевой сбой" not in res["refusal_reason"]
    assert "подождать" in res["refusal_reason"]


# ---------------------------------------------------------------------------
# Волна 2: нежилые позиции модуля не считаются домиками
# ---------------------------------------------------------------------------

def test_tent_sites_are_not_units():
    """Живой piskali.ru: 52 «юнита», из них 36 — «Место под палатку».

    Считать их домиками нельзя дважды: они попадают в знаменатель занятости
    (и она занижается) и стоят по запросу пробника каждая.
    """
    fetch = fake_fetch({
        "piskali.ru": load("bnovo_dachi63.html"),
        "reservationsteps.ru/rooms/index": load("bnovo_rooms_tent_sites.html"),
    })
    res = scout.scout("https://piskali.ru", fetch, today=TODAY, pages=("",),
                      unit_pairs=1)
    room_types = res["recipe_draft"]["request"]["params"]["room_types"]
    assert sorted(room_types.values()) == ["Лесной домик с удобствами",
                                           "Оливковый домик на двоих"]


def test_excluded_positions_are_named_in_the_draft():
    fetch = fake_fetch({
        "piskali.ru": load("bnovo_dachi63.html"),
        "reservationsteps.ru/rooms/index": load("bnovo_rooms_tent_sites.html"),
    })
    res = scout.scout("https://piskali.ru", fetch, today=TODAY, pages=("",),
                      unit_pairs=1)
    assert res["units_found"] == 2
    assert res["units_excluded"] == 3
    assert "Место под палатку 1" in " ".join(res["excluded_units"])
    assert "нежил" in res["recipe_draft"]["notes"]


# ---------------------------------------------------------------------------
# Волна 2: ноль юнитов — это ноль, а не «не снималось»
# ---------------------------------------------------------------------------

def test_empty_directory_is_an_honest_zero():
    fetch = fake_fetch({
        "dachi63.ru": load("bnovo_dachi63.html"),
        "reservationsteps.ru/rooms/index": load("bnovo_rooms_empty.html"),
    })
    res = scout.scout("https://dachi63.ru", fetch, today=TODAY, pages=("",),
                      unit_pairs=1)
    assert res["units_found"] == 0
    assert res["verdict"] == "low"  # room_types обязателен, а их ноль
    assert "не снималось" not in res["recipe_draft"]["notes"]
    assert "Юнитов найдено: 0" in res["recipe_draft"]["notes"]


def test_directory_not_scanned_stays_none():
    fetch = fake_fetch({"dachi63.ru": load("bnovo_dachi63.html")})
    res = scout.scout("https://dachi63.ru", fetch, today=TODAY, pages=("",),
                      units=False)
    assert res["units_found"] is None
    assert "не снималось" in res["recipe_draft"]["notes"]


# ---------------------------------------------------------------------------
# Волна 2: min_stay доходит до пробника и добывается GET-ом
# ---------------------------------------------------------------------------

def test_min_stay_lands_where_the_probe_reads_it():
    """Пробник читает recipe.request.params.min_stay (probes/travelline.py:343),
    а разведка писала его только в верхний уровень черновика."""
    apartments = json.dumps({"apartments": [
        {"id": 71049, "title": "Дом у воды", "min_stay": 2},
        {"id": 89311, "title": "Баня", "min_stay": 1},
    ]}, ensure_ascii=False)
    fetch = fake_fetch({
        "baninaozerah.ru": load("homereserve_baninaozerah.html"),
        "realtycalendar.ru": apartments,
    })
    res = scout.scout("https://baninaozerah.ru", fetch, today=TODAY,
                      pages=("",), allow_post=True)
    params = res["recipe_draft"]["request"]["params"]
    assert params["min_stay"] == 2
    # поюнитные минимумы не схлопываются: скаляр — самый строгий, но исходные
    # значения остаются в рецепте
    assert params["min_stay_by_unit"] == {"71049": 2, "89311": 1}


def test_homereserve_min_stay_falls_back_to_the_calendar():
    """У живого объекта apartments отдаёт min_stay=null во всех записях
    (фикстура homereserve/apartments_bani_na_ozerah.json), а срок лежит в
    календаре (фикстура calendar_bani_house.json) — оттуда и берём."""
    apartments = json.dumps({"apartments": [
        {"id": 71049, "title": "Дом у воды", "min_stay": None}]},
        ensure_ascii=False)
    calendar = json.dumps({"calendar": [
        {"date": "2026-09-04", "available": False, "min_stay": 2},
        {"date": "2026-09-05", "available": True, "min_stay": 2}]})
    fetch = fake_fetch({
        "baninaozerah.ru": load("homereserve_baninaozerah.html"),
        "realtycalendar.ru/v2/widget/Sh0gLTkBgP/apartments": apartments,
        "realtycalendar.ru/v2/widget/Sh0gLTkBgP/calendar": calendar,
    })
    res = scout.scout("https://baninaozerah.ru", fetch, today=TODAY,
                      pages=("",), allow_post=True)
    assert res["min_stay"] == 2
    assert res["recipe_draft"]["request"]["params"]["min_stay"] == 2


def test_bnovo_min_stay_is_detected_by_widening_the_window():
    """Окно в одну ночь пусто у ВСЕХ категорий, в две — свободно: это не
    аншлаг, а минимальный срок проживания (механика 15.08, dacha_limerence).
    Ходим только GET-ом и только когда однночное окно оказалось пустым."""
    calls = []
    fetch = fake_fetch({
        "dachi63.ru": load("bnovo_dachi63.html"),
        "rooms/index": load("bnovo_rooms_all_busy.html"),
        "dto=24-09-2026": load("bnovo_rooms_free_on_two_nights.html"),
    }, calls)
    res = scout.scout("https://dachi63.ru", fetch, today=TODAY, pages=("",),
                      unit_pairs=1)
    assert res["min_stay"] == 2
    assert res["recipe_draft"]["request"]["params"]["min_stay"] == 2
    assert all(payload is None for _, payload in calls), "только GET"


def test_bnovo_min_stay_stays_unknown_when_every_window_is_empty():
    """Пусто и на 1, и на 2, и на 3 ночи — это может быть закрытое окно
    продаж, а не минимальный срок. Выдумывать число нельзя."""
    calls = []
    fetch = fake_fetch({
        "dachi63.ru": load("bnovo_dachi63.html"),
        "rooms/index": load("bnovo_rooms_all_busy.html"),
    }, calls)
    res = scout.scout("https://dachi63.ru", fetch, today=TODAY, pages=("",),
                      unit_pairs=1)
    assert res["min_stay"] is None
    assert "min_stay" not in res["recipe_draft"]["request"]["params"]
    assert "минимальн" in res["note"]
    # лестница окон ограничена: главная + окна 1, 2 и 3 ночи
    assert len(calls) == 4, calls


def test_bnovo_min_stay_ladder_is_not_run_when_something_is_free():
    """Свободные номера есть — второй запрос за тем же самым не нужен."""
    calls = []
    fetch = fake_fetch({
        "dachi63.ru": load("bnovo_dachi63.html"),
        "rooms/index": ROOMS_INDEX,
    }, calls)
    res = scout.scout("https://dachi63.ru", fetch, today=TODAY, pages=("",),
                      unit_pairs=1)
    assert res["min_stay"] is None
    assert len(calls) == 2, calls


# ---------------------------------------------------------------------------
# Волна 2: два слабых маркера — не монетка
# ---------------------------------------------------------------------------

def test_two_weak_markers_of_different_engines_are_refuse():
    """Р5 спеки: неоднозначность решает агент. Молчаливый выбор по алфавиту
    уносил в черновик движок, которого на объекте может не быть вовсе."""
    fetch = fake_fetch({"weak-glamp.ru": load("weak_markers_two_engines.html")})
    res = scout.scout("https://weak-glamp.ru", fetch, today=TODAY, pages=("",))
    assert res["verdict"] == "refuse"
    assert res["recipe_draft"] is None
    assert sorted(res["engines_found"]) == ["bnovo", "travelline"]
    assert "bnovo" in res["refusal_reason"]
    assert "travelline" in res["refusal_reason"]


# ---------------------------------------------------------------------------
# Волна 2: транспорт — общий, усиленный (тикет 03)
# ---------------------------------------------------------------------------

class _FakeResponse:
    """Ответ по контракту requests: тело читается потоком, .text из _content."""

    def __init__(self, body=b"<html></html>", status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body
        self._content = b""
        self.encoding = "utf-8"
        self.closed = False

    def iter_content(self, size):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def close(self):
        self.closed = True

    @property
    def text(self):
        return self._content.decode(self.encoding, "replace")


class _FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, headers=None, timeout=None, stream=False):
        self.calls.append((url, headers, stream))
        return self.response

    def post(self, url, json=None, headers=None, timeout=None, stream=False):
        self.calls.append((url, headers, stream))
        return self.response


def test_live_fetch_reads_body_with_the_shared_cap(monkeypatch):
    """Разведка ходит по 31 НЕизвестному сайту — тело без потолка читать нельзя."""
    import requests

    from probes import _common

    monkeypatch.setattr(_common, "MAX_BODY_BYTES", 32)
    monkeypatch.setattr(_common, "_last_request_by_host", {})
    monkeypatch.setattr(_common, "_host_not_before", {})
    session = _FakeSession(_FakeResponse(body=b"x" * 1024))
    monkeypatch.setattr(requests, "Session", lambda: session)
    fetch = scout.make_fetch()
    with pytest.raises(_common.ResponseTooLarge):
        fetch("https://huge-glamp.ru/", {})


def test_live_fetch_respects_retry_after(monkeypatch):
    import requests

    from probes import _common

    monkeypatch.setattr(_common, "_last_request_by_host", {})
    monkeypatch.setattr(_common, "_host_not_before", {})
    session = _FakeSession(_FakeResponse(
        body=b"slow down", status_code=429, headers={"Retry-After": "120"}))
    monkeypatch.setattr(requests, "Session", lambda: session)
    fetch = scout.make_fetch()
    fetch("https://busy-glamp.ru/", {})
    assert _common._host_not_before.get("busy-glamp.ru") is not None


# ---------------------------------------------------------------------------
# Волна 3: фильтр нежилых позиций больше не выбрасывает домики
#
# Ревью волны 2 проверило список слов-признаков на 157 живых именах из боевых
# снапшотов (последние 4 прогона) и нашло перелёт: «мангал», «завтрак»,
# «парковк», «прокат», «обед» выбрасывали НАСТОЯЩИЕ домики (bani_na_ozerah
# 12 -> 10, glamping_iva_spa 6 -> 5), а вердикт оставался high. Принцип теперь
# другой: позиция отбрасывается, только если её назвал услугой САМ модуль
# (нулевая вместимость, тип-услуга, цена за место) либо в имени стоит признак
# нежилой позиции И нет ни одного признака жилья.
# ---------------------------------------------------------------------------

def test_a_house_with_a_grill_in_the_name_stays_a_house():
    """Имена дословно из боевых снапшотов 01-04.09 — все три позиции жильё."""
    fetch = fake_fetch({
        "dachi63.ru": load("bnovo_dachi63.html"),
        "reservationsteps.ru/rooms/index": load("bnovo_rooms_live_names.html"),
    })
    res = scout.scout("https://dachi63.ru", fetch, today=TODAY, pages=("",),
                      unit_pairs=1)
    names = res["recipe_draft"]["request"]["params"]["room_types"].values()
    assert "✨ Мечта - отдых у воды в стиле бохо, горячий чан, камин и мангал" \
        in names
    assert "🏡 Флора - светлый A-Frame у озера с горячим чаном, костровой " \
        "зоной и мангалом" in names
    assert "Комфорт с мангалом и холодильником. Сфера с панорамным видом на " \
        "лес" in names


def test_a_word_inside_another_word_is_not_a_service_marker():
    """«Дом «Победа»» ловился на подстроку «обед», «Домик с завтраком» — на
    «завтрак». Признак ищется по границе слова, а не по подстроке."""
    assert scout.unit_exclusion_reason("Дом «Победа»") is None
    assert scout.unit_exclusion_reason("Домик с завтраком") is None
    assert scout.unit_exclusion_reason("Купольный дом с парковкой") is None
    assert scout.unit_exclusion_reason("Дом с прокатом сапбордов") is None


def test_service_positions_are_dropped_and_the_reason_is_named():
    """Живые нежилые позиции из снапшотов и с piskali.ru: отбрасываются, но
    каждая названа вслух вместе с причиной — иначе агент не увидит потери."""
    units = {
        "1": "Место под палатку 1",
        "2": "Место под автодом",
        "3": "Стоянка для яхты/катера на причале",
        "4": "Подарочный сертификат",
        "5": "Депозит",
        "6": "Оливковый домик на двоих",
    }
    keep, drop = scout.partition_units(units)
    assert list(keep) == ["6"]
    assert set(drop) == {"1", "2", "3", "4", "5"}
    for uid, record in drop.items():
        assert record["name"] == units[uid]
        assert record["reason"], uid


def test_the_module_itself_can_call_a_position_a_service():
    """Признак самого модуля сильнее имени: нулевая вместимость, тип-услуга и
    цена за место отбрасывают позицию, как бы она ни называлась."""
    assert scout.unit_exclusion_reason(
        "Завтрак «Шведский стол»", {"is_service": True})
    assert scout.unit_exclusion_reason("Дом у озера", {"capacity": 0})
    assert scout.unit_exclusion_reason("Шатёр", {"type": "service"})
    assert scout.unit_exclusion_reason(
        "Место в шатре", {"price_unit": "за место"})
    # а жилая запись того же движка остаётся жильём
    assert scout.unit_exclusion_reason(
        "Дом у озера", {"capacity": 4, "type": "room"}) is None


def test_the_draft_names_what_was_dropped_and_why():
    fetch = fake_fetch({
        "piskali.ru": load("bnovo_dachi63.html"),
        "reservationsteps.ru/rooms/index": load("bnovo_rooms_tent_sites.html"),
    })
    res = scout.scout("https://piskali.ru", fetch, today=TODAY, pages=("",),
                      unit_pairs=1)
    assert res["units_excluded"] == 3
    told = res["excluded_units"]
    assert any("Место под палатку 1" in item for item in told)
    # у КАЖДОЙ отсеянной позиции названа причина, а не только имя
    assert all(" — " in item and len(item.split(" — ", 1)[1]) > 10
               for item in told), told
    assert "Место под палатку 1" in res["recipe_draft"]["notes"]


def test_split_residential_keeps_its_old_shape():
    """Обратная совместимость: прежняя функция жива и отдаёт {id: имя}."""
    keep, drop = scout.split_residential({"1": "Место под палатку",
                                          "2": "Дом у озера"})
    assert keep == {"2": "Дом у озера"}
    assert drop == {"1": "Место под палатку"}


# ---------------------------------------------------------------------------
# Волна 3: токен HomeReserve не берётся из маркера самого движка
# ---------------------------------------------------------------------------

def test_homereserve_token_survives_a_nested_object_in_the_widget_call():
    """Вложенный объект (guests) до ключа token — законная раскладка вызова:
    порядок ключей в конфиге виджета ничем не зафиксирован."""
    res = scout.scout("https://baninaozerah.ru",
                      fake_fetch({"baninaozerah.ru":
                                  load("homereserve_nested_call.html")}),
                      today=TODAY, pages=("",))
    assert res["recipe_draft"]["request"]["params"]["token"] == "Sh0gLTkBgP"
    assert res["verdict"] == "high"


def test_engine_marker_is_never_taken_for_a_homereserve_token():
    """homereserve.ru/widget.js — это СИЛЬНЫЙ МАРКЕР движка, а не токен.
    Запасная регулярка вычитывала из него token='widget' и отдавала заведомо
    ложный рецепт с вердиктом high (ревью волны 2)."""
    res = scout.scout("https://baninaozerah.ru",
                      fake_fetch({"baninaozerah.ru":
                                  load("homereserve_marker_only.html")}),
                      today=TODAY, pages=("",))
    assert res["engine"] == "homereserve"
    assert res["verdict"] == "low"
    assert "token" in res["missing_params"]
    assert res["recipe_draft"]["request"]["params"].get("token") is None


# ---------------------------------------------------------------------------
# Волна 3: неопределённый минимальный срок — не вердикт high
# ---------------------------------------------------------------------------

def test_bnovo_without_reserves_on_the_page_is_not_high():
    """Категории есть, селектов с остатком нет: свободные ночи не читаются, и
    молчать об этом нельзя (ревью волны 2 — вердикт был high, note пуст)."""
    fetch = fake_fetch({
        "dachi63.ru": load("bnovo_dachi63.html"),
        "reservationsteps.ru/rooms/index":
            load("bnovo_rooms_no_availability.html"),
    })
    res = scout.scout("https://dachi63.ru", fetch, today=TODAY, pages=("",),
                      unit_pairs=1)
    assert res["verdict"] == "low"
    assert "min_stay" in res["missing_params"]
    assert "остатк" in res["note"]


def test_bnovo_ladder_runs_when_a_one_night_window_shows_no_categories():
    """Пустая страница на однночном окне — не «категорий нет», а возможный
    минимальный срок: окно расширяется, и справочник берётся с него."""
    fetch = fake_fetch({
        "dachi63.ru": load("bnovo_dachi63.html"),
        "rooms/index": load("bnovo_rooms_empty.html"),
        "dto=24-09-2026": load("bnovo_rooms_free_on_two_nights.html"),
    })
    res = scout.scout("https://dachi63.ru", fetch, today=TODAY, pages=("",),
                      unit_pairs=1)
    assert res["min_stay"] == 2
    room_types = res["recipe_draft"]["request"]["params"]["room_types"]
    assert sorted(room_types.values()) == ["Лесной домик с удобствами",
                                           "Оливковый домик на двоих"]
    assert res["verdict"] == "high"


def test_unknown_min_stay_downgrades_the_verdict():
    """Пусто на 1, 2 и 3 ночи — минимальный срок неизвестен, а по нему пробник
    выбирает ветку окон (тикет 07). Такой черновик не «готов»."""
    fetch = fake_fetch({
        "dachi63.ru": load("bnovo_dachi63.html"),
        "rooms/index": load("bnovo_rooms_all_busy.html"),
    })
    res = scout.scout("https://dachi63.ru", fetch, today=TODAY, pages=("",),
                      unit_pairs=1)
    assert res["verdict"] == "low"
    assert "min_stay" in res["missing_params"]
    assert "минимальн" in res["note"]


# ---------------------------------------------------------------------------
# Волна 3: заслон опознаётся ОДНИМ предикатом, без своего потолка размера
# ---------------------------------------------------------------------------

def test_a_big_challenge_page_is_still_a_refusal():
    """Потолок размера тела (20 КБ) делал предикат разведки строго слабее
    общего: челлендж на 32 КБ читался как «модуля бронирования нет»."""
    from probes import _common

    body = ("<html><head><title>Just a moment...</title></head><body>"
            "<div id='cf-chl-widget'>Checking your browser</div>"
            + "<p>наполнитель</p>" * 2000 + "</body></html>")
    assert len(body) > 20000
    assert _common.is_challenge(body) is True
    assert scout.looks_like_antibot(200, body) is not None
    res = scout.scout("https://example-glamp.ru",
                      fake_fetch({"example-glamp.ru": body}),
                      today=TODAY, pages=("",))
    assert res["verdict"] == "refuse"
    assert res["access_refused"] is True
    assert "антибот" in res["refusal_reason"]


def test_google_recaptcha_include_is_not_a_challenge():
    """Google reCAPTCHA на странице — защита ЧУЖОЙ формы, а не заслон.

    Живая цена ошибки 09.09.2026: домен adygea-otdyh.ru припаркован в
    Timeweb, парковочная страница подключает recaptcha/api.js — и разведка
    отчиталась «антибот: страница похожа на челлендж (captcha)» вместо
    «домена больше нет». Объект уходил в ветку «нужен агент с браузером»
    вместо честного вывода. Российские заслоны ставят свои капчи, и они
    маркерами остаются.
    """
    from probes import _common

    parked = ('<html><head><title>Домен припаркован в Timeweb</title>'
              '<script src="https://www.google.com/recaptcha/api.js?'
              'onload=onloadcallback&render=explicit" async defer></script>'
              '</head><body>Домен припаркован</body></html>')
    assert _common.is_challenge(parked) is False
    assert scout.looks_like_antibot(200, parked) is None
    # SmartCaptcha Яндекса — именно заслон, маркером остаётся
    wall = ('<html><head><script src="//smartcaptcha.cloud.yandex.ru/'
            'captcha.js"></script></head><body>Вы не робот?</body></html>')
    assert _common.is_challenge(wall) is True


# ---------------------------------------------------------------------------
# Волна 3: заголовки черновика — как в живых рецептах
# ---------------------------------------------------------------------------

def test_bnovo_draft_carries_only_referer():
    """Все четыре живых рецепта bnovo в recipes.json (a_ureki, chekhovapi,
    wood_glamp, dacha_limerence) обходятся одним Referer; лишний Origin на
    GET за WAF — ровно тот класс отличий, который даёт 403 у одного объекта
    при зелёных тестах у всех."""
    assert scout._headers_for("bnovo", "https://dachi63.ru") == {
        "Referer": "https://dachi63.ru/"}
    assert scout._headers_for("litepms", "https://dachavsosnah.ru") == {
        "Referer": "https://dachavsosnah.ru/"}


def test_travelline_draft_keeps_origin():
    """Рецептов TravelLine в agent-runtime/.../recipes.json ровно 10, Origin
    несут 8 из них (без него — kuzminskoeglamp.ru и ok-reka.ru): WAF Qrator
    его ждёт (SKILL.md:84). Числа сверены с реестром 04.09 — прежние «10 из
    12» не сходились ни с чем."""
    assert scout._headers_for("travelline", "https://astroglamp.ru") == {
        "Referer": "https://astroglamp.ru/",
        "Origin": "https://astroglamp.ru"}


# ---------------------------------------------------------------------------
# Волна 3: 403 стороннего API — не «сайт нас не пустил»
# ---------------------------------------------------------------------------

def test_qrator_403_on_the_tl_api_is_not_a_site_refusal():
    """access_refused — про САЙТ объекта: по нему тикет 12 отправляет объект к
    агенту с браузером. 403 Qrator на ru-ibe.tlintegration.ru — известная
    ложная тревога, и объект с живым сайтом уезжал в «нас не пустили»."""
    fetch = fake_fetch({
        "prostory-village.ru": load("travelline_prostory.html"),
        "integration/profile": (403, "Forbidden"),
    })
    res = scout.scout("https://prostory-village.ru", fetch, today=TODAY,
                      pages=("",))
    assert res["verdict"] == "low"
    assert res["access_refused"] is False
    assert "403" in json.dumps(res["evidence"], ensure_ascii=False)


def test_refusal_by_the_object_site_still_sets_the_flag():
    fetch = fake_fetch({"example-glamp.ru": (403, "Forbidden")})
    res = scout.scout("https://example-glamp.ru", fetch, today=TODAY,
                      pages=("",))
    assert res["access_refused"] is True


# ---------------------------------------------------------------------------
# Волна 3: справочник bronirui — в каноническом виде рецепта (тикет 04)
# ---------------------------------------------------------------------------

def test_bronirui_numbers_are_written_in_the_canonical_form():
    """Пробник читает params.numbers как {id: {"name", "rooms_count"}}
    (probes/bronirui.directory, тикет 04). Плоское {id: имя} он тоже читает,
    но фонд из него не берётся — и починка счёта домиков в бою была no-op."""
    numbers = json.dumps({"numbers": [
        {"id": 15001, "name": "A-frame у озера", "rooms_count": 3},
        {"id": 15002, "name": "Купольный шатёр", "rooms_count": 1},
    ]}, ensure_ascii=False)
    fetch = fake_fetch({
        "lakevilleglamping.ru": load("bronirui_lakeville.html"),
        "api.bronirui-online.ru/v2/numbers": numbers,
    })
    res = scout.scout("https://lakevilleglamping.ru", fetch, today=TODAY,
                      pages=("",), allow_post=True)
    params = res["recipe_draft"]["request"]["params"]
    assert params["numbers"] == {
        "15001": {"name": "A-frame у озера", "rooms_count": 3},
        "15002": {"name": "Купольный шатёр", "rooms_count": 1}}
    # и пробник действительно видит фонд
    from probes.bronirui import directory
    _, capacity, unknown = directory(params["numbers"])
    assert capacity == {"15001": 3, "15002": 1}
    assert unknown == []
    # фонд назван в черновике ДОСЛОВНО: сверять с числом домиков на сайте
    # будет агент, читающий именно notes. Проверка на «"4" в notes» была
    # зелёной всегда — в них есть дата «2026-09-04» (ревью волны 3).
    assert ("позиций справочника 2, домиков по фонду (rooms_count) 4"
            in res["recipe_draft"]["notes"])


def test_bronirui_number_without_rooms_count_keeps_the_field_absent():
    """Поля нет — врать «фонд = 1» нельзя: пробник сам скажет «считано по
    номерам без фонда»."""
    assert scout.parse_bronirui_numbers({"numbers": [{"id": 7, "name": "Дом"}]}) \
        == {"7": {"name": "Дом"}}


def test_bronirui_numbers_union_keeps_the_biggest_fund():
    """Справочник собирается союзом пар дат: на ПЕРВОЙ дате движок отдал номер
    с фондом 3, на второй — тот же номер без фонда, и голый update терял
    множитель знаменателя (merge_numbers, тикет 04). Ответы на пары дат должны
    РАЗЛИЧАТЬСЯ, иначе тест зелёный и без союза (ревью волны 3)."""
    answers = [
        json.dumps({"numbers": [
            {"id": 15001, "name": "A-frame у озера", "rooms_count": 3},
            {"id": 15002, "name": "Купольный шатёр", "rooms_count": 1}]},
            ensure_ascii=False),
        json.dumps({"numbers": [
            {"id": 15001, "name": "A-frame у озера"}]}, ensure_ascii=False),
    ]
    served = []

    def fetch(url, headers, payload=None):
        if "lakevilleglamping.ru" in url:
            return 200, load("bronirui_lakeville.html")
        assert "api.bronirui-online.ru/v2/numbers" in url, url
        served.append((payload["date_from"], payload["date_to"]))
        return 200, answers[min(len(served) - 1, len(answers) - 1)]

    res = scout.scout("https://lakevilleglamping.ru", fetch, today=TODAY,
                      pages=("",), allow_post=True, unit_pairs=2)
    assert len(served) == 2 and served[0] != served[1], served
    numbers = res["recipe_draft"]["request"]["params"]["numbers"]
    # запись второй даты (без фонда) не затирает фонд первой
    assert numbers["15001"] == {"name": "A-frame у озера", "rooms_count": 3}
    # и номер, которого на второй дате не было вовсе, из справочника не исчез
    assert numbers["15002"]["rooms_count"] == 1


# ---------------------------------------------------------------------------
# Волна 3: живой fetch берёт ОБЩИЙ замок хоста (тикет 05)
# ---------------------------------------------------------------------------

def test_live_fetch_holds_the_shared_host_lock_during_the_request(monkeypatch):
    """Голого _wait_host_turn мало: он выдерживает паузу, но соседа в неё не
    пускает замок, а не расчёт (probes/_common.host_turn). Разведка ходит по
    31 незнакомому сайту — там это самое рискованное место."""
    import requests

    from probes import _common

    monkeypatch.setattr(_common, "_last_request_by_host", {})
    monkeypatch.setattr(_common, "_host_not_before", {})
    held = []

    class _LockWatchingSession(_FakeSession):
        def get(self, url, headers=None, timeout=None, stream=False):
            held.append(_common._lock_for("locked-glamp.ru").locked())
            return super().get(url, headers=headers, timeout=timeout,
                               stream=stream)

    session = _LockWatchingSession(_FakeResponse(body=b"<html></html>"))
    monkeypatch.setattr(requests, "Session", lambda: session)
    fetch = scout.make_fetch()
    fetch("https://locked-glamp.ru/", {})
    assert held == [True], "запрос ушёл, не заняв очередь к хосту"


# ---------------------------------------------------------------------------
# Волна 4: отсев доходит до рецепта, границы вызова виджета, заслон
# ---------------------------------------------------------------------------

def test_homereserve_excluded_units_leave_the_white_list():
    """Пробник снимает ровно то, что перечислено в params.apartment_ids
    (probes/homereserve.py:218 — `wanted = params.get("apartment_ids")`).
    Отсеянный сертификат, оставшийся в этом списке, попадает в знаменатель
    занятости объекта — то есть отсев виден в черновике и не работает в бою."""
    apartments = json.dumps({"apartments": [
        {"id": 71049, "title": "Дом у воды", "min_stay": 2},
        {"id": 89311, "title": "Подарочный сертификат", "min_stay": 1},
    ]}, ensure_ascii=False)
    fetch = fake_fetch({
        "baninaozerah.ru": load("homereserve_baninaozerah.html"),
        "realtycalendar.ru": apartments,
    })
    res = scout.scout("https://baninaozerah.ru", fetch, today=TODAY,
                      pages=("",), allow_post=True)
    params = res["recipe_draft"]["request"]["params"]
    assert params["apartment_ids"] == [71049]
    assert res["units_found"] == 1
    assert res["units_excluded"] == 1
    # черновик больше не спорит сам с собой: сколько домиков назвали, столько
    # и в белом списке рецепта
    assert len(params["apartment_ids"]) == res["units_found"]


def test_homereserve_token_after_the_widget_call_is_not_taken():
    """Зеркало дефекта волны 1: у сайта на Tilda форма обратной связи с ключом
    token стоит НИЖЕ виджета столько же раз, сколько выше, а класс [\\s\\S]
    снял границу объекта вызова."""
    page = ('<script>window.homereserve.initWidgetSearch({"apartments":'
            '[71049,89311],"guests":{"adults":2,"children":[]},'
            '"token":"Sh0gLTkBgP","lang":"ru"});</script>'
            '<script>window.tildaForm={"token":"WRONGTOKEN1"};</script>')
    assert scout.homereserve_token(page) == "Sh0gLTkBgP"
    # и тот же случай без токена внутри вызова: чужой ключ снаружи в рецепт
    # не идёт вовсе — «нет токена» дешевле ложного
    lonely = ('<script>window.homereserve.initWidgetSearch({"apartments":'
              '[71049]});</script>'
              '<script>window.tildaForm={"token":"WRONGTOKEN1"};</script>')
    assert scout.homereserve_token(lonely) is None


def test_bronirui_module_id_after_the_widget_call_is_not_taken():
    """Тот же класс у «Бронируй Онлайн»: счётчик с полем moduleId ниже вызова
    виджета так же обычен, как выше."""
    page = ("<script>znmsWidget.init('#w', {widget_type:'booking-rooms',"
            "moduleId: 4242});</script>"
            "<script>var analytics={moduleId: 99};</script>")
    res = scout.scout("https://znms-glamp.ru",
                      fake_fetch({"znms-glamp.ru": page}),
                      today=TODAY, pages=("",))
    assert res["recipe_draft"]["request"]["params"]["module_id"] == 4242

    foreign = ("<script>znmsWidget.init('#w', "
               "{widget_type:'booking-rooms'});</script>"
               "<script>var analytics={moduleId: 99};</script>")
    res = scout.scout("https://znms-glamp.ru",
                      fake_fetch({"znms-glamp.ru": foreign}),
                      today=TODAY, pages=("",))
    assert res["recipe_draft"]["request"]["params"].get("module_id") is None


def test_recaptcha_tag_on_a_live_page_is_not_a_challenge():
    """Штатный тег reCAPTCHA у формы обратной связи стоит в первых 4096
    знаках доброй половины лендингов, а маркер «captcha» общего предиката
    делает из него антибот. Страница-заслон виджета объекта не несёт никогда —
    сильный маркер движка и есть доказательство, что нам отдали САЙТ."""
    page = ("<head><script src='https://www.google.com/recaptcha/api.js"
            "?render=6Lc'></script></head>" + load("bnovo_dachi63.html"))
    assert scout.looks_like_antibot(200, page) is None
    res = scout.scout("https://dachi63.ru",
                      fake_fetch({"dachi63.ru": page,
                                  "reservationsteps.ru/rooms/index":
                                      ROOMS_INDEX}),
                      today=TODAY, pages=("",))
    assert res["verdict"] != "refuse"
    assert res["access_refused"] is False
    assert res["recipe_draft"]["request"]["params"].get("uid")


def test_real_challenge_is_still_a_refusal():
    """Сужение не должно ослабить предикат: у настоящего заслона виджета
    объекта на странице нет, и он по-прежнему даёт refuse."""
    assert scout.looks_like_antibot(200, load("antibot_challenge.html"))
    assert scout.looks_like_antibot(200, load("cloudflare_challenge_200.html"))


def test_service_words_are_not_cancelled_by_a_dwelling_word():
    """«Подарочный сертификат на домик» — сертификат, а не домик: слова
    сертификат/подарочн/депозит/трансфер ложного отсева жилья не дают по
    построению (на 189 живых именах из снапшотов ни одного вхождения)."""
    for name in ("Подарочный сертификат на домик",
                 "Сертификат на проживание в доме",
                 "Депозит за дом",
                 "Ранний заезд в домик",
                 "Трансфер до глэмпинга"):
        assert scout.unit_exclusion_reason(name), name
    # слабые слова признак жилья по-прежнему отменяет: «Купольный дом с
    # парковкой» — домик (ревью волны 2)
    for name in ("Купольный дом с парковкой",
                 "Шатёр у стоянки",
                 "Место для палатки у дома"):
        assert scout.unit_exclusion_reason(name) is None, name


def test_call_arguments_stops_at_the_closing_bracket():
    """Границу вызова держат скобки, а не длина окна; кавычки учитываются —
    иначе скобка в имени домика («Дом «Победа» (2)») сбивает счёт."""
    opener = scout.re.compile(r"init\s*\(")
    assert scout.call_arguments(
        'init({"a":{"b":1},"name":"Дом (2)"});var x={"a":9}', opener
    ) == '{"a":{"b":1},"name":"Дом (2)"}'
    # незакрытый вызов — не «весь остаток страницы», а честное None
    assert scout.call_arguments('init({"a":1', opener) is None
    assert scout.call_arguments('нет вызова вовсе', opener) is None


def test_homereserve_get_branch_says_the_white_list_is_unfiltered():
    """Без --allow-post имён позиций мы не видели: молчать о том, что в белый
    список рецепта попали все id вызова, нельзя — это знаменатель занятости."""
    res = scout.scout("https://baninaozerah.ru",
                      fake_fetch({"baninaozerah.ru":
                                  load("homereserve_baninaozerah.html")}),
                      today=TODAY, pages=("",))
    assert "нежилые позиции не отсеяны" in res["recipe_draft"]["notes"]


def test_challenge_from_the_engine_api_is_still_a_refusal():
    """Отмена вердикта заслона живёт только на страницах САМОГО САЙТА: заслон
    Cloudflare называет хост, на который нас не пустили, а хост движка — это и
    есть его сильный маркер, и отмена на стороннем API превратила бы честное
    «нас не пустили» в «ответ не той формы»."""
    challenge = ("<html><head><title>Just a moment...</title></head><body>"
                 "Checking your browser before accessing realtycalendar.ru"
                 "</body></html>")
    assert scout.looks_like_antibot(200, challenge, False) is not None
    fetch = fake_fetch({
        "baninaozerah.ru": load("homereserve_baninaozerah.html"),
        "realtycalendar.ru": challenge,
    })
    res = scout.scout("https://baninaozerah.ru", fetch, today=TODAY,
                      pages=("",), allow_post=True)
    assert "антибот" in res["note"], res["note"]
    # отказ СТОРОННЕГО хоста сайт объекта не пятнает (ревью волны 2)
    assert res["access_refused"] is False


# ---------------------------------------------------------------------------
# Волна 5 (ревью wave4-core, пункт 3): разведка знает о живом съёме
# ---------------------------------------------------------------------------
#
# Замок «живой съём один на машине» (core.host_lock_path) брал только прогон
# пробников, а разведчик ходил на ТЕ ЖЕ чужие хосты своим процессом мимо него.
# 04.09 это уже случилось: разведка 54 сайтов шла параллельно с прогонами
# агентов, то есть пауза >= 1.2 с к хосту не соблюдалась ни разу.

def test_live_scout_takes_the_machine_lock(tmp_path, monkeypatch):
    import occupancy_core as core

    monkeypatch.setenv(core.HOST_LOCK_ENV, str(tmp_path / ".hosts.lock"))
    with scout.host_lock() as taken:
        assert taken is True
        with pytest.raises(core.RunLockError):
            with core.run_lock(core.host_lock_path()):
                pass
    with core.run_lock(core.host_lock_path()):   # отпущен после выхода
        pass


def test_scout_refuses_while_a_live_run_holds_the_machine(tmp_path,
                                                          monkeypatch, capsys):
    import occupancy_core as core

    monkeypatch.setenv(core.HOST_LOCK_ENV, str(tmp_path / ".hosts.lock"))

    def never(*a, **kw):
        raise AssertionError("разведка пошла в сеть при живом прогоне")

    monkeypatch.setattr(scout, "make_fetch", lambda *a, **kw: never)
    with core.run_lock(core.host_lock_path()):
        code = scout.main(["--url", "https://dachi63.ru",
                           "--out-dir", str(tmp_path / "scout-out"),
                           "--today", TODAY.isoformat()])

    assert code == 2
    err = capsys.readouterr().err
    assert "живой съём" in err and "--ignore-run-lock" in err
    assert not (tmp_path / "scout-out").exists()


def test_scout_can_be_told_to_go_anyway_and_says_so(tmp_path, monkeypatch,
                                                    capsys):
    """Отказ не должен запирать агента на полтора часа планового прогона:
    флаг есть, но он громкий — молча мимо замка разведка не ходит."""
    import occupancy_core as core

    monkeypatch.setenv(core.HOST_LOCK_ENV, str(tmp_path / ".hosts.lock"))
    with core.run_lock(core.host_lock_path()):
        with scout.host_lock(ignore=True) as taken:
            assert taken is False
    err = capsys.readouterr().err
    assert "предупреждение" in err and "живой съём" in err


def test_scout_on_saved_pages_does_not_need_the_lock(tmp_path, monkeypatch):
    """Разбор уже снятого HTML (тесты, повтор по сохранённой странице) в сеть
    не ходит — запирать его живым прогоном не за что."""
    import occupancy_core as core

    monkeypatch.setenv(core.HOST_LOCK_ENV, str(tmp_path / ".hosts.lock"))
    fetch = fake_fetch({"dachi63.ru": load("bnovo_dachi63.html"),
                        "reservationsteps.ru/rooms/index": ROOMS_INDEX})
    with core.run_lock(core.host_lock_path()):
        code = scout.main(["--url", "https://dachi63.ru",
                           "--out-dir", str(tmp_path / "scout-out"),
                           "--pages", "", "--today", TODAY.isoformat()],
                          fetch=fetch)
    assert code == 0
    assert (tmp_path / "scout-out" / "smr_dachi63.json").is_file()


# ---------------------------------------------------------------------------
# 05.09: заголовки черновика для кириллического сайта — у КАЖДОГО движка
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("engine", sorted(set(scout._HEADERS_BY_ENGINE)
                                          | set(scout._REQUEST_TEMPLATES)))
def test_draft_headers_for_an_idn_site_are_wire_safe_for_every_engine(engine):
    """Живой прогон 05.09 00:00 уронил smr_mesto_schastya (местосчастья.рф)
    UnicodeEncodeError'ом в заголовке: http.client кодирует их latin-1.
    Тест 04.09 проверял это на одном litepms — здесь каждый движок: ни одно
    значение заголовка не должно нести кириллицу, а хост сайта в них — только
    punycode."""
    headers = scout._headers_for(engine, "https://местосчастья.рф")
    for value in headers.values():
        value.encode("latin-1")  # ровно здесь падал живой прогон
        assert "местосчастья" not in value
    site_bound = [v for v in headers.values() if "xn--80ajujobbee1c4cub" in v]
    if engine != "uhotels":  # его заголовки указывают на api.uhotels.app
        assert site_bound, headers


def test_cyrillic_domain_refusal_is_still_the_sites_own_refusal():
    """Ревью 14.09.2026. Хост сайта хранился в punycode, а страницы строились из
    кириллического адреса: у смоларелакс.рф и ещё пяти целей _own_host давал False,
    и отказ самого сайта не взводил access_refused (объект не уходил к агенту с
    браузером), а отмена ложного «антибота» сильным маркером движка не работала."""
    fetch = fake_fetch({"смоларелакс.рф": (403, "Forbidden")})
    res = scout.scout("https://смоларелакс.рф", fetch, today=TODAY)
    assert res["verdict"] == "refuse"
    assert res["access_refused"] is True
