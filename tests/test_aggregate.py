"""Метрики занятости: occupancy_pct по месяцам × (все/будни/выходные).

Календарная справка: 2026-08-03 — понедельник, 2026-08-07 — пятница,
2026-08-08 — суббота, 2026-08-09 — воскресенье.
Пятница относится к ВЫХОДНЫМ (заезды загородных объектов живут уик-эндами
с пятницы) — это фиксируется тестом test_friday_is_weekend.
"""
import occupancy_core as core


def week_units():
    # Одна неделя августа 2026: пн 3 — вс 9.
    return {
        "Дом 1": {
            "2026-08-03": {"state": "busy"},            # пн
            "2026-08-04": {"state": "free"},            # вт
            "2026-08-05": {"state": "free"},            # ср
            "2026-08-06": {"state": "unknown"},         # чт
            "2026-08-07": {"state": "busy"},            # пт
            "2026-08-08": {"state": "free"},            # сб
            "2026-08-09": {"state": "sales_not_open"},  # вс
        }
    }


def get_month(metrics, month):
    by_month = {m["month"]: m for m in metrics}
    return by_month[month]


def test_denominator_excludes_sales_not_open_and_unknown():
    m = get_month(core.aggregate(week_units(), ["2026-08"]), "2026-08")
    # известные клетки: пн busy, вт free, ср free, пт busy, сб free -> 2/5
    assert m["cuts"]["all"]["busy"] == 2
    assert m["cuts"]["all"]["known"] == 5
    assert m["cuts"]["all"]["pct"] == 40.0
    assert m["unknown"] == 1
    assert m["sales_not_open"] == 1


def test_friday_is_weekend():
    m = get_month(core.aggregate(week_units(), ["2026-08"]), "2026-08")
    # будни пн-чт: busy пн из известных пн/вт/ср (чт unknown) -> 1/3
    wd = m["cuts"]["weekday"]
    assert (wd["busy"], wd["known"]) == (1, 3)
    # выходные пт-вс: пт busy + сб free (вс sales_not_open вне знаменателя) -> 1/2
    we = m["cuts"]["weekend"]
    assert (we["busy"], we["known"]) == (1, 2)
    assert we["pct"] == 50.0


def test_month_boundaries_and_empty_month():
    units = {
        "Дом 1": {
            "2026-08-31": {"state": "busy"},
            "2026-09-01": {"state": "free"},
        }
    }
    metrics = core.aggregate(units, ["2026-08", "2026-09", "2026-10"])
    aug, sep, okt = (get_month(metrics, m) for m in ("2026-08", "2026-09", "2026-10"))
    assert aug["cuts"]["all"] == {"busy": 1, "known": 1, "pct": 100.0}
    assert sep["cuts"]["all"] == {"busy": 0, "known": 1, "pct": 0.0}
    # месяц без данных: знаменатель пуст, процент честно None
    assert okt["cuts"]["all"]["known"] == 0
    assert okt["cuts"]["all"]["pct"] is None


def test_per_unit_cells_counted_per_unit():
    units = {
        "Дом 1": {"2026-08-03": {"state": "busy"}},
        "Дом 2": {"2026-08-03": {"state": "free"}},
    }
    m = get_month(core.aggregate(units, ["2026-08"]), "2026-08")
    assert m["cuts"]["all"] == {"busy": 1, "known": 2, "pct": 50.0}


def test_summary_line_marks_upper_bound_and_unknown():
    obj = {
        "username": "test_obj",
        "site": "https://example.com",
        "engine": "travelline",
        "source_kind": "module",
        "granularity": "per_unit",
        "units": week_units(),
        "status": "ok",
        "reason": "",
        "source_urls": ["https://example.com/booking"],
    }
    line = core.summary_line(obj, ["2026-08", "2026-09", "2026-10"])
    assert "оценка сверху" in line
    assert "40%" in line          # все дни августа
    assert "неизвестн" in line    # счётчик unknown виден
    assert "продажи не открыты" in line
    assert "test_obj" in line


# ---------------------------------------------------------------------------
# Фонд типа: клетка весит столько домиков, сколько их в типе (правка 15.08 по
# вопросу заказчика «все ли домики отсматривает» — у TravelLine и Bnovo юнит это
# ТИП, и типов вида «А-фрейм ×3» полно)
# ---------------------------------------------------------------------------

def test_cell_without_capacity_weighs_one_home_as_before():
    assert core.cell_units({"state": "busy"}) == (1, 1)
    assert core.cell_units({"state": "free"}) == (1, 0)


def test_cell_with_capacity_weighs_all_homes_of_the_type():
    # тип из трёх домиков, свободен один -> два проданы
    assert core.cell_units(
        {"state": "free", "units_total": 3, "units_free": 1}) == (3, 2)
    assert core.cell_units(
        {"state": "busy", "units_total": 3, "units_free": 0}) == (3, 3)


def test_broken_capacity_falls_back_to_binary_cell():
    for bad in ({"state": "free", "units_total": 0, "units_free": 0},
                {"state": "free", "units_total": 3, "units_free": 4},
                {"state": "free", "units_total": "три", "units_free": 1},
                {"state": "free", "units_total": True, "units_free": True}):
        assert core.cell_units(bad) == (1, 0)


def test_occupancy_counts_home_nights_not_type_nights():
    """Ровно тот случай, из-за которого цифры занижались: три одинаковых
    домика, две ночи; в первую продан один, во вторую — все три."""
    units = {"А-фрейм": {
        "2026-08-03": {"state": "free", "units_total": 3, "units_free": 2},
        "2026-08-04": {"state": "busy", "units_total": 3, "units_free": 0},
    }}
    m = get_month(core.aggregate(units, ["2026-08"]), "2026-08")
    assert m["cuts"]["all"] == {"busy": 4, "known": 6, "pct": 66.7}
    # без фонда те же две ночи дали бы 1 из 2 = 50% — вот и занижение
    bare = {"А-фрейм": {"2026-08-03": {"state": "free"},
                        "2026-08-04": {"state": "busy"}}}
    assert get_month(core.aggregate(bare, ["2026-08"]), "2026-08")[
        "cuts"]["all"]["pct"] == 50.0


def test_unit_basis_and_note():
    with_capacity = {"2026-08-03": {"state": "free", "units_total": 3,
                                    "units_free": 2}}
    bare = {"2026-08-03": {"state": "free"}}
    assert core.unit_capacity(with_capacity) == 3
    assert core.unit_capacity(bare) is None

    all_known = core.unit_basis({"А": with_capacity, "Б": with_capacity})
    assert all_known["basis"] == "unit" and all_known["homes"] == 6
    assert "считано по домикам: 6" in core.basis_note(
        {"А": with_capacity, "Б": with_capacity})

    mixed = core.unit_basis({"А": with_capacity, "Б": bare})
    assert mixed["basis"] == "mixed" and mixed["homes"] == 4
    assert "частично" in core.basis_note({"А": with_capacity, "Б": bare})

    none_known = core.unit_basis({"А": bare})
    assert none_known["basis"] == "type" and none_known["homes"] == 1
    assert "занижение" in core.basis_note({"А": bare})
