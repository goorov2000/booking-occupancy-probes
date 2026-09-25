# -*- coding: utf-8 -*-
"""Скользящие горизонты (тикет 01): ни одного календарного года в коде.

Дата-бомба, которую эти тесты сторожат: DEFAULT_MONTHS = авг-окт 2026, из неё
считался конец горизонта запроса, и с 01.11.2026 прогон снимал бы НОЛЬ ночей.
Поэтому «сегодня» здесь подменяется на даты ПОСЛЕ бомбы и на 2027 год.
"""
import re
from datetime import date
from pathlib import Path

import pytest

import occupancy_core as core


@pytest.fixture
def frozen(monkeypatch):
    """Подменить core.today() — единственный шов даты для всего скилла."""
    def _freeze(value: date):
        monkeypatch.setattr(core, "today", lambda: value)
        return value
    return _freeze


# ---------------------------------------------------------------------------
# Месяцы сводки
# ---------------------------------------------------------------------------

def test_summary_months_after_the_date_bomb(frozen):
    frozen(date(2026, 11, 5))
    assert core.summary_months() == ["2026-11", "2026-12", "2027-01"]


def test_summary_months_next_year(frozen):
    frozen(date(2027, 6, 1))
    assert core.summary_months() == ["2027-06", "2027-07", "2027-08"]


def test_summary_months_count_is_adjustable():
    assert core.summary_months(date(2026, 12, 31), count=2) == ["2026-12",
                                                                "2027-01"]


# ---------------------------------------------------------------------------
# Два горизонта: сетка и фонд
# ---------------------------------------------------------------------------

def test_grid_horizon_is_a_year_ahead_of_today(frozen):
    frozen(date(2026, 11, 5))
    assert core.grid_horizon() == date(2027, 11, 5)
    assert core.grid_horizon() > core.today()


def test_grid_horizon_after_the_bomb_is_not_empty(frozen):
    """Прогон 05.11.2026: горизонт запроса непустой, ночи есть."""
    today = frozen(date(2026, 11, 5))
    nights = (core.grid_horizon() - today).days
    assert nights >= 365


def test_inventory_horizon_is_the_near_window(frozen):
    frozen(date(2027, 6, 1))
    assert core.inventory_horizon() == date(2027, 7, 16)  # +45 ночей
    assert core.inventory_horizon(days=10) == date(2027, 6, 11)
    # фонд всегда ближе сетки — иначе рычаг цены прогона не работает
    assert core.inventory_horizon() < core.grid_horizon()


def test_add_months_clamps_the_day():
    assert core.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert core.add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert core.add_months(date(2026, 12, 15), 12) == date(2027, 12, 15)


def test_default_months_is_deprecated_and_nobody_defaults_to_it():
    """DEFAULT_MONTHS осталась ради старых вызовов, но не служит дефолтом.

    Прежний тест утверждал обратное («её ещё читают build_summary и
    run_scheduled») и тем закреплял дату-бомбу: с 01.11.2026 сводка и
    телеграм-дайджест показали бы «нет данных» по ВСЕМ объектам, хотя ночи
    снимаются. Поэтому здесь grep, а не вкусовщина: боевой код зовёт
    summary_months(), а календарную константу — никто.
    """
    assert core.DEFAULT_MONTHS == ["2026-08", "2026-09", "2026-10"]
    scripts = Path(__file__).resolve().parents[1] / "occupancy"
    sources = ([p for p in scripts.glob("*.py")]
               + [p for p in (scripts / "probes").glob("*.py")])
    users = [p.name for p in sources
             if p.name != "occupancy_core.py"
             and re.search(r"\bcore\.DEFAULT_MONTHS\b|\b_core\.DEFAULT_MONTHS\b",
                           p.read_text(encoding="utf-8"))]
    assert users == []
