# -*- coding: utf-8 -*-
"""Поюнитный отчёт и отчёт о сезонности (тикеты 14 и 15).

Просьба заказчика от 26.08, повторённая 27.08 и 28.08: «мы не видим, какие домики
сдаются, какие нет; месячные проценты слишком размыты». Отчёт обязан отвечать
на неё, ни разу не соврав в подписи строки.

Чем проверяется — статическая фикстура `fixtures/units_report/history`
(9 прогонов 10-18.08.2026, генератор рядом в `_make_history.py`). Что в неё
зашито нарочно:
  * `obj_homes` (homereserve, фонда нет) — два ДОМИКА: движок адресует номер;
  * `obj_types` (travelline) — «А-фрейм» с фондом 3 и «Люкс» с фондом 1: ни
    один из них не смеет называться домиком, в том числе «Люкс» при M=1;
  * `obj_spheres` (bnovo) — «Сфера 1» и «Сфера 2»: имя выглядит домиковым,
    фонд не снят, значит это ТИП без фонда (грабли `wood_glamp` из решения Р3);
  * `obj_ramp` (travelline, фонд 7) — контрольный разгон ночи 17.08: 0% за
    трое суток до неё и 42.9% (3 домика из 7) утром самой ночи;
  * `inventory_until=2026-09-15` у `obj_types` — сентябрь покрыт фондом
    наполовину, октябрь не покрыт вовсе (тикет 06);
  * стена «продажи не открыты» у «Сферы 2» после 20.09 — закрытое окно
    продаж не должно читаться как аншлаг;
  * `obj_quota` — объект, снятый КВОТОЙ площадки-агрегатора: это доля
    площадки, а не загрузка объекта, ряд её выбрасывает, и отчёт обязан
    сказать об этом словами.

Сети здесь нет и быть не может: отчёт читает только диск.
"""
import json
import re
import shutil
from datetime import date
from pathlib import Path

import pytest

import build_units_report as bur
import ledger
import occupancy_core as core

HISTORY = Path(__file__).parent / "fixtures" / "units_report" / "history"
TODAY = date(2026, 8, 18)

TARGETS = [
    {"username": "obj_homes", "site": "https://example.com/obj_homes",
     "priority": 100},
    {"username": "obj_types", "site": "https://example.com/obj_types",
     "priority": 90},
    {"username": "obj_spheres", "site": "https://example.com/obj_spheres",
     "priority": 80},
    {"username": "obj_ramp", "site": "https://example.com/obj_ramp",
     "priority": 70},
    {"username": "obj_quota", "site": "https://example.com/obj_quota",
     "priority": 60},
]

REGIONS = {"obj_homes": "samara", "obj_types": "samara",
           "obj_spheres": "msk3h", "obj_ramp": "msk3h",
           "obj_quota": "samara"}


# ---------------------------------------------------------------------------
# Оснастка
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """История + собранный поверх неё слой сезонности (один раз на модуль)."""
    base = tmp_path_factory.mktemp("units_report")
    root = base / "snapshots"
    shutil.copytree(HISTORY, root)
    ledger_dir = base / "ledger"
    ledger.rebuild(root, ledger_dir)
    return {"root": root, "ledger_dir": ledger_dir, "base": base}


def collect(built, *, targets=None, regions=False, ledger_dir=True, **kw):
    rows = [dict(t) for t in (targets if targets is not None else TARGETS)]
    if regions:
        for t in rows:
            t.setdefault("region", REGIONS.get(t["username"], "msk3h"))
    return bur.collect(snapshot_root=built["root"], targets=rows, recipes={},
                       ledger_dir=built["ledger_dir"] if ledger_dir
                       else built["base"] / "нет-слоя",
                       today_=TODAY, **kw)


def by_unit(data, username, unit):
    for row in data["units"]:
        if row["username"] == username and row["unit"] == unit:
            return row
    raise AssertionError(f"нет строки {username}/{unit}")


# ---------------------------------------------------------------------------
# Решение Р3: три рода строк и три подписи
# ---------------------------------------------------------------------------

def test_type_with_capacity_three_is_never_called_a_home(built):
    """Приёмка тикета 14: тип с фондом 3 никогда не подписан «Домик»."""
    row = by_unit(collect(built), "obj_types", "А-фрейм")
    assert row["kind"] == bur.KIND_TYPE
    assert row["label"].startswith("Тип: А-фрейм — продано ")
    assert "Домик" not in row["label"]
    assert "из 3" in row["label"]


def test_type_with_capacity_one_is_still_a_type(built):
    """«Строка второго рода не называется домиком даже при M=1» (Р3)."""
    row = by_unit(collect(built), "obj_types", "Люкс")
    assert row["kind"] == bur.KIND_TYPE
    assert row["label"].startswith("Тип: Люкс — продано ")
    assert "из 1" in row["label"]
    assert "Домик" not in row["label"]


def test_per_unit_engine_without_capacity_is_a_home(built):
    row = by_unit(collect(built), "obj_homes", "Дом 1")
    assert row["kind"] == bur.KIND_HOME
    assert row["label"] == "Домик: Дом 1"


def test_home_looking_name_without_capacity_is_a_type(built):
    """Грабли `wood_glamp`: «Сфера 1» выглядит домиком, а фонд не снят."""
    row = by_unit(collect(built), "obj_spheres", "Сфера 1")
    assert row["kind"] == bur.KIND_TYPE_NO_CAPACITY
    assert row["label"] == "Тип: Сфера 1 — фонд не снят"
    assert "Домик" not in row["label"]


def test_kind_is_decided_by_engine_and_capacity_not_by_name():
    """Род — функция движка и фонда; имя юнита в неё не входит вовсе."""
    assert bur.row_kind("travelline", "per_unit", "Дом 1", None) == \
        bur.KIND_TYPE_NO_CAPACITY
    assert bur.row_kind("homereserve", "per_unit", "Тип люкс", None) == \
        bur.KIND_HOME
    # фонд больше одного домика перевешивает даже «домиковый» движок:
    # у bronirui с rooms_count>1 это ровно тикет 04
    assert bur.row_kind("bronirui", "per_unit", "Домик у леса", 4) == \
        bur.KIND_TYPE


def test_kind_agrees_with_the_seasonality_layer(built):
    """Род в отчёте и род в ряде — одна и та же величина, а не две копии."""
    data = collect(built)
    basis = {(r["username"], r["unit"]): r["basis"]
             for r in ledger.read_nights(built["ledger_dir"])}
    assert basis, "фикстура обязана давать замороженные ночи"
    matched = 0
    for row in data["units"]:
        key = (row["username"], row["unit"])
        if key in basis:
            assert row["kind"] == basis[key], key
            matched += 1
    # без этой строки проверка молча проходит при полном расхождении ключей
    assert matched == len(basis), (matched, len(basis))


def test_three_kinds_never_share_a_column(built):
    """Подписи трёх родов живут в ОДНОЙ колонке и не смешиваются между собой.

    Проверяется не только префикс: в таблице не должно быть отдельного
    столбца «домик/тип», где тип оказался бы подписан домиком.
    """
    data = collect(built)
    headers, rows = bur.unit_sheet(data)
    assert headers.count(bur.COL_KIND) == 1
    col = headers.index(bur.COL_KIND)
    kinds = {bur.KIND_HOME: "Домик: ", bur.KIND_TYPE: "Тип: ",
             bur.KIND_TYPE_NO_CAPACITY: "Тип: "}
    seen = set()
    for row, cells in zip(data["units"], rows):
        label = cells[col]
        assert label.startswith(kinds[row["kind"]]), label
        seen.add(row["kind"])
    assert seen == set(kinds), "фикстура обязана показать все три рода"
    # ни в одном другом столбце подписи рода нет — иначе они разъедутся
    for i, name in enumerate(headers):
        if i == col:
            continue
        for cells in rows:
            assert not str(cells[i]).startswith(("Домик:", "Тип:")), name


def test_row_count_by_kind_is_reported(built):
    """Сколько строк каждого рода — это и есть ответ заказчику, он в отчёте."""
    data = collect(built)
    assert data["by_kind"] == {bur.KIND_HOME: 2, bur.KIND_TYPE: 3,
                               bur.KIND_TYPE_NO_CAPACITY: 3}


# ---------------------------------------------------------------------------
# «Продано вперёд» против «фактически занято»
# ---------------------------------------------------------------------------

def test_fact_and_forward_are_never_the_same_column(built):
    """Две разные цифры и НИКОГДА не одна колонка."""
    data = collect(built)
    headers, _ = bur.unit_sheet(data)
    fact = {h for h in headers if h.startswith(bur.FACT_PREFIX)}
    ahead = {h for h in headers if h.startswith(bur.AHEAD_PREFIX)}
    assert fact and ahead
    assert not (fact & ahead)
    row = by_unit(data, "obj_types", "А-фрейм")
    assert set(row["fact"]) and set(row["ahead"])
    # август есть и там и там: ряд по прошедшим ночам и продажи по будущим —
    # ровно поэтому их и нельзя складывать в один столбец
    assert "2026-08" in row["fact"] and "2026-08" in row["ahead"]
    assert row["fact"]["2026-08"]["nights"] != row["ahead"]["2026-08"]["nights"]


def test_forward_sales_match_the_core_metric(built):
    """«Продано вперёд» считается ядром, а не своей арифметикой."""
    data = collect(built)
    row = by_unit(data, "obj_types", "А-фрейм")
    obj = data["objects"]["obj_types"]
    expect = core.aggregate({"А-фрейм": obj["units"]["А-фрейм"]},
                            data["months_ahead"])
    for m in expect:
        assert row["ahead"][m["month"]]["cuts"] == m["cuts"]


def test_fact_comes_from_the_frozen_series(built):
    """Факт — из ряда, а не из последнего снимка."""
    data = collect(built)
    row = by_unit(data, "obj_types", "А-фрейм")
    nights = [r for r in ledger.read_nights(built["ledger_dir"])
              if r["username"] == "obj_types" and r["unit"] == "А-фрейм"]
    expect = ledger.realized(username="obj_types", month="2026-08",
                             rows=nights)
    assert row["fact"]["2026-08"]["cuts"] == expect["cuts"]
    assert row["fact"]["2026-08"]["nights"] == expect["nights"]


def test_weekday_and_weekend_cuts_are_present(built):
    row = by_unit(collect(built), "obj_types", "А-фрейм")
    assert set(row["fact_cuts"]) == {"all", "weekday", "weekend"}
    assert row["fact_cuts"]["weekend"]["known"] > 0


# ---------------------------------------------------------------------------
# Честность цифр: короткий ряд и горизонт фонда
# ---------------------------------------------------------------------------

def test_short_series_month_is_not_shown_as_fact(built):
    """Приёмка тикета 15: ряд короче месяца не печатается как факт месяца."""
    data = collect(built)
    row = by_unit(data, "obj_homes", "Дом 1")
    fact = row["fact"]["2026-08"]
    assert fact["nights"] == 8 and fact["days_in_month"] == 31
    assert fact["complete"] is False
    cell = bur.fact_cell(fact)
    assert cell.startswith("неполный ряд:")
    assert "по 8 ночам из 31" in cell


def test_complete_month_is_shown_as_a_plain_number():
    """Полный месяц печатается цифрой — иначе оговорка обесценится."""
    fact = {"cuts": {"all": {"busy": 62, "known": 93, "pct": 66.7}},
            "nights": 30, "days_in_month": 30, "complete": True,
            "unknown": 0, "sales_not_open": 0, "gap_excluded": 0}
    cell = bur.fact_cell(fact)
    assert cell.startswith("66.7%")
    assert "неполный ряд" not in cell


def test_month_beyond_inventory_horizon_is_marked_not_shown_as_fact(built):
    """Приёмка тикета 14: месяц за границей фонда помечен, а не показан фактом.

    У `obj_types` фонд снят до 15.09: сентябрь покрыт наполовину, октябрь не
    покрыт вовсе. В обоих месяцах тип из трёх домиков весит один — процент
    занижен, и без пометки это читается как реальная незанятость.
    """
    data = collect(built, months_ahead=3)
    row = by_unit(data, "obj_types", "А-фрейм")
    assert row["inventory_gap"]["2026-09"] is True
    assert row["inventory_gap"]["2026-10"] is True
    assert row["inventory_gap"]["2026-08"] is False
    cell = bur.ahead_cell(row["ahead"]["2026-09"], gap=True)
    assert "фонд снят не на все ночи" in cell
    plain = bur.ahead_cell(row["ahead"]["2026-08"], gap=False)
    assert "фонд снят не на все ночи" not in plain
    assert row["notes"], "оговорка обязана доехать до колонки оговорок"


def test_closed_sales_window_is_not_read_as_full_house(built):
    """Стена «продажи не открыты» не превращается в занятость."""
    data = collect(built)
    row = by_unit(data, "obj_spheres", "Сфера 2")
    ahead = row["ahead"]["2026-10"]
    assert ahead["sales_not_open"] > 0
    assert ahead["cuts"]["all"]["known"] == 0
    assert bur.ahead_cell(ahead, gap=False).startswith("продажи не открыты")


def test_unit_gone_from_the_grid_keeps_its_row(built):
    """Юнит с историей, но выпавший из сегодняшней сетки, не исчезает.

    Правило №2 завода: честная строка с причиной, а не пустое место.
    """
    data = collect(built, targets=[t for t in TARGETS
                                   if t["username"] != "obj_ramp"])
    row = by_unit(data, "obj_ramp", "Купол")
    assert row["in_grid"] is False
    assert row["fact"]["2026-08"]["nights"] == 8
    # причина названа словами, а не «любой непустой строкой»
    assert "нет в сегодняшней сетке" in row["source_label"]


def test_aggregator_quota_is_named_not_silently_blank(built):
    """Квота площадки не превращается в пустую ячейку без объяснения.

    Ряд её выбрасывает (это доля агрегатора, а не загрузка объекта), и без
    подписи такая строка читалась бы как «объект простаивал весь месяц».
    """
    data = collect(built)
    row = by_unit(data, "obj_quota", "Шатёр")
    assert row["quota"] is True
    fact = row["fact"]["2026-08"]
    assert fact["cuts"]["all"]["known"] == 0
    assert fact["quota_excluded"] > 0
    assert "квота агрегатора" in bur.fact_cell(fact)
    assert "квот" in row["notes"]
    season = [r for r in data["seasonality"] if r["username"] == "obj_quota"]
    assert season and season[0]["quota_excluded"] > 0


# ---------------------------------------------------------------------------
# Разрез по региону (контракт 3 волны 3)
# ---------------------------------------------------------------------------

def test_target_without_a_single_unit_does_not_vanish(built):
    """Правило №2 завода: цель без цифр остаётся строкой с причиной.

    В поюнитную таблицу она не идёт — юнита у неё нет вовсе, и четвёртая
    подпись размыла бы три рода. Идёт на отдельный лист.
    """
    data = collect(built, targets=TARGETS + [{"username": "obj_dead",
                                              "site": "https://example.com/x",
                                              "priority": 1}])
    assert "obj_dead" not in {u["username"] for u in data["units"]}
    dead = [r for r in data["no_data"] if r["username"] == "obj_dead"]
    assert dead, "цель без юнитов обязана остаться в отчёте"
    headers, rows = bur.nodata_sheet(data)
    assert headers[-2:] == ["Вердикт заказчика", "Комментарий"]
    assert any(row[0] == "obj_dead" for row in rows)
    assert "obj_dead" in bur.render_markdown(data)


def test_missing_region_gives_an_honest_message_not_a_crash(built):
    data = collect(built, regions=False)
    assert data["region"]["ok"] is False
    assert "разрез по региону недоступен" in data["region"]["note"]
    sheets = bur.unit_sheets(data)
    assert [name for name, _, _ in sheets] == [bur.ALL_TARGETS_SHEET]
    assert data["region"]["note"] in "\n".join(bur.how_to_read_lines(data))


def test_region_present_splits_sheets(built):
    data = collect(built, regions=True)
    assert data["region"]["ok"] is True
    names = [name for name, _, _ in bur.unit_sheets(data)]
    assert names == ["Юниты — msk3h", "Юниты — samara"]
    _, _, rows = bur.unit_sheets(data)[1]
    assert len(rows) == 5      # obj_homes 2 + obj_types 2 + obj_quota 1


def test_regions_are_never_mixed_in_one_median(built):
    """Медиана считается по региону отдельно — «никогда не в одной медиане»."""
    data = collect(built, regions=True)
    per_region = {m["region"]: m for m in data["market"]
                  if m["month"] == "2026-08"}
    assert set(per_region) == {"samara", "msk3h"}
    # квота агрегатора в медиану не идёт, поэтому в samara считаны два
    # объекта из трёх — и это правильно, а не потеря строки
    assert per_region["samara"]["objects"] == 2
    assert per_region["msk3h"]["objects"] == 2


# ---------------------------------------------------------------------------
# Сезонность и темп бронирования (тикет 15)
# ---------------------------------------------------------------------------

def test_seasonality_rows_carry_fact_only(built):
    data = collect(built)
    rows = data["seasonality"]
    assert rows
    aug = [r for r in rows
           if r["username"] == "obj_types" and r["month"] == "2026-08"][0]
    assert aug["complete"] is False
    assert aug["cuts"]["weekend"]["known"] > 0
    assert "ahead" not in aug and "sold_ahead" not in aug


def test_pace_curve_reproduces_the_control_ramp(built):
    """Приёмка тикета 15: кривая воспроизводит контрольный разгон ночи.

    В фикстуре ночь 17.08 у `obj_ramp` идёт 0 из 7 за трое суток до неё и 3 из
    7 утром самой ночи — та же форма, что у живого `zapovednik_glamp` в ночь
    02.09 (0% -> 42.9% за трое суток).
    """
    data = collect(built)
    series = bur.night_pace(data, "obj_ramp", "2026-08-17")
    points = {p["lead_days"]: p["pct"] for p in series}
    assert points[3] == 0.0
    assert points[0] == 42.9


def test_pace_sheet_names_the_reason_when_a_point_has_no_number(built):
    """Точка без цифры печатает ПРИЧИНУ, а не пустоту и не чужой процент.

    Причин ровно две, и обе обязаны быть названы: точка стоит на слишком
    малом числе ночей (у глубоких lead истории просто нет) или её съел
    ближний горизонт фонда (тикет 06).
    """
    data = collect(built)
    rows = data["pace"]
    assert rows
    seen = set()
    for row in rows:
        for point in row["points"]:
            cell = bur.pace_cell(point)
            if point["pct"] is None:
                assert not cell.endswith("%"), cell
                # причина названа, а не свалена в одну подпись: «мало ночей:
                # 17 из 17» на живом ok_reka было ложью про полный набор
                assert cell in ("—", "ночи не сняты", "продажи не открыты",
                                "фонд не снят") or "мало ночей" in cell, cell
                if "мало ночей" in cell:
                    assert point["nights"] < point["nights_base"], cell
                seen.add(cell.split(":")[0])
            else:
                assert cell.startswith(("0", "1", "2", "3", "4", "5", "6",
                                        "7", "8", "9")), cell
    # обе причины обязаны прозвучать: свалить их в одну подпись нельзя
    assert seen == {"мало ночей", "фонд не снят"}, seen


def test_honest_deadlines_are_spelled_out(built):
    """Решение Р6: сроки прямым текстом, а не намёком (и из ряда, а не из головы)."""
    text = "\n".join(bur.how_to_read_lines(collect(built))).lower()
    for bit in ("сентябр", "октябр", "год"):
        assert bit in text
    assert "продано вперёд" in text


# ---------------------------------------------------------------------------
# Формат доставки
# ---------------------------------------------------------------------------

def test_how_to_read_is_human_text(built):
    """Лист «Как читать» написан человеческим текстом, без машинных обрывков."""
    lines = [l for l in bur.how_to_read_lines(collect(built)) if l.strip()]
    assert len(lines) >= 6
    body = "\n".join(lines)
    for junk in ("None", "{'", "[{", "units_total", "pct=", "type_no_capacity"):
        assert junk not in body, junk
    # хотя бы половина строк — предложения, а не заголовки из двух слов
    assert sum(1 for l in lines if len(l) > 80) >= len(lines) // 2


def test_xlsx_has_the_verdict_columns_and_named_sheets(built, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    data = collect(built, regions=True)
    path = bur.write_xlsx(data, tmp_path / "u.xlsx")
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames[0].endswith("Как читать")
    assert "Юниты — samara" in wb.sheetnames
    assert bur.SEASON_SHEET in wb.sheetnames
    assert bur.PACE_SHEET in wb.sheetnames
    ws = wb["Юниты — samara"]
    headers = [c.value for c in ws[1]]
    assert headers[-2:] == ["Вердикт заказчика", "Комментарий"]
    assert all(ws.cell(row=r, column=len(headers)).value in (None, "")
               for r in range(2, ws.max_row + 1))


def test_markdown_names_exactly_the_sheets_the_file_has(built, tmp_path):
    """Выжимка обещает ровно те листы, которые в файле есть.

    Статический список листов в тексте — тихая ложь: лист «Объекты без цифр»
    появляется только когда такие цели есть, а лист региона — только когда
    проставлен region.
    """
    openpyxl = pytest.importorskip("openpyxl")
    data = collect(built, regions=True)
    path = bur.write_xlsx(data, tmp_path / "n.xlsx")
    actual = openpyxl.load_workbook(path).sheetnames
    promised = bur._sheet_names(data)
    assert len(promised) == len(actual)
    for name, real in zip(promised, actual):
        assert real.endswith(name), (name, real)
    md = bur.render_markdown(data)
    for name in promised:
        assert f"«{name}»" in md, name


def test_markdown_is_written_and_counts_kinds(built):
    md = bur.render_markdown(collect(built))
    assert md.startswith("# ")
    assert "Домик" in md and "Тип" in md
    assert "| 2 |" in md or "домиков: 2" in md
    # ни одной висячей ячейки-обрывка вроде «None»
    assert "None" not in md


def test_main_writes_both_artifacts(built, tmp_path):
    pytest.importorskip("openpyxl")
    targets_path = tmp_path / "targets.json"
    targets_path.write_text(json.dumps(TARGETS, ensure_ascii=False),
                            encoding="utf-8")
    recipes_path = tmp_path / "recipes.json"
    recipes_path.write_text("{}", encoding="utf-8")
    out_dir = tmp_path / "out"
    md_path = tmp_path / "units.md"
    code = bur.main([
        "--snapshot-root", str(built["root"]),
        "--targets", str(targets_path),
        "--recipes", str(recipes_path),
        "--ledger-dir", str(built["ledger_dir"]),
        "--out-dir", str(out_dir),
        "--md", str(md_path),
        "--today", TODAY.isoformat(),
    ])
    assert code == 0
    assert md_path.is_file()
    assert list(out_dir.glob("*.xlsx"))


def test_missing_ledger_is_an_honest_note_not_a_crash(built):
    """Слоя сезонности нет — отчёт всё равно собирается, но без факта."""
    data = collect(built, ledger_dir=False)
    assert data["ledger_error"]
    assert data["months_fact"] == []
    row = by_unit(data, "obj_types", "А-фрейм")
    assert row["fact"] == {}
    assert row["ahead"]["2026-08"]["cuts"]["all"]["known"] > 0
    assert any("слой сезонности" in l.lower()
               for l in bur.how_to_read_lines(data))


def test_module_touches_no_network():
    """Запрет инструмента: отчёт читает диск и только диск."""
    source = (Path(bur.__file__)).read_text(encoding="utf-8")
    for forbidden in ("requests", "urllib.request", "http.client", "socket"):
        assert forbidden not in source, forbidden


def test_pipes_and_newlines_in_names_do_not_break_the_markdown(built):
    """Чужое имя юнита с `|` не разъезжает таблицу отчёта."""
    cell = bur.md_cell("Дом | «А»\nвторая строка")
    assert "\n" not in cell
    assert "|" not in cell.replace("\\|", "")


def test_row_label_is_stable_when_the_nearest_night_is_not_measured():
    """Тип с фондом без снятой ближайшей ночи всё равно не становится домиком."""
    label = bur.row_label(bur.KIND_TYPE, "А-фрейм", capacity=3, sold=None,
                          night=None)
    assert label.startswith("Тип: А-фрейм —")
    assert "Домик" not in label
    assert "3" in label


def test_collect_is_pure_and_writes_nothing(built, tmp_path):
    """Сборка данных ничего не пишет на диск: пишут только render/write."""
    before = sorted(p.name for p in built["base"].iterdir())
    collect(built)
    assert sorted(p.name for p in built["base"].iterdir()) == before


def test_headers_are_stable_between_regions(built):
    """Листы регионов имеют одинаковую шапку — иначе их нельзя сравнить."""
    data = collect(built, regions=True)
    sheets = bur.unit_sheets(data)
    assert len({tuple(headers) for _, headers, _ in sheets}) == 1


def test_snapshot_date_is_in_every_row(built):
    """«Плюс дата снимка» из тикета 14 — у каждой строки своя, не общая."""
    data = collect(built)
    headers, rows = bur.unit_sheet(data)
    col = headers.index(bur.COL_CHECKED)
    for row, cells in zip(data["units"], rows):
        if row["in_grid"]:
            assert re.match(r"\d{2}\.\d{2}\.\d{4}", str(cells[col])), cells[col]


# ---------------------------------------------------------------------------
# Ревью волны 3: цифра, стоящая на смешанном основании, обязана это сказать
# ---------------------------------------------------------------------------

def night_row(night, username="obj_x", unit="А-фрейм", *, state="free",
              total=3, free=3, gap=0, source_kind="module"):
    """Строка ряда так, как её пишет `ledger` — без обхода его же правил."""
    return {"night": night, "username": username, "unit": unit,
            "state": state, "units_total": total, "units_free": free,
            "price": None, "price_before_sale": None,
            "source_run_id": f"{night}-0630", "gap_days": gap,
            "basis": ledger.BASIS_TYPE, "engine": "travelline",
            "granularity": "per_unit", "source_kind": source_kind}


def august(shorts=()):
    """31 ночь августа у типа с фондом 3; в `shorts` фонд не снят (весит 1)."""
    rows = []
    for day in range(1, 32):
        night = f"2026-08-{day:02d}"
        if day in shorts:
            rows.append(night_row(night, total=1, free=1))
        else:
            rows.append(night_row(night, total=3, free=2))
    return rows


def test_fact_of_a_month_with_two_bases_says_so_in_the_cell():
    """Тикет 08 на уровне юнита: знаменатель из двух оснований помечается.

    Пять ночей августа пришли без снятого фонда — в них тип из трёх домиков
    весит один. Один процент по такому знаменателю сравнивать с соседней
    строкой нельзя, и об этом обязана сказать сама ячейка.
    """
    fact = bur._unit_fact(august(shorts=(14, 20, 28, 30, 31)),
                          ["2026-08"], 3)["2026-08"]
    assert fact["inventory_short"] == 5
    assert fact["measured_nights"] == 31
    cell = bur.fact_cell(fact)
    assert "фонд снят не на все ночи" in cell
    assert "5 из 31" in cell
    assert "занижен" in cell


def test_fact_of_a_month_with_one_basis_is_not_marked():
    """Обратная сторона: пометка обесценится, если её ставить всем подряд."""
    fact = bur._unit_fact(august(), ["2026-08"], 3)["2026-08"]
    assert fact["inventory_short"] == 0
    assert "фонд снят не на все ночи" not in bur.fact_cell(fact)


def test_partial_fund_of_a_past_month_reaches_the_notes_column():
    """Пометка стоит и в ячейке, и в «Оговорках»: их читают порознь."""
    row = {"in_grid": True, "quota": False, "kind": bur.KIND_TYPE,
           "inventory_gap": {"2026-09": False}, "ahead": {},
           "fact": {"2026-08": {"inventory_short": 2, "measured_nights": 10,
                                "gap_excluded": 0}}}
    note = bur._row_notes(row, ["2026-09"])
    assert "фонд снят не на все ночи" in note
    assert "авг" in note.lower()


# ---------------------------------------------------------------------------
# Медиана региона: и число ночей, и полнота — по этому же региону
# ---------------------------------------------------------------------------

MIXED_TARGETS = [{"username": "msk_obj", "region": "msk3h", "priority": 10},
                 {"username": "sam_obj", "region": "samara", "priority": 9},
                 {"username": "empty_obj", "region": "ural", "priority": 8}]


def test_median_of_a_region_counts_the_nights_of_that_region():
    """У самарского ряда в три ночи полноту подписывал подмосковный — нельзя.

    Ровно это состояние наступит при сборке самарской партии: у Подмосковья
    полный месяц, у Самары три ночи, и обе строки печатались как «31 ночь,
    полный месяц — да».
    """
    rows = [night_row(f"2026-08-{d:02d}", username="msk_obj")
            for d in range(1, 32)]
    rows += [night_row(f"2026-08-{d:02d}", username="sam_obj")
             for d in (1, 2, 3)]
    coverage = ledger.region_coverage(MIXED_TARGETS)
    assert coverage["ok"] is True
    market = {m["region"]: m
              for m in bur._market_rows(rows, ["2026-08"], MIXED_TARGETS,
                                        coverage)}
    assert market["msk3h"]["nights"] == 31
    assert market["msk3h"]["complete"] is True
    assert market["samara"]["nights"] == 3
    assert market["samara"]["complete"] is False
    # регион без единой строки не притворяется полным месяцем
    assert market["ural"]["nights"] == 0
    assert market["ural"]["complete"] is False
    assert market["ural"]["median_pct"] is None
    assert market["ural"]["objects"] == 0


def test_median_ignores_the_nights_it_threw_away():
    """Ночи, снятые с опозданием, в медиану не пошли — и в счёт ночей тоже."""
    rows = [night_row(f"2026-08-{d:02d}", username="sam_obj",
                      gap=1 if d > 2 else 0) for d in (1, 2, 3, 4, 5)]
    coverage = ledger.region_coverage(MIXED_TARGETS)
    market = {m["region"]: m
              for m in bur._market_rows(rows, ["2026-08"], MIXED_TARGETS,
                                        coverage)}
    assert market["samara"]["nights"] == 2
    assert market["samara"]["gap_excluded"] == 3


def test_mixed_median_never_calls_itself_regional(built):
    """Поле region проставлено не всем — медиана одна, и она это признаёт."""
    data = collect(built, regions=False)
    headers, rows = bur.season_sheet(data)
    note_col = headers.index("Оговорка")
    medians = [r for r in rows if str(r[0]).startswith("МЕДИАНА")]
    assert medians
    for row in medians:
        assert "региона" not in row[note_col], row[note_col]
        assert "смешан" in row[note_col], row[note_col]


# ---------------------------------------------------------------------------
# Темп: точка несёт своё основание
# ---------------------------------------------------------------------------

def _point(**kw):
    base = {"lead_days": 7, "busy": 24, "known": 33, "pct": 71.5,
            "unknown": 0, "sales_not_open": 0, "nights": 11,
            "nights_base": 21, "coverage": 0.524, "partial": True,
            "pct_partial": 71.5, "inventory_missing": 0}
    base.update(kw)
    return base


def test_pace_cell_of_a_partial_point_carries_its_night_count():
    """Точка на 11 ночах из 21 не может стоять голым процентом рядом с полной."""
    cell = bur.pace_cell(_point())
    assert cell.startswith("71.5%")
    assert "11" in cell and "21" in cell


def test_pace_cell_of_a_full_point_stays_a_plain_number():
    assert bur.pace_cell(_point(partial=False, coverage=1.0,
                                nights=21)) == "71.5%"


def test_pace_cell_names_the_fund_that_was_not_taken():
    cell = bur.pace_cell(_point(partial=False, coverage=1.0, nights=21,
                                inventory_missing=4))
    assert cell.startswith("71.5%")
    assert "фонд" in cell


def test_pace_sheet_warns_about_points_standing_on_shorter_sets(built):
    """Оговорка строки написана и про цифры, а не только про пустые ячейки."""
    data = collect(built)
    headers, rows = bur.pace_sheet(data)
    note_col = headers.index("Оговорка")
    partial = {r["username"] + r["month"] for r in data["pace"]
               if any(p["pct"] is not None and (p["partial"]
                                                or p["inventory_missing"])
                      for p in r["points"])}
    assert partial, "фикстура обязана дать частичную точку с цифрой"
    for row, source in zip(rows, data["pace"]):
        key = source["username"] + source["month"]
        note = str(row[note_col])
        assert ("короче опорного" in note) == (key in partial), (key, note)


def test_pace_curves_do_not_rescan_every_point_for_every_curve(built,
                                                               monkeypatch):
    """Сложность кривых — линейная по точкам, а не «кривые × все точки».

    На боевом слое 70 кривых по 39878 точек давали 2.79 млн проходов; целей
    станет вдвое больше, а горизонт — скользящий год.
    """
    data = collect(built)
    pace_rows = data["_pace_rows"]
    seen = []
    real = ledger.pace_curve

    def spy(username, month, **kw):
        seen.append(len(kw.get("rows") or []))
        return real(username, month, **kw)

    monkeypatch.setattr(ledger, "pace_curve", spy)
    curves = bur._pace_curves(pace_rows, data["units"],
                              data["months_fact"] + data["months_ahead"])
    assert len(curves) > 1
    assert sum(seen) <= len(pace_rows)


# ---------------------------------------------------------------------------
# Слой битый, а не отсутствующий
# ---------------------------------------------------------------------------

def test_broken_ledger_file_is_an_honest_note_not_a_traceback(built, tmp_path):
    """Оборванная дозапись слоя валила весь отчёт трейсбеком.

    Дозапись идёт в хвосте планового прогона — туда же прилетает SIGTERM по
    TimeoutStartSec. Отчёт обязан сказать «слой не прочитан», а не упасть.
    """
    broken = tmp_path / "broken-ledger"
    shutil.copytree(built["ledger_dir"], broken)
    with open(broken / ledger.NIGHTS_FILE, "ab") as fh:
        fh.write(b"\x1f\x8b\x08\x00truncated-member")
    data = bur.collect(snapshot_root=built["root"], targets=TARGETS,
                       recipes={}, ledger_dir=broken, today_=TODAY)
    assert data["ledger_error"]
    assert data["months_fact"] == []
    assert any("слой сезонности" in l.lower()
               for l in bur.how_to_read_lines(data))


# ---------------------------------------------------------------------------
# Сроки и «чего пока нет» — из данных, а не из памяти автора
# ---------------------------------------------------------------------------

def test_deadlines_are_computed_from_the_series(built):
    """«Первый честный месяц — октябрь» устаревает молча. Считаем из ряда."""
    data = collect(built)
    text = "\n".join(bur.how_to_read_lines(data)).lower()
    # ряд начат 10.08, значит первый ПОЛНЫЙ месяц — сентябрь, готов 1 октября
    assert "сентябрь 2026" in text
    assert "1 октября 2026" in text
    limits = bur.limits_rows(data)
    assert any("полн" in row[0].lower() and "месяц" in row[0].lower()
               for row in limits), limits


def test_a_full_month_in_the_series_removes_the_claim_that_there_is_none(built):
    """Как только полный месяц появится, отчёт перестаёт обещать его в будущем."""
    data = collect(built)
    data["seasonality"] = [dict(r, complete=True, nights=r["days_in_month"])
                           for r in data["seasonality"]]
    text = "\n".join(bur.how_to_read_lines(data)).lower()
    assert "первый полный месяц" not in text
    assert "август 2026" in text
    limits = bur.limits_rows(data)
    assert not any("полный месяц факта" == row[0].lower() for row in limits)


def test_limits_sheet_names_only_the_regions_that_really_have_no_numbers(built):
    """Про регион с цифрами отчёт не смеет писать «ни одной цифры»."""
    data = collect(built, regions=True, targets=TARGETS + [
        {"username": "obj_dead", "site": "https://example.com/dead",
         "priority": 1, "region": "krasnodar"}])
    limits = bur.limits_rows(data)
    text = "\n".join(" ".join(str(c) for c in row) for row in limits)
    assert "krasnodar" in text
    assert "samara" not in text.replace("region — samara", "")


# ---------------------------------------------------------------------------
# Мелочи, которые врут человеку
# ---------------------------------------------------------------------------

def test_nearest_sold_takes_both_numbers_from_one_night():
    """«Продано 0 из 3» склеивалось из клетки без фонда и максимума истории."""
    cells = {"2026-09-01": {"state": "free"},
             "2026-09-02": {"state": "free", "units_total": 3,
                            "units_free": 1}}
    assert bur._nearest_sold(cells, 3) == (2, "2026-09-02", False)


def test_type_without_a_single_night_of_fund_says_so():
    sold, night, no_fund = bur._nearest_sold({"2026-09-01": {"state": "free"}},
                                             3)
    assert (sold, night, no_fund) == (None, None, True)
    label = bur.row_label(bur.KIND_TYPE, "А-фрейм", capacity=3, sold=None,
                          no_fund=True)
    assert "Домик" not in label
    assert "фонд" in label and "3" in label


def test_ahead_cell_says_how_many_nights_it_counted():
    """Октябрь по ОДНОЙ контрольной ночи — это не «0% за октябрь»."""
    ahead = {"month": "2026-10", "label": "октябрь 2026",
             "cuts": {"all": {"busy": 0, "known": 1, "pct": 0.0}},
             "unknown": 0, "sales_not_open": 0, "nights": 1,
             "days_in_month": 31}
    cell = bur.ahead_cell(ahead)
    assert cell.startswith("0.0%")
    assert "1 ноч" in cell and "31" in cell
    full = dict(ahead, nights=31)
    assert "из 31" not in bur.ahead_cell(full).split("домико-ночей")[1]


def test_units_column_counts_the_month_and_only_counted_units(built):
    """Колонка знаменателя считалась по всему ряду и включала выброшенных."""
    data = collect(built)
    per_user = {r["username"]: r for r in data["seasonality"]
                if r["month"] == "2026-08"}
    assert per_user["obj_homes"]["units"] == 2
    # у квоты в факт не пошло ничего: юнитов в знаменателе тоже ноль
    assert per_user["obj_quota"]["units"] == 0


def test_sheet_titles_survive_a_region_with_forbidden_characters(built,
                                                                 tmp_path):
    """Одна косая черта в реестре роняла сборку уже после всех расчётов."""
    openpyxl = pytest.importorskip("openpyxl")
    targets = [dict(t) for t in TARGETS]
    for t in targets:
        t["region"] = ("Самара/обл. [юг]" if t["username"] != "obj_ramp"
                       else "Подмосковье: 3 часа")
    data = collect(built, targets=targets)
    path = bur.write_xlsx(data, tmp_path / "regions.xlsx")
    names = openpyxl.load_workbook(path).sheetnames
    assert len(set(names)) == len(names)
    for name in names:
        assert not set(name) & set("/\\?*[]:"), name
        assert len(name) <= 31
    assert bur._sheet_names(data)


def test_markdown_does_not_call_every_sellable_position_a_home(built):
    """Причал и баня — не домики: итог называется тем, что в нём лежит."""
    md = bur.render_markdown(collect(built))
    assert "позиц" in md.lower()


# ---------------------------------------------------------------------------
# Ревью волны 4: смешанное основание обязано дойти до листа сезонности
# ---------------------------------------------------------------------------

def season_of(rows, units, month="2026-08"):
    """Строка листа сезонности по готовому ряду — один объект, один месяц."""
    by_unit_rows = {}
    for row in rows:
        by_unit_rows.setdefault((row["username"], row["unit"]), []).append(row)
    return bur._seasonality_rows(by_unit_rows, [month], units)[0]


UNITS_AFRAME = [{"username": "obj_x", "unit": "А-фрейм", "capacity": 3,
                 "region": "samara"}]


def test_seasonality_row_of_a_month_with_two_bases_says_so():
    """Тот же дефект, что чинила ячейка юнита, — на листе для заказчика.

    Пять ночей августа пришли без снятого фонда: в них тип из трёх домиков
    весит один. Лист «Сезонность по месяцам» — та самая таблица, ради которой
    писался тикет 15, и печатать в ней голый процент нельзя.
    """
    row = season_of(august(shorts=(14, 20, 28, 30, 31)), UNITS_AFRAME)
    assert row["inventory_short"] == 5
    assert row["measured_unit_nights"] == 31

    headers, sheet = bur.season_sheet({"seasonality": [row], "market": []})
    note = sheet[0][headers.index("Оговорка")]
    assert "фонд снят не на все ночи" in note, note
    assert "5 из 31" in note, note
    assert "занижен" in note, note


def test_seasonality_row_on_one_basis_is_not_marked():
    """Пометка обесценится, если ставить её всем подряд."""
    row = season_of(august(), UNITS_AFRAME)
    assert row["inventory_short"] == 0
    headers, sheet = bur.season_sheet({"seasonality": [row], "market": []})
    assert "фонд снят не на все ночи" not in sheet[0][headers.index("Оговорка")]


def test_median_row_says_that_its_objects_stand_on_two_bases():
    """Медиана складывает занижённые проценты — и обязана это признать."""
    rows = august(shorts=(14, 20, 28, 30, 31))
    rows += [night_row(f"2026-08-{d:02d}", username="sam_obj")
             for d in range(1, 32)]
    units = [dict(UNITS_AFRAME[0]),
             {"username": "sam_obj", "unit": "А-фрейм", "capacity": 3,
              "region": "samara"}]
    by_unit_rows = {}
    for row in rows:
        by_unit_rows.setdefault((row["username"], row["unit"]), []).append(row)
    targets = [{"username": "obj_x", "region": "samara"},
               {"username": "sam_obj", "region": "samara"}]
    coverage = ledger.region_coverage(targets)
    season = bur._seasonality_rows(by_unit_rows, ["2026-08"], units)
    market = bur._market_rows(rows, ["2026-08"], targets, coverage, season)

    assert market[0]["objects_short"] == 1
    headers, sheet = bur.season_sheet({"seasonality": [], "market": market})
    note = sheet[0][headers.index("Оговорка")]
    assert "фонд снят не на все ночи" in note, note
    assert "1 из 2" in note, note


def test_median_without_a_hole_in_the_fund_is_not_marked():
    rows = august()
    by_unit_rows = {("obj_x", "А-фрейм"): rows}
    targets = [{"username": "obj_x", "region": "samara"}]
    coverage = ledger.region_coverage(targets)
    season = bur._seasonality_rows(by_unit_rows, ["2026-08"], UNITS_AFRAME)
    market = bur._market_rows(rows, ["2026-08"], targets, coverage, season)
    assert market[0]["objects_short"] == 0
    headers, sheet = bur.season_sheet({"seasonality": [], "market": market})
    assert "фонд снят не на все ночи" not in sheet[0][headers.index("Оговорка")]


def test_markdown_seasonality_carries_the_region_and_the_mixed_basis(built):
    """Выжимка — тот же отчёт, только версионируемый: колонки те же.

    Без колонки региона самарские и подмосковные объекты встают одним
    списком (тикет 14 это запрещает), а без пометки основания процент
    смешанного знаменателя читается как сравнимый с соседней строкой.
    """
    data = collect(built, regions=True)
    data["seasonality"] = [season_of(august(shorts=(14, 20)), UNITS_AFRAME)]
    text = bur.render_markdown(data)
    table = [l for l in text.splitlines() if l.startswith("| obj_x ")]
    assert table, text
    assert "samara" in table[0], table[0]
    assert "фонд снят не на все ночи" in table[0], table[0]
    assert "2 из 31" in table[0], table[0]


def test_markdown_seasonality_of_the_real_layer_names_every_region(built):
    """На боевом наборе колонка региона стоит у каждой строки сезонности."""
    data = collect(built, regions=True)
    text = bur.render_markdown(data)
    body = text.split("## Сезонность по объектам", 1)[1].split("\n## ", 1)[0]
    header = [l for l in body.splitlines() if l.startswith("|")][0]
    assert "Регион" in header, header
    rows = [l for l in body.splitlines() if l.startswith("| obj_")]
    assert rows
    for line in rows:
        assert "samara" in line or "msk3h" in line, line


# ---------------------------------------------------------------------------
# Ревью волны 4: дыра в слое обязана дойти до оператора и до заказчика
# ---------------------------------------------------------------------------

def holed_ledger(built, tmp_path, stale="2026-08-09-0630"):
    """Копия слоя, в индексе которого записана дыра (прогон задним числом)."""
    holed = tmp_path / "holed-ledger"
    shutil.copytree(built["ledger_dir"], holed)
    path = holed / ledger.INDEX_FILE
    index = json.loads(path.read_text(encoding="utf-8"))
    index["stale_runs"] = [stale]
    path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return holed


def test_layer_hole_reaches_the_customer(built, tmp_path):
    """Прогон, не попавший в слой, — дыра в цифрах, а не тихий день.

    `ledger.append_note`/`hole_note` пишутся ровно для этого; до отчёта дыра
    не доходила вовсе, и ряд без этих суток читался как честный.
    """
    data = bur.collect(snapshot_root=built["root"], targets=TARGETS,
                       recipes={}, ledger_dir=holed_ledger(built, tmp_path),
                       today_=TODAY)
    assert "2026-08-09-0630" in data["ledger_hole"]

    text = "\n".join(bur.how_to_read_lines(data))
    assert "2026-08-09-0630" in text, text
    limits = "\n".join(" ".join(str(c) for c in row)
                       for row in bur.limits_rows(data))
    assert "2026-08-09-0630" in limits, limits
    assert "2026-08-09-0630" in bur.render_markdown(data)


def test_a_healthy_layer_says_nothing_about_holes(built):
    data = collect(built)
    assert data["ledger_hole"] == ""
    assert "задним числом" not in "\n".join(bur.how_to_read_lines(data))


def test_layer_hole_reaches_the_operator(built, tmp_path, capsys):
    """Оператор видит дыру в выводе прогона, а не только в index.json."""
    pytest.importorskip("openpyxl")
    targets_path = tmp_path / "targets.json"
    targets_path.write_text(json.dumps(TARGETS, ensure_ascii=False),
                            encoding="utf-8")
    (tmp_path / "recipes.json").write_text("{}", encoding="utf-8")
    code = bur.main([
        "--snapshot-root", str(built["root"]),
        "--targets", str(targets_path),
        "--recipes", str(tmp_path / "recipes.json"),
        "--ledger-dir", str(holed_ledger(built, tmp_path)),
        "--out-dir", str(tmp_path / "out"),
        "--md", str(tmp_path / "units.md"),
        "--today", TODAY.isoformat(),
    ])
    assert code == 0
    out = capsys.readouterr().out + capsys.readouterr().err
    assert "2026-08-09-0630" in out, out


def test_layer_hole_is_seen_when_the_index_promises_more_rows(built, tmp_path):
    """Молчащая порча: индекс обещает больше строк, чем лежит в файле."""
    holed = tmp_path / "shrunk-ledger"
    shutil.copytree(built["ledger_dir"], holed)
    path = holed / ledger.INDEX_FILE
    index = json.loads(path.read_text(encoding="utf-8"))
    index["nights_rows"] = index["nights_rows"] + 900
    path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    data = bur.collect(snapshot_root=built["root"], targets=TARGETS,
                       recipes={}, ledger_dir=holed, today_=TODAY)
    assert "900" in data["ledger_hole"] or "обещает" in data["ledger_hole"]
    assert "повреж" in data["ledger_hole"]


# ---------------------------------------------------------------------------
# Ревью волны 4 (minor): «посчитано по N ночам» — это ночи, вошедшие в процент
# ---------------------------------------------------------------------------

def test_counted_nights_are_the_nights_that_went_into_the_percent():
    """N в «посчитано по N ночам из M» брался из ряда, а не из знаменателя.

    Ночи `unknown` и «продажи не открыты» в процент не идут — а в число
    ночей, по которым он якобы посчитан, шли: 17 ночей ряда, из них
    измеренных 12, и читатель считал процент вдвое надёжнее, чем он есть.
    """
    rows = [night_row(f"2026-08-{d:02d}") for d in range(1, 13)]
    rows += [night_row(f"2026-08-{d:02d}", state="unknown", total=0, free=0)
             for d in range(13, 16)]
    rows += [night_row(f"2026-08-{d:02d}", state="sales_not_open", total=0,
                       free=0) for d in range(16, 18)]
    fact = bur._unit_fact(rows, ["2026-08"], 3)["2026-08"]
    assert fact["nights"] == 17
    assert fact["measured_nights"] == 12
    cell = bur.fact_cell(fact)
    assert "по 12 ночам из 31" in cell, cell
    assert "по 17 ночам" not in cell, cell
