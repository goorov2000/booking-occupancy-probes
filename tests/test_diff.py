"""Дифф двух снапшотов одного объекта: сдвиг occupancy и новые занятые даты."""
import occupancy_core as core


PREV = {
    "Дом 1": {
        "2026-08-03": {"state": "free"},
        "2026-08-04": {"state": "free"},
        "2026-08-05": {"state": "busy"},
        "2026-08-06": {"state": "unknown"},
    }
}
CUR = {
    "Дом 1": {
        "2026-08-03": {"state": "busy"},     # free -> busy
        "2026-08-04": {"state": "free"},
        "2026-08-05": {"state": "busy"},     # busy -> busy: не «новая»
        "2026-08-06": {"state": "busy"},     # unknown -> busy
        "2026-08-07": {"state": "busy"},     # даты не было -> busy
    }
}


def test_diff_occupancy_shift_by_month():
    d = core.diff_units(CUR, PREV, ["2026-08"])
    aug = d["months"]["2026-08"]
    # prev: 1 busy / 3 known = 33.3; cur: 4 busy / 5 known = 80.0
    assert aug["prev_pct"] == 33.3
    assert aug["cur_pct"] == 80.0
    assert aug["delta_pp"] == 46.7


def test_diff_newly_busy_dates():
    d = core.diff_units(CUR, PREV, ["2026-08"])
    newly = {(e["unit"], e["date"]) for e in d["newly_busy"]}
    assert newly == {
        ("Дом 1", "2026-08-03"),
        ("Дом 1", "2026-08-06"),
        ("Дом 1", "2026-08-07"),
    }


def test_diff_skips_months_without_data():
    d = core.diff_units(CUR, PREV, ["2026-08", "2026-10"])
    assert "2026-10" not in d["months"]


def test_diff_line_human_readable():
    d = core.diff_units(CUR, PREV, ["2026-08"])
    line = core.diff_line(d, "2026-08-13-1200")
    assert "2026-08-13-1200" in line
    assert "пп" in line
    assert "2026-08-07" in line


def test_newly_sold_catches_movement_inside_a_type():
    """Тип из трёх домиков: продали ещё один, но не последний. До появления
    фонда такое движение не было видно вовсе — state обоих прогонов free."""
    prev = {"А-фрейм": {"2026-08-03": {"state": "free", "units_total": 3,
                                       "units_free": 3}}}
    cur = {"А-фрейм": {"2026-08-03": {"state": "free", "units_total": 3,
                                      "units_free": 1}}}
    diff = core.diff_units(cur, prev, ["2026-08"])
    assert diff["newly_busy"] == []
    assert diff["newly_sold"] == [{"unit": "А-фрейм", "date": "2026-08-03",
                                   "prev_busy": 0, "cur_busy": 2, "total": 3}]
    line = core.diff_line(diff, "2026-08-14-1200")
    assert "допродано домиков внутри типов: 1" in line
    assert "занято 2 из 3" in line


def test_newly_sold_empty_when_nothing_moved_inside_types():
    same = {"А-фрейм": {"2026-08-03": {"state": "free", "units_total": 3,
                                       "units_free": 2}}}
    diff = core.diff_units(same, same, ["2026-08"])
    assert diff["newly_sold"] == []
    assert "допродано" not in core.diff_line(diff, "2026-08-14-1200")


def test_diff_ignores_dates_outside_summary_months():
    """Снапшоты разной глубины: даты вне месяцев сводки в динамику не идут —
    иначе первый углублённый прогон рисует тысячи мнимых «новых занятых»."""
    prev = {"Дом": {"2026-08-03": {"state": "free"}}}
    cur = {"Дом": {"2026-08-03": {"state": "free"},
                   "2026-12-31": {"state": "busy"},
                   "2027-01-01": {"state": "busy"}}}
    diff = core.diff_units(cur, prev, ["2026-08"])
    assert diff["newly_busy"] == []


# ---------------------------------------------------------------------------
# Смена основания счёта — не продажа (волна 5, ревью wave4-probes)
# ---------------------------------------------------------------------------

def test_basis_change_is_not_reported_as_a_sale():
    """У ночи появился снятый фонд — это не «допродано домиков».

    Горизонт фонда в 45 ночей ползёт каждые сутки, поэтому вчерашняя клетка
    без фонда сегодня приходит с фондом. cell_units давал 0 занятых против
    2 занятых, и дайджест печатал человеку выдуманную продажу — каждый день.
    """
    prev = {"А-фрейм": {"2026-10-19": {"state": "free"}}}
    cur = {"А-фрейм": {"2026-10-19": {"state": "free",
                                      "units_total": 3, "units_free": 1}}}
    d = core.diff_units(cur, prev, ["2026-10"])
    assert d["newly_sold"] == []
    assert d["basis_changed"] == [{"unit": "А-фрейм", "date": "2026-10-19",
                                   "prev": "type", "cur": "unit"}]
    assert "не продажи" in core.diff_line(d, "2026-10-18-0630")


def test_real_sale_inside_a_type_is_still_reported():
    """Обратная сторона: при ОДНОМ основании допродажа домика видна как была."""
    prev = {"А-фрейм": {"2026-10-19": {"state": "free",
                                       "units_total": 3, "units_free": 3}}}
    cur = {"А-фрейм": {"2026-10-19": {"state": "free",
                                      "units_total": 3, "units_free": 1}}}
    d = core.diff_units(cur, prev, ["2026-10"])
    assert d["basis_changed"] == []
    assert d["newly_sold"] == [{"unit": "А-фрейм", "date": "2026-10-19",
                                "prev_busy": 0, "cur_busy": 2, "total": 3}]
