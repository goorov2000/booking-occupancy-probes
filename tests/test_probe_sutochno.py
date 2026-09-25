# -*- coding: utf-8 -*-
"""Пробник Суточно.ру: живые фикстуры, типы блоков, ключ приложения, каскад.

Метод — GET sutochno.ru/api/json/orders/getOrdersByObject?object_id=<id> с
заголовками api-version/platform/token (разведка хаусботов 21.08.2026,
перепроверка 08.09.2026). Ответ — блоки календаря объявления с типами
cs-book/cs-appr/cs-import/cs-navail; все = «канал не даёт забронировать».

Фикстуры — живые ответы sutochno.ru, снятые 08.09.2026 (тела как пришли,
ключ приложения в них не фигурирует):
- getOrdersByObject_2224174_tsarevschina.json — «Плавдом на озере»
  (Царевщина, Самара): пять блоков cs-import, сентябрь 11-12, 14-17, 19 и
  октябрь 16-17 (плюс прошедшая ночь 07.09);
- getOrdersByObject_2026009_uglich.json — «Дом на воде для двоих» (Углич):
  один блок cs-appr 19.10.2026 -> 30.04.2027 — сезонное закрытие;
- getOrdersByObject_2193366_gora_hb40.json — «Хаусбот 40» отеля «Гора»:
  cs-navail 10-13.09, 18-21.09, 26.09 и 30.12.2026 -> 06.09.2027;
- getOrdersByObject_1746701_sparohod.json — «Спароход» (Калининград):
  календарь пуст;
- auth_failed_403.json — HTTP 403 на чужой/протухший ключ приложения
  (actions.need_update_token=true);
- asset_index_apiToken_excerpt.js, nuxt_entry_tokens_excerpt.js,
  main_page_entry_tag.html — где в бандле лежит ключ (legacy и Nuxt).
Движок в диспетчер здесь подставляется фикстурой: регистрацию в
probes/__init__.py делает оркестратор, а тесты обязаны быть зелёными и до
неё, и после.
"""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import occupancy_core as core
import probes
from probes import _common, sutochno

FIXTURES = Path(__file__).parent / "fixtures" / "sutochno"
TODAY = date(2026, 9, 8)
TOKEN = sutochno.DEFAULT_TOKEN
NEW_TOKEN = "NewAppKeyFromBundle0001/Ab=="
API = sutochno.API_URL
LEGACY = sutochno.TOKEN_SOURCE_LEGACY
MAIN = sutochno.TOKEN_SOURCE_NUXT
ENTRY = "https://cdn.sutochno.ru/static-pages/_nuxt/B03gNPFx.js"

CHALLENGE_PAGE = (
    "<!DOCTYPE html><html><head><title>Just a moment...</title></head>"
    "<body><div class=\"cf-browser-verification\">Checking your browser"
    "</div></body></html>")


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


SUTOCHNO_RECIPE = {
    "site": "https://drozdi63.ru/",
    "engine": "sutochno",
    "status": "ok",
    "request": {
        "url_template": API,
        "method": "GET",
        "params": {
            "objects": {"2224174": {"name": "Плавдом на озере",
                                    "rooms_count": 1}},
            "token": TOKEN,
        },
        "headers": {"Referer": "https://samara.sutochno.ru/2224174"},
        "date_substitution": "дат в запросе нет: один GET на object_id, "
                             "ответ — все будущие блоки календаря",
    },
    "discovered_at": "2026-09-08T00:40:00+03:00",
    "notes": "",
    "source_urls": [],
}


@pytest.fixture(autouse=True)
def _engine_cache_and_today(monkeypatch):
    """Движок в диспетчере, пустой кэш ключа, «сегодня» = день фикстур."""
    monkeypatch.setitem(probes.ENGINES, "sutochno", sutochno)
    monkeypatch.setattr(sutochno, "_token_cache", {})
    monkeypatch.setattr(core, "today", lambda: TODAY)


def fake_fetch(responses, calls=None):
    """Фейковый JSON-fetch: object_id из url -> ответ.

    Ответ: payload | (status, payload) | исключение | callable(token) -> то же
    (для сценариев с протухшим ключом). calls копит (object_id, headers).
    """
    def fetch(url, headers, payload=None):
        object_id = url.rsplit("object_id=", 1)[-1]
        if calls is not None:
            calls.append((object_id, dict(headers)))
        if object_id not in responses:
            raise AssertionError(f"неожиданный object_id: {object_id}")
        answer = responses[object_id]
        if callable(answer):
            answer = answer(headers.get("token"))
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, tuple) else (200, answer)
    fetch.last_body_text = ""
    return fetch


def fake_text(pages, calls=None):
    """Фейковый текстовый fetch: url -> (status, текст) | исключение."""
    def text_fetch(url, headers):
        if calls is not None:
            calls.append(url)
        if url not in pages:
            raise AssertionError(f"неожиданный текстовый url: {url}")
        answer = pages[url]
        if isinstance(answer, Exception):
            raise answer
        return answer
    return text_fetch


def recipe_with(objects=None, **params):
    recipe = json.loads(json.dumps(SUTOCHNO_RECIPE))
    if objects is not None:
        recipe["request"]["params"]["objects"] = objects
    recipe["request"]["params"].update(params)
    return recipe


def blocks_payload(*blocks):
    """Мини-ответ движка: [(begin, end, type), ...] -> data.calendars."""
    return {"success": True, "data": {"calendars": [
        {"date_begin": f"{b} 14:00:00", "date_end": f"{e} 12:00:00",
         "type": t, "id": i, "object_id": 1}
        for i, (b, e, t) in enumerate(blocks, 1)]},
        "errors": [], "actions": []}


def nights(cells, state, prefix=""):
    return sorted(d for d, c in cells.items()
                  if c["state"] == state and d.startswith(prefix))


# ---------------------------------------------------------------------------
# Живые фикстуры: сетка по объявлениям через диспетчер
# ---------------------------------------------------------------------------

def test_run_recipe_dispatches_sutochno_and_builds_quota_grid():
    calls = []
    fetch = fake_fetch(
        {"2224174": load("getOrdersByObject_2224174_tsarevschina.json")},
        calls)
    obj, broken = probes.run_recipe("smr_plavdom_na_ozere", SUTOCHNO_RECIPE,
                                    TODAY, date(2026, 10, 31), fetch=fetch)
    assert broken is None
    assert obj["status"] == "ok"
    assert obj["engine"] == "sutochno"
    assert obj["source_kind"] == "aggregator_quota"   # квота, не загрузка
    assert obj["granularity"] == "per_unit"
    cells = obj["units"]["Плавдом на озере"]
    assert nights(cells, "busy", "2026-09") == [
        "2026-09-11", "2026-09-12", "2026-09-14", "2026-09-15",
        "2026-09-16", "2026-09-17", "2026-09-19"]
    assert nights(cells, "busy", "2026-10") == ["2026-10-16", "2026-10-17"]
    assert cells["2026-09-11"] == {"state": "busy", "block": "cs-import",
                                   "units_total": 1, "units_free": 0}
    assert cells["2026-09-13"] == {"state": "free", "units_total": 1,
                                   "units_free": 1}
    assert "cs-import×9" in obj["reason"] and "оценка сверху" in obj["reason"]
    assert obj["source_urls"] == [API.replace("{object_id}", "2224174")]
    # заголовки метода: api-version, platform и ключ приложения + Referer рецепта
    _, headers = calls[0]
    assert headers["api-version"] == "1.8" and headers["platform"] == "js"
    assert headers["token"] == TOKEN
    assert headers["Referer"] == "https://samara.sutochno.ru/2224174"
    core.validate_object(obj)


def test_night_semantics_checkin_to_checkout_is_one_night():
    """Блок «07.09 14:00 -> 08.09 12:00» — ровно ночь 07.09, не две."""
    fetch = fake_fetch({"2224174": blocks_payload(
        ("2026-09-07", "2026-09-08", "cs-book"))})
    obj, _ = probes.run_recipe("x", SUTOCHNO_RECIPE, date(2026, 9, 7),
                               date(2026, 9, 9), fetch=fetch)
    cells = obj["units"]["Плавдом на озере"]
    assert cells["2026-09-07"]["state"] == "busy"
    assert cells["2026-09-08"]["state"] == "free"
    assert cells["2026-09-09"]["state"] == "free"


def test_grid_is_deep_for_free_and_names_its_horizon():
    """Глубина бесплатна (дат в запросе нет): сетка до горизонта ядра, и
    границы grid_until/inventory_until названы (контракт волны 3)."""
    fetch = fake_fetch({"2224174": blocks_payload()})
    obj, _ = probes.run_recipe("x", SUTOCHNO_RECIPE, TODAY,
                               date(2026, 10, 31), fetch=fetch)
    deep_end = core.grid_horizon().isoformat()
    assert obj["grid_until"] == deep_end
    assert obj["inventory_until"] == deep_end
    cells = obj["units"]["Плавдом на озере"]
    assert max(cells) == deep_end
    assert (date.fromisoformat(deep_end) + timedelta(days=1)).isoformat() \
        not in cells


# ---------------------------------------------------------------------------
# Типы блоков: все busy с составом; длинный блок не-cs-book — закрытые продажи
# ---------------------------------------------------------------------------

def test_uglich_long_cs_appr_block_is_closed_sales_not_occupancy():
    """Живой Углич: cs-appr 19.10.2026 -> 30.04.2027 — сезонное закрытие.

    100% занятости с октября по апрель — выдуманный пик сезона; правило
    «стены» диспетчера ловит только хвост горизонта, а этот блок стоит
    посреди него (после 30.04 объявление снова свободно)."""
    recipe = recipe_with({"2026009": {"name": "Дом на воде", "rooms_count": 1}})
    fetch = fake_fetch({"2026009": load("getOrdersByObject_2026009_uglich.json")})
    obj, broken = probes.run_recipe("uglich", recipe, TODAY,
                                    date(2027, 5, 31), fetch=fetch)
    assert broken is None and obj["status"] == "ok"
    cells = obj["units"]["Дом на воде"]
    closed = nights(cells, "sales_not_open")
    assert closed[0] == "2026-10-19" and closed[-1] == "2027-04-29"
    assert len(closed) == 193
    assert nights(cells, "busy") == []
    assert cells["2026-10-18"]["state"] == "free"
    assert cells["2027-04-30"]["state"] == "free"
    assert cells["2026-12-01"] == {"state": "sales_not_open",
                                   "block": "cs-appr", "rule": "long_block",
                                   "units_total": 1, "units_free": 0}
    assert "закрытые продажи" in obj["reason"]
    assert "cs-appr 2026-10-19..2027-04-29 (193 ноч.)" in obj["reason"]
    # в знаменатель занятости закрытые ночи не идут
    nov = core.aggregate(obj["units"], ["2026-11"])[0]
    assert nov["cuts"]["all"]["known"] == 0 and nov["sales_not_open"] == 30
    core.validate_object(obj)


def test_gora_cs_navail_short_blocks_busy_long_block_closed():
    """Живой «Хаусбот 40» Горы: короткие cs-navail — занятость (закрыто
    владельцем и бронь неразличимы, оценка сверху), блок 30.12 -> 06.09.2027
    (250 ночей) — закрытые продажи; состав назван в reason."""
    recipe = recipe_with({"2193366": {"name": "Хаусбот 40", "rooms_count": 1}})
    fetch = fake_fetch({"2193366": load("getOrdersByObject_2193366_gora_hb40.json")})
    obj, broken = probes.run_recipe("gora", recipe, TODAY,
                                    date(2026, 12, 31), fetch=fetch)
    assert broken is None and obj["status"] == "ok"
    cells = obj["units"]["Хаусбот 40"]
    assert nights(cells, "busy") == [
        "2026-09-10", "2026-09-11", "2026-09-12", "2026-09-13",
        "2026-09-18", "2026-09-19", "2026-09-20", "2026-09-21", "2026-09-26"]
    assert cells["2026-09-10"]["block"] == "cs-navail"
    closed = nights(cells, "sales_not_open")
    assert closed[0] == "2026-12-30" and closed[-1] == "2027-09-05"
    assert len(closed) == 250
    assert "Хаусбот 40: cs-navail×9" in obj["reason"]
    # диспетчер не переписывает то, что уже размечено закрытыми продажами
    assert (obj.get("sales_window") or {}).get("rule") != "no_free_cells"


def test_long_block_threshold_comes_from_recipe():
    """params.long_block_nights: 0 выключает правило, порог выше длины блока
    оставляет его занятостью."""
    fixture = load("getOrdersByObject_2026009_uglich.json")
    for threshold in (0, 200):
        recipe = recipe_with({"2026009": "Дом на воде"},
                             long_block_nights=threshold)
        obj, _ = probes.run_recipe("uglich", recipe, TODAY,
                                   date(2027, 5, 31),
                                   fetch=fake_fetch({"2026009": fixture}))
        cells = obj["units"]["Дом на воде"]
        assert len(nights(cells, "busy")) == 193, threshold
        assert nights(cells, "sales_not_open") == []


def test_cs_book_of_any_length_stays_a_booking():
    """Длинный cs-book — запись самой площадки о заказе, не закрытие."""
    fetch = fake_fetch({"2224174": blocks_payload(
        ("2026-10-01", "2026-11-15", "cs-book"))})
    obj, _ = probes.run_recipe("x", SUTOCHNO_RECIPE, TODAY,
                               date(2026, 11, 30), fetch=fetch)
    cells = obj["units"]["Плавдом на озере"]
    assert len(nights(cells, "busy")) == 45
    assert nights(cells, "sales_not_open") == []


def test_unknown_block_type_is_unknown_and_named():
    fetch = fake_fetch({"2224174": blocks_payload(
        ("2026-09-10", "2026-09-12", "cs-xyz"))})
    obj, _ = probes.run_recipe("x", SUTOCHNO_RECIPE, TODAY,
                               date(2026, 9, 30), fetch=fetch)
    cells = obj["units"]["Плавдом на озере"]
    assert nights(cells, "unknown") == ["2026-09-10", "2026-09-11"]
    assert "units_free" not in cells["2026-09-10"]
    assert cells["2026-09-10"]["block"] == "cs-xyz"
    assert "незнакомый тип блока cs-xyz" in obj["reason"]


def test_booking_inside_a_long_closure_wins_as_busy():
    fetch = fake_fetch({"2224174": blocks_payload(
        ("2026-10-01", "2026-12-01", "cs-navail"),
        ("2026-10-10", "2026-10-12", "cs-book"))})
    obj, _ = probes.run_recipe("x", SUTOCHNO_RECIPE, TODAY,
                               date(2026, 12, 31), fetch=fetch)
    cells = obj["units"]["Плавдом на озере"]
    assert cells["2026-10-10"]["state"] == "busy"
    assert cells["2026-10-10"]["block"] == "cs-book"
    assert cells["2026-10-09"]["state"] == "sales_not_open"
    assert cells["2026-10-12"]["state"] == "sales_not_open"


def test_zero_length_block_blocks_nothing():
    fetch = fake_fetch({"2224174": blocks_payload(
        ("2026-09-10", "2026-09-10", "cs-book"))})
    obj, _ = probes.run_recipe("x", SUTOCHNO_RECIPE, TODAY,
                               date(2026, 9, 30), fetch=fetch)
    assert nights(obj["units"]["Плавдом на озере"], "busy") == []


# ---------------------------------------------------------------------------
# Пустой календарь: свободно везде ИЛИ объявления нет — проверяем страницу
# ---------------------------------------------------------------------------

SPAROHOD = {"1746701": {"name": "Спароход", "rooms_count": 1}}
LISTING = sutochno.LISTING_URL.format(object_id="1746701")


def test_empty_calendar_with_live_listing_is_free_with_caveat():
    calls = []
    obj, broken = sutochno.probe(
        "sparohod", recipe_with(SPAROHOD), TODAY, date(2026, 10, 31),
        fetch=fake_fetch({"1746701": load("getOrdersByObject_1746701_sparohod.json")}),
        text_fetch=fake_text({LISTING: (200, "<title>Отдельный дом, "
                                             "объявление 1746701</title>")},
                             calls))
    assert broken is None and obj["status"] == "ok"
    cells = obj["units"]["Спароход"]
    assert nights(cells, "free") == sorted(cells)
    assert calls == [LISTING]
    assert LISTING in obj["source_urls"]
    assert "календарь пуст (объявление живо)" in obj["reason"]
    assert "не ведёт календарь площадки" in obj["reason"]


def test_empty_calendar_with_gone_listing_is_unknown_and_insufficient():
    """Несуществующий object_id API отвечает так же, как свободный (живая
    проверка 08.09, id 999999999) — 404 страницы объявления это «объекта
    нет», а не «свободно везде»."""
    obj, broken = sutochno.probe(
        "sparohod", recipe_with(SPAROHOD), TODAY, date(2026, 10, 31),
        fetch=fake_fetch({"1746701": blocks_payload()}),
        text_fetch=fake_text({LISTING: (404, "Страница не найдена")}))
    assert broken is None                    # рецепт жив: объявление вернётся
    assert obj["status"] == "insufficient_data"
    assert "объявление не найдено" in obj["reason"] and "404" in obj["reason"]
    cells = obj["units"]["Спароход"]
    assert nights(cells, "unknown") == sorted(cells)


def test_empty_calendar_listing_page_without_the_id_is_not_proof():
    """200 чужой страницы (редирект на город) за живое объявление не сходит."""
    obj, _ = sutochno.probe(
        "sparohod", recipe_with(SPAROHOD), TODAY, date(2026, 9, 30),
        fetch=fake_fetch({"1746701": blocks_payload()}),
        text_fetch=fake_text({LISTING: (200, "<title>Калининград</title>")}))
    assert obj["status"] == "ok"
    assert "без номера объявления" in obj["reason"]
    assert "объявление живо" not in obj["reason"]


def test_empty_calendar_without_text_transport_says_unchecked():
    """Через диспетчер текстового транспорта нет (тесты): free с пометкой."""
    obj, broken = probes.run_recipe(
        "sparohod", recipe_with(SPAROHOD), TODAY, date(2026, 9, 30),
        fetch=fake_fetch({"1746701": blocks_payload()}))
    assert broken is None and obj["status"] == "ok"
    assert "не проверялось" in obj["reason"]


def test_empty_calendar_listing_network_failure_keeps_free_with_note():
    obj, _ = sutochno.probe(
        "sparohod", recipe_with(SPAROHOD), TODAY, date(2026, 9, 30),
        fetch=fake_fetch({"1746701": blocks_payload()}),
        text_fetch=fake_text({LISTING: ConnectionError("обрыв")}))
    assert obj["status"] == "ok"
    assert "сетевой сбой" in obj["reason"]


def test_listing_check_is_not_made_when_calendar_has_blocks():
    calls = []
    sutochno.probe(
        "x", SUTOCHNO_RECIPE, TODAY, date(2026, 10, 31),
        fetch=fake_fetch({"2224174": load("getOrdersByObject_2224174_tsarevschina.json")}),
        text_fetch=fake_text({}, calls))
    assert calls == []


# ---------------------------------------------------------------------------
# Ключ приложения: протух -> из бандла, один раз, с повтором того же объекта
# ---------------------------------------------------------------------------

def stale_or_live(payload):
    """Ответ движка по ключу: старый -> 403 need_update_token, новый -> данные."""
    def answer(token):
        if token == NEW_TOKEN:
            return payload
        return 403, load("auth_failed_403.json")
    return answer


def test_extract_token_from_both_bundle_shapes():
    assert sutochno.extract_token(load_text("asset_index_apiToken_excerpt.js")) \
        == TOKEN
    assert sutochno.extract_token(load_text("nuxt_entry_tokens_excerpt.js")) \
        == TOKEN
    assert sutochno.extract_token("default:\"short\"") is None
    assert sutochno.extract_token("") is None


def test_stale_token_is_recognised_by_need_update_token():
    assert sutochno.stale_token(403, load("auth_failed_403.json"))
    assert sutochno.stale_token(403, {"errors": ["Application authentication "
                                                 "failed"]})
    assert not sutochno.stale_token(403, {"message": "forbidden"})
    assert not sutochno.stale_token(200, load("auth_failed_403.json"))
    assert not sutochno.stale_token(403, None)


def test_stale_token_is_refreshed_from_legacy_bundle_and_object_retried():
    calls, pages = [], []
    live = load("getOrdersByObject_2224174_tsarevschina.json")
    legacy = load_text("asset_index_apiToken_excerpt.js").replace(TOKEN, NEW_TOKEN)
    obj, broken = sutochno.probe(
        "x", SUTOCHNO_RECIPE, TODAY, date(2026, 10, 31),
        fetch=fake_fetch({"2224174": stale_or_live(live)}, calls),
        text_fetch=fake_text({LEGACY: (200, legacy)}, pages))
    assert broken is None and obj["status"] == "ok"
    # один повтор того же объекта, уже с новым ключом
    assert [(o, h["token"]) for o, h in calls] == [("2224174", TOKEN),
                                                   ("2224174", NEW_TOKEN)]
    assert pages == [LEGACY]
    assert obj["token_refresh"]["token"] == NEW_TOKEN
    assert obj["token_refresh"]["source"] == LEGACY
    assert obj["token_refresh"]["stale_from"] == "recipe"
    assert "обновите params.token" in obj["reason"]
    assert LEGACY in obj["source_urls"]
    assert len(nights(obj["units"]["Плавдом на озере"], "busy")) == 9


def test_refreshed_token_is_shared_by_the_next_target_without_a_second_trip():
    live = load("getOrdersByObject_2224174_tsarevschina.json")
    legacy = load_text("asset_index_apiToken_excerpt.js").replace(TOKEN, NEW_TOKEN)
    sutochno.probe("first", SUTOCHNO_RECIPE, TODAY, date(2026, 9, 30),
                   fetch=fake_fetch({"2224174": stale_or_live(live)}),
                   text_fetch=fake_text({LEGACY: (200, legacy)}))
    calls, pages = [], []
    obj, _ = sutochno.probe(
        "second", SUTOCHNO_RECIPE, TODAY, date(2026, 9, 30),
        fetch=fake_fetch({"2224174": stale_or_live(live)}, calls),
        text_fetch=fake_text({}, pages))     # в бандл больше не ходим
    assert obj["status"] == "ok"
    assert [h["token"] for _, h in calls] == [NEW_TOKEN]
    assert pages == []
    assert "token_refresh" not in obj


def test_stale_token_falls_back_to_the_nuxt_chain():
    """legacy-скрипт не отдал — главная -> entry-чанк -> карта ключей."""
    live = load("getOrdersByObject_2224174_tsarevschina.json")
    chunk = load_text("nuxt_entry_tokens_excerpt.js").replace(TOKEN, NEW_TOKEN)
    pages = []
    obj, broken = sutochno.probe(
        "x", SUTOCHNO_RECIPE, TODAY, date(2026, 9, 30),
        fetch=fake_fetch({"2224174": stale_or_live(live)}),
        text_fetch=fake_text({LEGACY: (404, ""),
                              MAIN: (200, load_text("main_page_entry_tag.html")),
                              ENTRY: (200, chunk)}, pages))
    assert broken is None and obj["status"] == "ok"
    assert pages == [LEGACY, MAIN, ENTRY]
    assert obj["token_refresh"]["source"] == ENTRY
    assert ENTRY in obj["source_urls"]


def test_bundle_without_a_token_marks_the_recipe_broken():
    live = load("getOrdersByObject_2224174_tsarevschina.json")
    obj, broken = sutochno.probe(
        "x", SUTOCHNO_RECIPE, TODAY, date(2026, 9, 30),
        fetch=fake_fetch({"2224174": stale_or_live(live)}),
        text_fetch=fake_text({LEGACY: (200, "var x = 1;"),
                              MAIN: (200, "<html><body>no tags</body></html>")}))
    assert broken is not None
    assert "ключа нет" in broken and "переразведка" in broken
    assert obj["status"] == "insufficient_data"
    assert sutochno._token_cache == {}


def test_bundle_network_failure_keeps_the_recipe_alive():
    live = load("getOrdersByObject_2224174_tsarevschina.json")
    obj, broken = sutochno.probe(
        "x", SUTOCHNO_RECIPE, TODAY, date(2026, 9, 30),
        fetch=fake_fetch({"2224174": stale_or_live(live)}),
        text_fetch=fake_text({LEGACY: ConnectionError("таймаут"),
                              MAIN: ConnectionError("таймаут")}))
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "бандл за свежим не снялся" in obj["reason"]
    assert "refusal" not in obj


def test_bundle_returning_the_same_token_makes_403_a_plain_refusal():
    """Бандл отдаёт тот же ключ — дело не в нём: 403 идёт по каскаду как
    отказ хоста (рецепт жив, сутки считает CLI), повторного запроса нет."""
    calls = []
    obj, broken = sutochno.probe(
        "x", SUTOCHNO_RECIPE, TODAY, date(2026, 9, 30),
        fetch=fake_fetch({"2224174": (403, load("auth_failed_403.json"))}, calls),
        text_fetch=fake_text({LEGACY: (200, load_text(
            "asset_index_apiToken_excerpt.js"))}))
    assert broken is None
    assert len(calls) == 1
    assert obj["refusal"]["status"] == 403
    assert "authentication failed" in obj["refusal"]["reason"].lower()
    assert obj["status"] == "insufficient_data"


def test_second_stale_answer_in_one_probe_is_not_refreshed_again():
    """Один раз за съём: второй 403 need_update_token — уже отказ хоста."""
    always_stale = (403, load("auth_failed_403.json"))
    pages = []
    recipe = recipe_with({"1": "Первый", "2": "Второй"})
    obj, broken = sutochno.probe(
        "x", recipe, TODAY, date(2026, 9, 30),
        fetch=fake_fetch({"1": always_stale, "2": always_stale}),
        text_fetch=fake_text({LEGACY: (200, load_text(
            "asset_index_apiToken_excerpt.js").replace(TOKEN, NEW_TOKEN))},
            pages))
    assert broken is None
    assert pages == [LEGACY]
    assert obj["refusal"]["status"] == 403


def test_default_token_is_used_when_recipe_has_none():
    calls = []
    recipe = recipe_with()
    del recipe["request"]["params"]["token"]
    probes.run_recipe("x", recipe, TODAY, date(2026, 9, 30),
                      fetch=fake_fetch({"2224174": blocks_payload()}, calls))
    assert calls[0][1]["token"] == TOKEN


# ---------------------------------------------------------------------------
# Каскад: отказ хоста, сеть, схема
# ---------------------------------------------------------------------------

def test_403_without_need_update_token_is_a_refusal_not_broken():
    pages = []
    obj, broken = sutochno.probe(
        "x", SUTOCHNO_RECIPE, TODAY, date(2026, 9, 30),
        fetch=fake_fetch({"2224174": (403, {"message": "forbidden"})}),
        text_fetch=fake_text({}, pages))
    assert broken is None
    assert pages == []                       # в бандл не ходили
    assert obj["refusal"]["status"] == 403
    assert obj["status"] == "insufficient_data"


def test_5xx_keeps_the_recipe_alive():
    obj, broken = probes.run_recipe(
        "x", SUTOCHNO_RECIPE, TODAY, date(2026, 9, 30),
        fetch=fake_fetch({"2224174": (502, None)}))
    assert broken is None
    assert obj["status"] == "insufficient_data" and "502" in obj["reason"]


def test_non_json_200_marks_broken():
    obj, broken = probes.run_recipe(
        "x", SUTOCHNO_RECIPE, TODAY, date(2026, 9, 30),
        fetch=fake_fetch({"2224174": (200, None)}))
    assert broken is not None and "не JSON" in broken
    assert obj["status"] == "insufficient_data"


def test_schema_change_marks_broken():
    obj, broken = probes.run_recipe(
        "x", SUTOCHNO_RECIPE, TODAY, date(2026, 9, 30),
        fetch=fake_fetch({"2224174": {"success": True, "data": {}}}))
    assert broken is not None and "data.calendars" in broken


def test_success_false_at_http_200_marks_broken_with_api_text():
    payload = {"success": False, "data": [], "errors": [
        "Пропущен обязательный аргумент(ы): object_id"], "actions": []}
    obj, broken = probes.run_recipe(
        "x", SUTOCHNO_RECIPE, TODAY, date(2026, 9, 30),
        fetch=fake_fetch({"2224174": payload}))
    assert broken is not None
    assert "success=false" in broken and "object_id" in broken


def test_block_without_dates_marks_broken():
    payload = {"success": True, "data": {"calendars": [{"type": "cs-book"}]},
               "errors": [], "actions": []}
    _, broken = probes.run_recipe("x", SUTOCHNO_RECIPE, TODAY,
                                  date(2026, 9, 30),
                                  fetch=fake_fetch({"2224174": payload}))
    assert broken is not None and "date_begin" in broken


def test_network_failure_on_one_object_is_partial_not_broken():
    recipe = recipe_with({"1": "Первый", "2": "Второй"})
    obj, broken = probes.run_recipe(
        "x", recipe, TODAY, date(2026, 9, 30),
        fetch=fake_fetch({"1": ConnectionError("обрыв"),
                          "2": blocks_payload(("2026-09-10", "2026-09-11",
                                               "cs-book"))}))
    assert broken is None
    assert obj["status"] == "partial" and "сетевой сбой" in obj["reason"]
    assert obj["units"]["Первый"]["2026-09-10"] == {"state": "unknown"}
    assert obj["units"]["Второй"]["2026-09-10"]["state"] == "busy"


def test_challenge_page_with_http_200_is_refusal_not_broken():
    fetch = fake_fetch({"2224174": (200, None)})
    fetch.last_body_text = CHALLENGE_PAGE
    obj, broken = probes.run_recipe("x", SUTOCHNO_RECIPE, TODAY,
                                    date(2026, 9, 30), fetch=fetch)
    assert broken is None
    assert "не пустили" in obj["refusal"]["reason"]


def test_403_after_an_object_was_taken_is_not_counted_as_a_refusal():
    recipe = recipe_with({"1": "Первый", "2": "Второй"})
    obj, broken = probes.run_recipe(
        "x", recipe, TODAY, date(2026, 9, 30),
        fetch=fake_fetch({"1": blocks_payload(("2026-09-10", "2026-09-11",
                                               "cs-book")),
                          "2": (403, {"message": "forbidden"})}))
    assert broken is None
    assert "refusal" not in obj
    assert obj["status"] == "partial" and "снято не до конца" in obj["reason"]


# ---------------------------------------------------------------------------
# Фонд не выдумывается; контракт снапшота; справочник
# ---------------------------------------------------------------------------

def test_fund_pair_only_when_recipe_says_one_home():
    payload = blocks_payload(("2026-09-10", "2026-09-11", "cs-book"))
    flat, _ = probes.run_recipe("x", recipe_with({"2224174": "Плавдом"}),
                                TODAY, date(2026, 9, 30),
                                fetch=fake_fetch({"2224174": payload}))
    assert flat["units"]["Плавдом"]["2026-09-10"] == {"state": "busy",
                                                     "block": "cs-book"}
    assert core.unit_basis(flat["units"])["basis"] == "type"
    assert "фонд не снят" in core.basis_note(flat["units"])
    assert "без фонда" in flat["reason"] and flat["status"] == "ok"

    multi, _ = probes.run_recipe(
        "x", recipe_with({"2224174": {"name": "Плавдом", "rooms_count": 3}}),
        TODAY, date(2026, 9, 30), fetch=fake_fetch({"2224174": payload}))
    assert "units_total" not in multi["units"]["Плавдом"]["2026-09-10"]
    assert "поштучного остатка" in multi["reason"] and "×3" in multi["reason"]

    one, _ = probes.run_recipe("x", SUTOCHNO_RECIPE, TODAY, date(2026, 9, 30),
                               fetch=fake_fetch({"2224174": payload}))
    assert one["units"]["Плавдом на озере"]["2026-09-10"] == {
        "state": "busy", "block": "cs-book", "units_total": 1, "units_free": 0}
    assert core.unit_basis(one["units"])["basis"] == "unit"
    for obj in (flat, multi, one):
        core.validate_object(obj)


def test_rooms_count_can_live_in_a_separate_params_map():
    obj, _ = probes.run_recipe(
        "x", recipe_with({"2224174": "Плавдом"}, rooms_count={"2224174": 1}),
        TODAY, date(2026, 9, 30),
        fetch=fake_fetch({"2224174": blocks_payload()}))
    assert obj["units"]["Плавдом"]["2026-09-10"]["units_total"] == 1


def test_same_named_objects_stay_separate_units():
    recipe = recipe_with({"11": "Хаусбот", "22": "Хаусбот", "33": "Плот"})
    obj, _ = probes.run_recipe(
        "x", recipe, TODAY, date(2026, 9, 30),
        fetch=fake_fetch({"11": blocks_payload(("2026-09-10", "2026-09-11",
                                                "cs-book")),
                          "22": blocks_payload(), "33": blocks_payload()}))
    assert set(obj["units"]) == {"Хаусбот [11]", "Хаусбот [22]", "Плот"}
    assert obj["units"]["Хаусбот [11]"]["2026-09-10"]["state"] == "busy"
    assert obj["units"]["Хаусбот [22]"]["2026-09-10"]["state"] == "free"


def test_recipe_without_objects_is_honest_insufficient_data():
    recipe = recipe_with()
    del recipe["request"]["params"]["objects"]
    obj, broken = probes.run_recipe("x", recipe, TODAY, date(2026, 9, 30),
                                    fetch=fake_fetch({}))
    assert broken is None
    assert obj["status"] == "insufficient_data" and "objects" in obj["reason"]
    assert obj["source_kind"] == "aggregator_quota"


def test_object_url_from_template_variants():
    assert sutochno.object_url(API, "42") == \
        "https://sutochno.ru/api/json/orders/getOrdersByObject?object_id=42"
    assert sutochno.object_url(
        "https://sutochno.ru/api/json/orders/getOrdersByObject", "42") == \
        "https://sutochno.ru/api/json/orders/getOrdersByObject?object_id=42"
    assert sutochno.object_url("https://h/x?a=1", "42") == "https://h/x?a=1&object_id=42"
    assert sutochno.object_url("", "42") == sutochno.object_url(API, "42")


def test_probe_version_is_declared_for_the_seasonality_series():
    assert isinstance(sutochno.PROBE_VERSION, int) and sutochno.PROBE_VERSION >= 1
    assert _common.probe_version_of("sutochno", sutochno) == \
        f"sutochno@{sutochno.PROBE_VERSION}"


def test_snapshot_contract_horizon_and_fund_like_the_other_engines():
    """Те же два обещания, что test_probe_contract проверяет у шести движков."""
    obj, broken = probes.run_recipe(
        "x", SUTOCHNO_RECIPE, TODAY, TODAY,
        fetch=fake_fetch({"2224174": load(
            "getOrdersByObject_2224174_tsarevschina.json")}))
    assert broken is None
    assert obj["grid_until"] and obj["inventory_until"]
    nights_seen = [n for cells in obj["units"].values() for n in cells]
    assert nights_seen and max(nights_seen) <= obj["grid_until"]
    core.validate_object(obj)
    assert core.unit_basis(obj["units"])["basis"] == "unit"
