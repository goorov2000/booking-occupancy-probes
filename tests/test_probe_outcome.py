# -*- coding: utf-8 -*-
"""Правила исхода пробника (probes/_outcome.py): что ответ делает с рецептом.

Ревью волны 3, два подтверждённых дефекта:
- 502/503 от чужого хостера почти никогда не JSON, а общий каскад читал
  негодное тело как смену схемы раньше, чем смотрел на код, — и одна авария
  хостера ломала все цели этого хоста разом;
- частичный съём С ДАННЫМИ всё равно ставил объекту refusal, то есть копил
  сутки отказа живому рецепту (три подряд -> broken, переразведка не чинит).
"""
from probes import _outcome
from probes._common import ResponseVerdict


NGINX_502 = ("<html><head><title>502 Bad Gateway</title></head>"
             "<body><center><h1>502 Bad Gateway</h1></center>"
             "<hr><center>nginx</center></body></html>")


# ---------------------------------------------------------------------------
# Правило 1: авария чужого хостера рецепт не ломает
# ---------------------------------------------------------------------------

def test_5xx_without_json_body_is_network_not_broken():
    """Главный случай: страница nginx с кодом 502 — это сетевой сбой."""
    verdict = _outcome.classify_response(502, False, "категория",
                                         body_text=NGINX_502)
    assert verdict.kind == "network"
    assert "502" in verdict.reason


def test_every_5xx_is_network_whatever_the_body():
    for status in (500, 502, 503, 504, 521, 599):
        assert _outcome.classify_response(status, False, "юнит").kind \
            == "network", status
        assert _outcome.classify_response(status, True, "юнит").kind \
            == "network", status


def test_4xx_and_bad_body_at_200_still_break_the_recipe():
    """Правило узкое: смену схемы и отказ хоста оно не трогает."""
    assert _outcome.classify_response(404, True, "юнит").kind == "broken"
    assert _outcome.classify_response(200, False, "юнит").kind == "broken"
    assert _outcome.classify_response(200, True, "юнит").kind == "ok"
    assert _outcome.classify_response(403, True, "юнит").kind == "refused"
    assert _outcome.classify_response(429, True, "юнит").kind == "refused"


def test_challenge_page_stays_a_refusal_even_at_5xx():
    """Заслон опознаётся по телу и остаётся отказом, а не сетевым сбоем."""
    verdict = _outcome.classify_response(
        503, False, "юнит", body_text="<title>Just a moment...</title>")
    assert verdict.kind == "refused"


def test_retry_after_survives_the_wrapper():
    verdict = _outcome.classify_response(429, True, "юнит",
                                         headers={"Retry-After": "30"})
    assert isinstance(verdict, ResponseVerdict)
    assert verdict.retry_after == 30


# ---------------------------------------------------------------------------
# Правило 2: отказ поверх снятой сетки — не отказ
# ---------------------------------------------------------------------------

def _obj_with_grid():
    return {"units": {"Домик": {"2026-09-04": {"state": "free"},
                                "2026-09-05": {"state": "busy"}}},
            "status": "ok", "reason": ""}


def test_refusal_after_the_grid_was_taken_is_not_a_refusal():
    obj, broken = _outcome.finish(_obj_with_grid(), [], None,
                                  refused="HTTP 429 — нас не пустили",
                                  refusal_status=429)
    assert broken is None
    assert "refusal" not in obj          # счётчик суток не заводится
    assert obj["status"] == "partial"    # но цифры названы неполными
    assert "снято не до конца" in obj["reason"]
    assert "429" in obj["reason"]        # причина не потерялась


def test_refusal_without_a_single_known_cell_is_still_a_refusal():
    obj = {"units": {"Домик": {"2026-09-04": {"state": "unknown"}}},
           "status": "ok", "reason": ""}
    obj, broken = _outcome.finish(obj, [], None, refused="HTTP 403",
                                  refusal_status=403)
    assert broken is None
    assert obj["refusal"]["status"] == 403
    assert obj["status"] == "insufficient_data"


def test_finish_without_refusal_behaves_as_before():
    obj, broken = _outcome.finish(_obj_with_grid(), [], "схема сменилась")
    assert broken == "схема сменилась"
    assert obj["status"] == "insufficient_data"


def test_has_known_cells_counts_sales_not_open_as_an_answer():
    assert _outcome.has_known_cells(
        {"units": {"u": {"2026-09-04": {"state": "sales_not_open"}}}})
    assert not _outcome.has_known_cells({"units": {}})
    assert not _outcome.has_known_cells({})


# ---------------------------------------------------------------------------
# Правило 3: просьба ПОДОЖДАТЬ не метит отказом цель, к которой мы не ходили
# ---------------------------------------------------------------------------
# Ревью волны 4: 429 с длинным Retry-After запоминается транспортом на ХОСТ
# (probes/_common._host_not_before), и следующая цель того же хоста получает
# AccessRefused ещё до отправки запроса. На reservationsteps.ru таких целей
# 22 — один 429 заводил счётчик суток всем разом.

HOST_WAIT = ("public-api.reservationsteps.ru просит подождать 600 с "
             "(Retry-After) — объект пропущен, ждать дольше 120 с прогон "
             "не может")


def test_host_wide_wait_is_not_a_refusal_of_this_target():
    """Отказ БЕЗ кода HTTP — это пропуск цели, а не ответ хоста про неё."""
    obj = {"units": {"Домик": {"2026-09-04": {"state": "unknown"}}},
           "status": "ok", "reason": ""}
    obj, broken = _outcome.finish(obj, [], None, refused=HOST_WAIT,
                                  refusal_status=None)
    assert broken is None
    assert "refusal" not in obj           # счётчик суток не заводится
    assert obj["status"] == "insufficient_data"
    assert "600" in obj["reason"]         # причина видна человеку
    assert "запрос" in obj["reason"]


def test_host_wide_wait_keeps_earlier_failures_in_the_reason():
    obj = {"units": {}, "status": "ok", "reason": ""}
    obj, _ = _outcome.finish(obj, ["Купол: сетевой сбой"], None,
                             refused=HOST_WAIT, refusal_status=None)
    assert "Купол" in obj["reason"] and "600" in obj["reason"]


def test_answered_refusal_still_counts_against_the_recipe():
    """Узость правила: у ответа хоста код есть, и это по-прежнему отказ."""
    obj = {"units": {"Домик": {"2026-09-04": {"state": "unknown"}}},
           "status": "ok", "reason": ""}
    obj, _ = _outcome.finish(obj, [], None,
                             refused="HTTP 429 — нас не пустили",
                             refusal_status=429)
    assert obj["refusal"]["status"] == 429
