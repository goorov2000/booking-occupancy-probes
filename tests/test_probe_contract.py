# -*- coding: utf-8 -*-
"""Общий контракт снапшота: одни и те же обещания у ВСЕХ шести движков.

Ревью волны 3 нашло два дефекта, которые в одном движке починены, а в
соседних нет, — поэтому проверка идёт по всем разом, на фейковых ответах
(сеть тесты не трогают):

1. ГРАНИЦА СЪЁМА названа в объекте. `grid_until` — докуда спрашивалась
   ДОСТУПНОСТЬ, `inventory_until` — докуда спрашивался ФОНД. Без них месяц
   за краем съёма читается сводкой как обычное «нет данных», то есть «не
   спрашивали» путается с «движок не ответил». Обещание проверяется
   буквально: за grid_until в сетке нет ни одной клетки.

2. ФОНД НЕ ВЫДУМЫВАЕТСЯ. Клетка несёт либо обе половины пары
   units_total/units_free, либо ни одной, а «по домикам» (core.unit_basis ->
   "unit") объект имеет право говорить только там, где движок фонд и правда
   отдал. Заглушка «один домик» делала снимок неотличимым от объекта со
   снятым фондом: сводка подписывала строку «по домикам: 6 в 6 типах» ровно
   там, где движок про фонд не сказал ничего (вопрос заказчика 15.08 —
   «сколько домиков, а не типов»).
"""
from datetime import date

import pytest

import occupancy_core as core
import probes

from test_probe_bronirui import (BRONIRUI_RECIPE, fake_fetch as bronirui_fetch,
                                 recipe_with_numbers, small_calendar)
from test_probe_homereserve import HR_RECIPE, fake_fetch as hr_fetch
from test_probe_litepms import (LITEPMS_RECIPE, fake_fetch as lp_fetch,
                                load as lp_load)
from test_probe_travelline import RS_RECIPE, rs_fetch
from test_probe_uhotels import UH_RECIPE, fake_fetch as uh_fetch


DAY = date(2026, 9, 4)


def _bronirui_flat():
    """Bnovo-соседей нет: справочник рецепта старой формы, фонда движок не дал."""
    fetch = bronirui_fetch({"10043": small_calendar({"2026-09-04": (False, 9000)}),
                            "731": small_calendar({"2026-09-04": (True, None)})})
    return probes.run_recipe("vlesu_glamping", BRONIRUI_RECIPE, DAY, DAY,
                             fetch=fetch)


def _bronirui_with_fund():
    """Фонд номера = 1: бинарный ответ движка и есть остаток по домику."""
    recipe = recipe_with_numbers({"10043": {"name": "A-frame", "rooms_count": 1}})
    fetch = bronirui_fetch({"10043": small_calendar({"2026-09-04": (False, 9000)})})
    return probes.run_recipe("smr_test", recipe, DAY, DAY, fetch=fetch)


def _bronirui_multi_home():
    """Фонд номера = 3: сколько из трёх свободно, движок не говорит.

    Ревью волны 4: справочник знает ФОНД, а календарь отвечает «продаётся ли
    хоть один». Снимок обязан остаться на типах, а не выдавать фонд за
    остаток, — иначе свободная ночь весит три свободных домика, которых
    никто не считал.
    """
    recipe = recipe_with_numbers({"10043": {"name": "A-frame", "rooms_count": 3}})
    fetch = bronirui_fetch({"10043": small_calendar({"2026-09-04": (False, 9000)})})
    return probes.run_recipe("smr_test", recipe, DAY, DAY, fetch=fetch)


def _reservationsteps():
    prices = {"min_prices": {"2026-09-04": {"p": 7000, "g": 2}}}
    fetch = rs_fetch({"385130": prices, "385131": prices})
    return probes.run_recipe("yck_kuzminskoe", RS_RECIPE, DAY, DAY, fetch=fetch)


def _bnovo_alias():
    """Тот же API под именем движка bnovo — снимок обязан быть таким же."""
    recipe = dict(RS_RECIPE, engine="bnovo")
    prices = {"min_prices": {"2026-09-04": {"p": 7000, "g": 2}}}
    fetch = rs_fetch({"385130": prices, "385131": prices})
    return probes.run_recipe("a_ureki", recipe, DAY, DAY, fetch=fetch)


def _homereserve():
    apartments = {"apartments": [{"id": 5, "title": "Дом"}]}
    fetch = hr_fetch(apartments, {"5": {"calendar": [
        {"date": "2026-09-04", "available": True, "price": 100}]}})
    return probes.run_recipe("bani_na_ozerah", HR_RECIPE, DAY, DAY, fetch=fetch)


def _litepms():
    fetch = lp_fetch({"01-09-2026": lp_load("widget_calendar_dachavsosnah.html")})
    return probes.run_recipe("dachavsosnah", LITEPMS_RECIPE, DAY, DAY,
                             fetch=fetch)


def _uhotels():
    rooms = [{"code": "A", "name": "Домик", "max": 2, "price": 9000}]
    fetch = uh_fetch({}, default=rooms)
    return probes.run_recipe("pineriver_hotel", UH_RECIPE, DAY, DAY, fetch=fetch)


ALL_ENGINES = {
    "bronirui": _bronirui_flat,
    "bronirui+rooms_count": _bronirui_with_fund,
    "bronirui+multi_home": _bronirui_multi_home,
    "travelline/reservationsteps": _reservationsteps,
    "bnovo": _bnovo_alias,
    "homereserve": _homereserve,
    "litepms": _litepms,
    "uhotels": _uhotels,
}

# Движки, у которых фонд — физическое свойство единицы продажи, а не догадка:
# apartment HomeReserve и room-item LitePMS это конкретный домик (докстринги
# probes/homereserve.py и probes/litepms.py), остаток категории UHotels
# приходит тем же ответом, что доступность, а rooms_count=1 у bronirui значит
# «под номером один домик» — бинарный ответ календаря и есть его остаток.
# bronirui с фондом БОЛЬШЕ единицы в список не входит нарочно (ревью волны 4):
# фонд там известен, а остаток — нет, и снимок обязан остаться на типах.
FUND_IS_REAL = {"bronirui+rooms_count", "homereserve", "litepms", "uhotels"}


@pytest.mark.parametrize("engine", sorted(ALL_ENGINES))
def test_snapshot_names_the_horizon_it_actually_asked_about(engine):
    obj, broken = ALL_ENGINES[engine]()
    assert broken is None
    assert obj["grid_until"], f"{engine}: сетка без названной границы"
    assert obj["inventory_until"], f"{engine}: фонд без названной границы"
    nights = [n for cells in obj["units"].values() for n in cells]
    assert nights, f"{engine}: пустая сетка — контракт не на чем проверить"
    # Обещание поля буквальное: за границей клеток нет вовсе, поэтому месяц
    # целиком за ней — это «не спрашивали», а не «движок не ответил».
    assert max(nights) <= obj["grid_until"], engine


@pytest.mark.parametrize("engine", sorted(ALL_ENGINES))
def test_snapshot_never_invents_the_fund(engine):
    obj, broken = ALL_ENGINES[engine]()
    assert broken is None
    core.validate_object(obj)                 # пара units_total/units_free
    basis = core.unit_basis(obj["units"])["basis"]
    if engine in FUND_IS_REAL:
        assert basis == "unit", f"{engine}: фонд снят, а объект считан по типам"
    else:
        assert basis == "type", f"{engine}: фонд не снят, а объект зовёт типы домиками"
        assert "фонд не снят" in core.basis_note(obj["units"])
        for cells in obj["units"].values():
            for cell in cells.values():
                assert "units_total" not in cell, f"{engine}: выдуманный фонд"
