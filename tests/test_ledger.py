# -*- coding: utf-8 -*-
"""Ledger: заморозка прошедших ночей, кривая темпа, разрезы по факту.

Статическая фикстура — `fixtures/ledger/history` (4 прогона 10-13.08.2026, три
обезличенных объекта, генератор рядом в `_make_history.py`). Сети в тестах нет
и быть не может: ledger читает только диск.

Календарная справка: 2026-08-10 — понедельник, 2026-08-14 — пятница
(в метрике пятница относится к ВЫХОДНЫМ, см. occupancy_core).

Что зашито в фикстуру нарочно:
- `obj_gap` не снят в прогоне 12.08 — его ночь 12.08 замерзает снимком 11.08,
  то есть с `gap_days=1`, и в рыночную медиану не идёт;
- у `obj_home/Дом 1` ночь 11.08 стоила 10000 свободной и стала занятой —
  проверка обратной сборки `price_before_sale`;
- `obj_type/А-фрейм` — тип с фондом 3, он не должен называться домиком.
"""
import gzip
import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest

import ledger
import occupancy_core as core

HISTORY = Path(__file__).parent / "fixtures" / "ledger" / "history"
ALL_RUNS = ["2026-08-10-0630", "2026-08-11-0630", "2026-08-12-0630",
            "2026-08-13-0630"]


def history_copy(tmp_path, runs=ALL_RUNS):
    """Копия статической истории (или её начала) в tmp_path/snapshots."""
    root = tmp_path / "snapshots"
    root.mkdir(parents=True, exist_ok=True)
    for run_id in runs:
        shutil.copytree(HISTORY / run_id, root / run_id)
    return root


def by_key(rows):
    return {(r["night"], r["username"], r["unit"]): r for r in rows}


# ---------------------------------------------------------------------------
# Правило Р4: ночь замерзает состоянием из ПОСЛЕДНЕГО снимка, где она была
# ---------------------------------------------------------------------------

def test_rebuild_freezes_only_past_nights(tmp_path):
    root = history_copy(tmp_path)
    out = tmp_path / "ledger"
    stats = ledger.rebuild(root, out)

    rows = ledger.read_nights(out)
    nights = sorted({r["night"] for r in rows})
    # прогон 13.08 ещё держит ночь 13.08 в горизонте: она НЕ заморожена
    assert nights == ["2026-08-10", "2026-08-11", "2026-08-12"]
    assert stats["nights_rows"] == len(rows) == 12
    assert stats["frozen_through"] == "2026-08-12"


def test_frozen_state_comes_from_the_last_snapshot_holding_the_night(tmp_path):
    out = tmp_path / "ledger"
    ledger.rebuild(history_copy(tmp_path), out)
    rows = by_key(ledger.read_nights(out))

    # 11.08 у «Дом 1»: 10.08 снимок видел free, снимок утра 11.08 — busy
    frozen = rows[("2026-08-11", "obj_home", "Дом 1")]
    assert frozen["state"] == "busy"
    assert frozen["source_run_id"] == "2026-08-11-0630"
    assert frozen["gap_days"] == 0
    # тип с фондом: 12.08 заморожен снимком того же утра, продан 2 домика из 3
    typ = rows[("2026-08-12", "obj_type", "А-фрейм")]
    assert (typ["units_total"], typ["units_free"]) == (3, 1)
    assert typ["source_run_id"] == "2026-08-12-0630"


def test_night_missed_by_a_run_is_frozen_late_and_carries_gap_days(tmp_path):
    out = tmp_path / "ledger"
    ledger.rebuild(history_copy(tmp_path), out)
    rows = by_key(ledger.read_nights(out))

    late = rows[("2026-08-12", "obj_gap", "Шатёр")]
    # прогон 12.08 объект не снял -> последний снимок ночи 12.08 сделан 11.08
    assert late["source_run_id"] == "2026-08-11-0630"
    assert late["gap_days"] == 1
    assert late["state"] == "busy"


def test_run_lateness_from_provenance_counts_as_gap(tmp_path):
    """gap_days объекта (прогон догонял простой машины) переносится в ряд."""
    root = tmp_path / "snapshots"
    obj = make_obj("obj_late", {"Дом": {"2026-08-10": {"state": "busy"}}})
    obj["gap_days"] = 2
    core.write_snapshot(root, "2026-08-10-1830",
                        {"started_at": "2026-08-10T18:30:00+03:00",
                         "targets": ["obj_late"]}, [obj])
    core.write_snapshot(root, "2026-08-11-0630",
                        {"started_at": "2026-08-11T06:30:00+03:00",
                         "targets": ["obj_late"]},
                        [make_obj("obj_late",
                                  {"Дом": {"2026-08-11": {"state": "free"}}})])
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    row = by_key(ledger.read_nights(out))[("2026-08-10", "obj_late", "Дом")]
    assert row["gap_days"] == 2


# ---------------------------------------------------------------------------
# Род строки (решение Р3): домик / тип с фондом / тип без фонда
# ---------------------------------------------------------------------------

def test_basis_tells_home_from_type(tmp_path):
    out = tmp_path / "ledger"
    ledger.rebuild(history_copy(tmp_path), out)
    rows = by_key(ledger.read_nights(out))

    # homereserve адресует физический домик
    assert rows[("2026-08-10", "obj_home", "Дом 1")]["basis"] == ledger.BASIS_HOME
    # travelline с фондом 3 — тип, домиком не называется никогда
    assert rows[("2026-08-10", "obj_type", "А-фрейм")]["basis"] == ledger.BASIS_TYPE
    # bnovo без снятого фонда — тип, про который мы не знаем, сколько в нём домиков
    assert (rows[("2026-08-10", "obj_gap", "Шатёр")]["basis"]
            == ledger.BASIS_TYPE_NO_CAPACITY)


def test_rc_bookings_physical_apartments_remain_homes_in_saved_ledger(tmp_path):
    root = history_copy(tmp_path)
    for path in root.glob("*/obj_home.json"):
        obj = json.loads(path.read_text())
        obj["engine"] = "rc-bookings"
        path.write_text(json.dumps(obj))
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    homes = [r for r in ledger.read_nights(out) if r["username"] == "obj_home"]
    assert homes and all(r["basis"] == ledger.BASIS_HOME for r in homes)


def test_basis_does_not_depend_on_when_the_fund_showed_up(tmp_path):
    """Род строки — свойство ЮНИТА, а не места ночи в истории.

    На живой истории 87 юнитов из 157 несли по два разных `basis`: ночь
    замерзала до того, как фонд впервые попадал в снапшот (a_ureki/«Белый
    Дом» — 14.08 `type_no_capacity`, 15.08 и дальше `type`). Цифры при этом
    верные, врёт подпись, и отчёту приходится гадать.
    """
    root = tmp_path / "snapshots"
    # 10.08: фонд юнита ещё не снят (движок отдал клетку без units_total)
    core.write_snapshot(root, "2026-08-10-0630",
                        {"started_at": "x", "targets": ["obj_tl"]},
                        [make_obj("obj_tl",
                                  {"Белый Дом": {"2026-08-10": {"state": "free"},
                                                 "2026-08-11": {"state": "free"}}},
                                  engine="travelline")])
    # 11.08: тот же юнит показал, что домиков в нём четыре
    core.write_snapshot(root, "2026-08-11-0630",
                        {"started_at": "x", "targets": ["obj_tl"]},
                        [make_obj("obj_tl",
                                  {"Белый Дом": {"2026-08-11": {
                                      "state": "free", "units_total": 4,
                                      "units_free": 2}}},
                                  engine="travelline")])
    core.write_snapshot(root, "2026-08-12-0630",
                        {"started_at": "x", "targets": ["obj_tl"]},
                        [make_obj("obj_tl",
                                  {"Белый Дом": {"2026-08-12": {"state": "free"}}},
                                  engine="travelline")])
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    rows = ledger.read_nights(out)
    assert {r["basis"] for r in rows} == {ledger.BASIS_TYPE}


def test_basis_of_older_rows_is_fixed_up_when_the_fund_appears_later(tmp_path):
    """Фонд, снятый ПОСЛЕ дозаписи ночи, чинит и уже записанные строки."""
    root = tmp_path / "snapshots"
    core.write_snapshot(root, "2026-08-10-0630",
                        {"started_at": "x", "targets": ["obj_tl"]},
                        [make_obj("obj_tl",
                                  {"Дом": {"2026-08-10": {"state": "free"}}},
                                  engine="travelline")])
    core.write_snapshot(root, "2026-08-11-0630",
                        {"started_at": "x", "targets": ["obj_tl"]},
                        [make_obj("obj_tl",
                                  {"Дом": {"2026-08-11": {"state": "free"}}},
                                  engine="travelline")])
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    assert [r["basis"] for r in ledger.read_nights(out)] == \
        [ledger.BASIS_TYPE_NO_CAPACITY]

    core.write_snapshot(root, "2026-08-12-0630",
                        {"started_at": "x", "targets": ["obj_tl"]},
                        [make_obj("obj_tl",
                                  {"Дом": {"2026-08-12": {"state": "free",
                                                          "units_total": 3,
                                                          "units_free": 1}}},
                                  engine="travelline")])
    ledger.append(root, out)
    assert {r["basis"] for r in ledger.read_nights(out)} == {ledger.BASIS_TYPE}


def test_type_with_capacity_one_is_still_a_type(tmp_path):
    root = tmp_path / "snapshots"
    units = {"Люкс": {"2026-08-10": {"state": "busy", "units_total": 1,
                                     "units_free": 0}}}
    core.write_snapshot(root, "2026-08-10-0630",
                        {"started_at": "x", "targets": ["obj_tl"]},
                        [make_obj("obj_tl", units, engine="travelline")])
    core.write_snapshot(root, "2026-08-11-0630",
                        {"started_at": "x", "targets": ["obj_tl"]},
                        [make_obj("obj_tl",
                                  {"Люкс": {"2026-08-11": {"state": "free"}}},
                                  engine="travelline")])
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    row = by_key(ledger.read_nights(out))[("2026-08-10", "obj_tl", "Люкс")]
    assert row["basis"] == ledger.BASIS_TYPE


# ---------------------------------------------------------------------------
# Обратная сборка цены проданной ночи
# ---------------------------------------------------------------------------

def test_price_before_sale_recovered_from_earlier_snapshot(tmp_path):
    out = tmp_path / "ledger"
    ledger.rebuild(history_copy(tmp_path), out)
    rows = by_key(ledger.read_nights(out))

    sold = rows[("2026-08-11", "obj_home", "Дом 1")]
    assert sold["price"] is None          # у занятой клетки движок цены не даёт
    assert sold["price_before_sale"] == 10000
    # ночь 12.08 подорожала до продажи — берётся ПОСЛЕДНЯЯ известная цена
    assert rows[("2026-08-12", "obj_home", "Дом 1")]["price_before_sale"] == 10500
    # свободная ночь несёт свою цену и не притворяется проданной
    free_row = rows[("2026-08-10", "obj_home", "Дом 2")]
    assert free_row["price"] == 9000
    assert free_row["price_before_sale"] is None


def test_price_before_sale_only_for_sold_nights(tmp_path):
    """Ночь, которую мы потеряли из виду, не «продана по последней цене».

    На живой истории таких строк нашлось 6 (state=unknown с подставленной
    ценой) — цифра выглядела бы выручкой, которой не было.
    """
    root = tmp_path / "snapshots"
    core.write_snapshot(root, "2026-08-10-0630",
                        {"started_at": "x", "targets": ["obj_home"]},
                        [make_obj("obj_home",
                                  {"Дом": {"2026-08-10": {"state": "free",
                                                          "price": 8000}}})])
    core.write_snapshot(root, "2026-08-10-1830",
                        {"started_at": "x", "targets": ["obj_home"]},
                        [make_obj("obj_home",
                                  {"Дом": {"2026-08-10": {"state": "unknown"}}})])
    core.write_snapshot(root, "2026-08-11-0630",
                        {"started_at": "x", "targets": ["obj_home"]},
                        [make_obj("obj_home",
                                  {"Дом": {"2026-08-11": {"state": "free"}}})])
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    row = by_key(ledger.read_nights(out))[("2026-08-10", "obj_home", "Дом")]
    assert row["state"] == "unknown"
    assert row["price_before_sale"] is None


# ---------------------------------------------------------------------------
# Идемпотентность и инкремент
# ---------------------------------------------------------------------------

def test_rebuild_is_byte_identical_on_repeat(tmp_path):
    root = history_copy(tmp_path)
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    before = {name: (out / name).read_bytes() for name in
              (ledger.NIGHTS_FILE, ledger.PACE_FILE, ledger.INDEX_FILE)}
    ledger.rebuild(root, out)
    after = {name: (out / name).read_bytes() for name in before}
    assert after == before


def test_append_adds_only_new_nights_and_matches_full_rebuild(tmp_path):
    root = history_copy(tmp_path, ALL_RUNS[:3])
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    first = ledger.read_nights(out)
    assert sorted({r["night"] for r in first}) == ["2026-08-10", "2026-08-11"]

    shutil.copytree(HISTORY / ALL_RUNS[3], root / ALL_RUNS[3])
    stats = ledger.append(root, out)
    assert stats["new_runs"] == [ALL_RUNS[3]]
    assert stats["nights_rows_added"] == 4        # четыре юнита за ночь 12.08

    grown = ledger.read_nights(out)
    assert grown[:len(first)] == first            # старое не переписано
    full_out = tmp_path / "ledger_full"
    ledger.rebuild(history_copy(tmp_path / "full", ALL_RUNS), full_out)
    assert by_key(grown) == by_key(ledger.read_nights(full_out))


def test_append_leaves_a_run_that_is_still_being_written_alone(tmp_path):
    """Недописанный прогон не морозит ночи: заморозка необратима.

    После тикета 02 полупустой каталог прогона — НОРМА в течение всего съёма
    (до 90 минут): run.json пишется первым, объекты дописываются по ходу.
    Если взять такой прогон в работу, он навсегда попадёт в index["runs"], а
    ночи замёрзнут по данным, которых в нём ещё нет.
    """
    root = history_copy(tmp_path, ALL_RUNS[:2])
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)

    core.open_snapshot(root, "2026-08-12-0630",
                       {"started_at": "2026-08-12T06:30:00+03:00",
                        "planned_at": "2026-08-12T06:30:00+03:00",
                        "targets": ["obj_home", "obj_type"]})
    stats = ledger.append(root, out)
    assert stats["new_runs"] == []
    assert stats["pending_runs"] == ["2026-08-12-0630"]
    assert "2026-08-12-0630" not in ledger.load_index(out)["runs"]


def test_a_run_deferred_while_writing_is_picked_up_when_it_closes(tmp_path):
    """Отложенный прогон не потерян: следующий append берёт его целиком."""
    root = history_copy(tmp_path, ALL_RUNS[:2])
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    core.open_snapshot(root, "2026-08-12-0630",
                       {"started_at": "2026-08-12T06:30:00+03:00",
                        "planned_at": "2026-08-12T06:30:00+03:00",
                        "targets": ["obj_home", "obj_type"]})
    ledger.append(root, out)

    shutil.rmtree(root / "2026-08-12-0630")
    shutil.copytree(HISTORY / "2026-08-12-0630", root / "2026-08-12-0630")
    assert ledger.append(root, out)["new_runs"] == ["2026-08-12-0630"]
    shutil.copytree(HISTORY / ALL_RUNS[3], root / ALL_RUNS[3])
    ledger.append(root, out)

    rows = by_key(ledger.read_nights(out))
    # ночь 12.08 замёрзла снимком СВОЕГО утра, а не вчерашним
    night = rows[("2026-08-12", "obj_home", "Дом 1")]
    assert (night["state"], night["source_run_id"], night["gap_days"]) == (
        "busy", "2026-08-12-0630", 0)
    full = tmp_path / "ledger_full"
    ledger.rebuild(history_copy(tmp_path / "full", ALL_RUNS), full)
    assert rows == by_key(ledger.read_nights(full))


def test_rebuild_also_leaves_a_run_that_is_still_being_written_alone(tmp_path):
    root = history_copy(tmp_path, ALL_RUNS[:2])
    core.open_snapshot(root, "2026-08-12-0630",
                       {"started_at": "2026-08-12T06:30:00+03:00",
                        "planned_at": "2026-08-12T06:30:00+03:00",
                        "targets": ["obj_home"]})
    out = tmp_path / "ledger"
    stats = ledger.rebuild(root, out)
    assert stats["pending_runs"] == ["2026-08-12-0630"]
    assert stats["run_ids"] == ALL_RUNS[:2]
    assert sorted({r["night"] for r in ledger.read_nights(out)}) == ["2026-08-10"]


def test_run_killed_midway_is_read_once_the_next_run_lands(tmp_path):
    """Убитый таймаутом прогон не откладывается вечно: он уже не вырастет.

    Признак — появление СЛЕДУЮЩЕГО прогона: пока он на диске, предыдущий
    каталог никто не дописывает, и то, что в нём есть, — всё, что будет.
    """
    root = history_copy(tmp_path, ALL_RUNS[:2])
    killed = core.open_snapshot(root, "2026-08-12-0630",
                                {"started_at": "2026-08-12T06:30:00+03:00",
                                 "planned_at": "2026-08-12T06:30:00+03:00",
                                 "targets": ["obj_home", "obj_type"]})
    core.append_object(killed, json.loads(
        (HISTORY / "2026-08-12-0630" / "obj_home.json").read_text("utf-8")))
    shutil.copytree(HISTORY / ALL_RUNS[3], root / ALL_RUNS[3])

    out = tmp_path / "ledger"
    stats = ledger.rebuild(root, out)
    assert stats["pending_runs"] == []
    assert "2026-08-12-0630" in stats["run_ids"]
    rows = by_key(ledger.read_nights(out))
    # obj_home прогон снять успел — ночь 12.08 замёрзла его снимком
    assert rows[("2026-08-12", "obj_home", "Дом 1")]["source_run_id"] == \
        "2026-08-12-0630"
    # obj_type он снять не успел, и ночь честно несёт опоздание
    assert rows[("2026-08-12", "obj_type", "А-фрейм")]["gap_days"] == 1


def test_legacy_run_without_planned_at_counts_as_finished(tmp_path):
    """Прогоны ДО тикета 02 не несут finished_at — выкидывать их нельзя.

    Все 38 боевых снапшотов (17.08-04.09.2026) написаны старым писателем: у
    них нет ни `finished_at`, ни `planned_at` — run.json писался ОДНИМ куском
    после съёма. Отсутствие `planned_at` и есть отличие такого прогона от
    открытого нынешним cli.py (он планом прогона run.json и открывает).
    """
    root = history_copy(tmp_path, ALL_RUNS[:2])
    for run_id in ALL_RUNS[:2]:
        path = root / run_id / "run.json"
        run = json.loads(path.read_text("utf-8"))
        run.pop("finished_at", None)
        path.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "ledger"
    stats = ledger.rebuild(root, out)
    assert stats["pending_runs"] == []
    assert sorted({r["night"] for r in ledger.read_nights(out)}) == ["2026-08-10"]


def test_backdated_run_is_recorded_once_and_asks_for_a_rebuild(tmp_path):
    """Прогон, появившийся задним числом, не исчезает молча.

    Его ночи уже заморожены, дозаписать его нечем — но дыра обязана быть
    видна: она пишется в индекс (один раз) и поднимает флаг «нужен пересбор».
    """
    root = history_copy(tmp_path, ALL_RUNS[1:])
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)

    shutil.copytree(HISTORY / ALL_RUNS[0], root / ALL_RUNS[0])
    stats = ledger.append(root, out)
    assert stats["skipped_stale"] == [ALL_RUNS[0]]
    assert stats["needs_rebuild"] is True
    assert ledger.load_index(out)["stale_runs"] == [ALL_RUNS[0]]

    again = ledger.append(root, out)
    assert again["skipped_stale"] == []          # второй раз не переоткрывается
    assert again["stale_runs"] == [ALL_RUNS[0]]  # но дыра всё ещё названа
    assert again["needs_rebuild"] is True


def test_append_without_new_runs_changes_nothing(tmp_path):
    root = history_copy(tmp_path)
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    before = (out / ledger.NIGHTS_FILE).read_bytes()
    stats = ledger.append(root, out)
    assert stats["new_runs"] == []
    assert (out / ledger.NIGHTS_FILE).read_bytes() == before


def test_append_on_empty_dir_builds_from_scratch(tmp_path):
    out = tmp_path / "ledger"
    ledger.append(history_copy(tmp_path), out)
    assert len(ledger.read_nights(out)) == 12


def test_index_records_processed_runs(tmp_path):
    out = tmp_path / "ledger"
    ledger.rebuild(history_copy(tmp_path), out)
    index = json.loads((out / ledger.INDEX_FILE).read_text(encoding="utf-8"))
    assert index["runs"] == ALL_RUNS
    assert index["last_run_id"] == ALL_RUNS[-1]
    assert index["frozen_through"] == "2026-08-12"


def test_rebuild_upto_cuts_history(tmp_path):
    out = tmp_path / "ledger"
    ledger.rebuild(history_copy(tmp_path), out, upto="2026-08-11-0630")
    assert sorted({r["night"] for r in ledger.read_nights(out)}) == ["2026-08-10"]


def test_files_are_gzip_jsonl(tmp_path):
    out = tmp_path / "ledger"
    ledger.rebuild(history_copy(tmp_path), out)
    with gzip.open(out / ledger.NIGHTS_FILE, "rt", encoding="utf-8") as fh:
        first = json.loads(fh.readline())
    assert first["night"] == "2026-08-10"


# ---------------------------------------------------------------------------
# Факт: realized() и рыночная медиана
# ---------------------------------------------------------------------------

def test_realized_counts_home_nights_and_drops_gap_nights(tmp_path):
    out = tmp_path / "ledger"
    ledger.rebuild(history_copy(tmp_path), out)

    fact = ledger.realized(out_dir=out)
    # 10.08: 4 из 6, 11.08: 2 из 6, 12.08: 4 из 5 (obj_gap выброшен по gap_days)
    assert (fact["busy"], fact["known"]) == (10, 17)
    assert fact["pct"] == 58.8
    assert fact["gap_excluded"] == 1
    assert fact["objects"] == 3

    with_gaps = ledger.realized(out_dir=out, include_gaps=True)
    assert (with_gaps["busy"], with_gaps["known"]) == (11, 18)


def test_object_without_a_denominator_is_not_counted_as_measured(tmp_path):
    """Объект, у которого все ночи «продажи не открыты», не объект с цифрой.

    На живой истории таких двое из двадцати (ok_reka, wood_glamp): считать их
    в «снято N объектов» — обещать данные, которых нет.
    """
    root = tmp_path / "snapshots"
    closed = make_obj("obj_closed",
                      {"Тип": {"2026-08-10": {"state": "sales_not_open"}}})
    core.write_snapshot(root, "2026-08-10-0630",
                        {"started_at": "x", "targets": ["obj_closed", "obj_home"]},
                        [closed, make_obj("obj_home",
                                          {"Дом": {"2026-08-10": {"state": "busy"}}})])
    core.write_snapshot(root, "2026-08-11-0630",
                        {"started_at": "x", "targets": ["obj_home"]},
                        [make_obj("obj_home",
                                  {"Дом": {"2026-08-11": {"state": "free"}}})])
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    fact = ledger.realized(out_dir=out)
    assert fact["objects"] == 1            # цифра есть только у obj_home
    assert fact["objects_with_rows"] == 2  # но в ряду присутствуют оба
    assert fact["sales_not_open"] == 1


def test_realized_filters_by_username_and_month(tmp_path):
    out = tmp_path / "ledger"
    ledger.rebuild(history_copy(tmp_path), out)

    one = ledger.realized(username="obj_type", out_dir=out)
    # 10.08 занято 2 из 3, 11.08 — 1 из 3, 12.08 — 2 из 3
    assert (one["busy"], one["known"]) == (5, 9)
    assert one["objects"] == 1
    assert ledger.realized(month="2026-09", out_dir=out)["known"] == 0
    assert ledger.realized(month="2026-09", out_dir=out)["pct"] is None


def test_realized_splits_weekday_and_weekend(tmp_path):
    """Пятница — выходной; в фикстуре все замороженные ночи будние."""
    out = tmp_path / "ledger"
    ledger.rebuild(history_copy(tmp_path), out)
    fact = ledger.realized(out_dir=out)
    assert fact["cuts"]["weekday"]["known"] == 17
    assert fact["cuts"]["weekend"]["known"] == 0
    assert fact["cuts"]["weekend"]["pct"] is None


def test_realized_counts_a_weekend_night_in_the_weekend_cut(tmp_path):
    """Позитивная проверка разреза: пятница 14.08.2026 — ВЫХОДНОЙ.

    Разрез будни/выходные — главная цифра загородного объекта, и пустая
    выходная корзина фикстуры 10-12.08 (пн-ср) его не проверяет вовсе.
    """
    root = tmp_path / "snapshots"
    core.write_snapshot(root, "2026-08-14-0630",
                        {"started_at": "x", "targets": ["obj_home"]},
                        [make_obj("obj_home",
                                  {"Дом 1": {"2026-08-14": {"state": "busy"}},
                                   "Дом 2": {"2026-08-14": {"state": "free",
                                                            "price": 9000}}})])
    core.write_snapshot(root, "2026-08-15-0630",
                        {"started_at": "x", "targets": ["obj_home"]},
                        [make_obj("obj_home",
                                  {"Дом 1": {"2026-08-15": {"state": "free"}},
                                   "Дом 2": {"2026-08-15": {"state": "free"}}})])
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    fact = ledger.realized(out_dir=out)
    assert fact["cuts"]["weekend"]["known"] == 2
    assert fact["cuts"]["weekend"]["busy"] == 1
    assert fact["cuts"]["weekend"]["pct"] == 50.0
    assert fact["cuts"]["weekday"]["known"] == 0
    assert fact["cuts"]["all"]["known"] == 2


def test_realized_counts_unknown_and_sales_not_open_apart(tmp_path):
    out = tmp_path / "ledger"
    ledger.rebuild(history_copy(tmp_path), out)
    fact = ledger.realized(username="obj_home", out_dir=out)
    # ночь 14.08 у «Дом 2» была unknown, но она ещё не заморожена;
    # в замороженном окне неизвестных нет
    assert fact["unknown"] == 0 and fact["sales_not_open"] == 0
    assert fact["known"] == 6


def test_realized_by_region_reads_targets(tmp_path):
    out = tmp_path / "ledger"
    ledger.rebuild(history_copy(tmp_path), out)
    targets = [{"username": "obj_home", "region": "samara"},
               {"username": "obj_type", "region": "msk3h"},
               {"username": "obj_gap", "region": "msk3h"}]
    fact = ledger.realized(region="samara", out_dir=out, targets=targets)
    assert fact["objects"] == 1
    assert (fact["busy"], fact["known"]) == (4, 6)


def test_realized_region_without_field_in_targets_is_honest_error(tmp_path):
    out = tmp_path / "ledger"
    ledger.rebuild(history_copy(tmp_path), out)
    with pytest.raises(ledger.LedgerError) as e:
        ledger.realized(region="samara", out_dir=out,
                        targets=[{"username": "obj_home"}])
    assert "region" in str(e.value)


def test_realized_region_with_a_target_without_region_is_honest_error(tmp_path):
    """Реестр заполнен наполовину — это `insufficient_data`, а не нули.

    Тикет 12 проставляет region самарским целям; пока подмосковные без поля,
    разрез обязан назвать, у кого поля нет, а не молча отдать пустую цифру.
    """
    out = tmp_path / "ledger"
    ledger.rebuild(history_copy(tmp_path), out)
    with pytest.raises(ledger.LedgerError) as e:
        ledger.realized(region="samara", out_dir=out,
                        targets=[{"username": "obj_home", "region": "samara"},
                                 {"username": "obj_type"}])
    assert "region" in str(e.value) and "obj_type" in str(e.value)


def test_market_median_skips_gap_nights(tmp_path):
    out = tmp_path / "ledger"
    ledger.rebuild(history_copy(tmp_path), out)

    med = ledger.market_median(out_dir=out)
    # obj_gap 1/2=50.0 (ночь 12.08 выброшена), obj_home 4/6=66.7, obj_type 5/9=55.6
    assert [o["pct"] for o in med["per_object"]] == [50.0, 66.7, 55.6]
    assert med["median_pct"] == 55.6
    # с опоздавшей ночью obj_gap стал бы 2/3 — медиана поехала бы вверх
    with_gaps = ledger.market_median(out_dir=out, include_gaps=True)
    assert with_gaps["median_pct"] == 66.7


def test_aggregator_quota_is_not_mixed_into_the_fact(tmp_path):
    root = tmp_path / "snapshots"
    quota = make_obj("obj_quota", {"__aggregate__": {"2026-08-10": {"state": "busy"}}})
    quota["source_kind"] = "aggregator_quota"
    quota["granularity"] = "aggregate"
    core.write_snapshot(root, "2026-08-10-0630",
                        {"started_at": "x", "targets": ["obj_quota", "obj_home"]},
                        [quota, make_obj("obj_home",
                                         {"Дом": {"2026-08-10": {"state": "free"}}})])
    core.write_snapshot(root, "2026-08-11-0630",
                        {"started_at": "x", "targets": ["obj_home"]},
                        [make_obj("obj_home",
                                  {"Дом": {"2026-08-11": {"state": "free"}}})])
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    assert len(ledger.read_nights(out)) == 2      # строка квоты в ряду есть
    fact = ledger.realized(out_dir=out)
    assert (fact["busy"], fact["known"]) == (0, 1)   # но в знаменатель не идёт
    assert fact["quota_excluded"] == 1


# ---------------------------------------------------------------------------
# Темп бронирования
# ---------------------------------------------------------------------------

def test_pace_rows_are_thinned_to_lead_steps(tmp_path):
    out = tmp_path / "ledger"
    ledger.rebuild(history_copy(tmp_path), out)
    rows = ledger.read_pace(out)
    assert {r["lead_days"] for r in rows} <= set(ledger.LEAD_STEPS)
    # lead 3 снимается каждым прогоном: 10.08 -> ночь 13.08, 11.08 -> 14.08 и т.д.
    lead3 = [r for r in rows if r["lead_days"] == 3]
    assert {r["night"] for r in lead3} == {"2026-08-13", "2026-08-14",
                                           "2026-08-15", "2026-08-16"}


def test_read_pace_keeps_duplicates_and_the_curve_folds_them(tmp_path):
    """Два прогона за сутки пишут ОДНУ точку кривой дважды — это журнал.

    На живой истории (38 прогонов, из них 12 за 14.08) пошаговый append дал
    40562 строки темпа против 39878 уникальных ключей. `read_pace` отдаёт их
    как есть, а `pace_curve` считает каждую пару «ночь-юнит-lead» один раз —
    иначе знаменатель дня с двумя прогонами удваивается.
    """
    root = tmp_path / "snapshots"
    core.write_snapshot(root, "2026-08-10-0630",
                        {"started_at": "x", "targets": ["obj_home"]},
                        [make_obj("obj_home",
                                  {"Дом": {"2026-08-10": {"state": "free"},
                                           "2026-08-11": {"state": "busy"}}})])
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    core.write_snapshot(root, "2026-08-10-1830",
                        {"started_at": "x", "targets": ["obj_home"]},
                        [make_obj("obj_home",
                                  {"Дом": {"2026-08-10": {"state": "busy"},
                                           "2026-08-11": {"state": "busy"}}})])
    ledger.append(root, out)

    rows = ledger.read_pace(out)
    lead1 = [r for r in rows if r["lead_days"] == 1]
    assert len(lead1) == 2                      # одна точка, две записи
    curve = {p["lead_days"]: p for p in ledger.pace_curve("obj_home", "2026-08",
                                                          out_dir=out)}
    assert (curve[1]["busy"], curve[1]["known"]) == (1, 1)


def test_pace_curve_shows_how_a_month_fills_up(tmp_path):
    out = tmp_path / "ledger"
    ledger.rebuild(history_copy(tmp_path), out)
    curve = {p["lead_days"]: p for p in ledger.pace_curve("obj_home", "2026-08",
                                                          out_dir=out)}
    # за сутки до ночи занято 2 клетки из 8, в само утро ночи — 5 из 8
    assert (curve[1]["busy"], curve[1]["known"]) == (2, 8)
    assert curve[1]["pct"] == 25.0
    assert (curve[0]["busy"], curve[0]["known"], curve[0]["pct"]) == (5, 8, 62.5)
    assert curve[0]["nights"] == 4


def test_pace_series_follows_one_night(tmp_path):
    out = tmp_path / "ledger"
    ledger.rebuild(history_copy(tmp_path), out)
    series = ledger.pace_series("obj_home", "2026-08-14", out_dir=out)
    # ночь 14.08 попала в шаги кривой на lead 3, 2 и 1 (lead 4 — не шаг);
    # точки идут по возрастанию lead, то есть ПРОТИВ хода времени
    assert [p["lead_days"] for p in series] == [1, 2, 3]
    assert [p["busy"] for p in series] == [1, 1, 0]


def test_pace_curve_ignores_unknown_cells(tmp_path):
    root = tmp_path / "snapshots"
    units = {"Дом": {"2026-08-10": {"state": "unknown"},
                     "2026-08-11": {"state": "busy"}}}
    core.write_snapshot(root, "2026-08-10-0630",
                        {"started_at": "x", "targets": ["obj_home"]},
                        [make_obj("obj_home", units)])
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    curve = {p["lead_days"]: p for p in ledger.pace_curve("obj_home", "2026-08",
                                                          out_dir=out)}
    assert curve[0]["known"] == 0 and curve[0]["pct"] is None
    assert curve[0]["unknown"] == 1
    assert (curve[1]["busy"], curve[1]["known"]) == (1, 1)


# ---------------------------------------------------------------------------
# Пустые и битые входы
# ---------------------------------------------------------------------------

def test_rebuild_on_empty_root_writes_empty_layer(tmp_path):
    out = tmp_path / "ledger"
    stats = ledger.rebuild(tmp_path / "nothing", out)
    assert stats["nights_rows"] == 0
    assert ledger.read_nights(out) == []
    assert ledger.realized(out_dir=out)["pct"] is None


def test_read_nights_without_layer_is_honest_error(tmp_path):
    with pytest.raises(ledger.LedgerError) as e:
        ledger.read_nights(tmp_path / "ledger")
    assert "nights" in str(e.value)


# ---------------------------------------------------------------------------
# Горизонт фонда: точки кривой по разные стороны границы несопоставимы
# ---------------------------------------------------------------------------

FUND_NIGHT = "2026-11-08"
# прогоны, в которых ночь 08.11 попадает ровно на шаги кривой 90/60/45/0
FUND_RUNS = {"2026-08-10-0630": 90, "2026-09-09-0630": 60,
             "2026-09-24-0630": 45, "2026-11-08-0630": 0}


def fund_history(root, fund_horizon=45):
    """Тип из ТРЁХ домиков, продан ровно один, и так все четыре прогона.

    Фонд снимается только на `fund_horizon` ночей вперёд (тикет 06): дальше
    движок отдаёт «категория продаётся» без остатков, и клетка весит один
    домик. Занятость ночи при этом не меняется НИ РАЗУ — любое движение
    кривой на такой истории берётся из знаменателя, а не из продаж.
    """
    for run_id, lead in FUND_RUNS.items():
        run_date = date.fromisoformat(run_id[:10])
        until = run_date + timedelta(days=fund_horizon)
        known = date.fromisoformat(FUND_NIGHT) <= until
        cell = ({"state": "free", "units_total": 3, "units_free": 2} if known
                else {"state": "free"})
        core.write_snapshot(
            root, run_id, {"started_at": f"{run_id[:10]}T06:30:00+03:00",
                           "targets": ["obj_fund"]},
            [make_obj("obj_fund", {"А-фрейм": {FUND_NIGHT: cell}},
                      engine="travelline", inventory_until=until.isoformat())])
    return root


def test_pace_point_beyond_the_fund_horizon_is_not_counted_as_sales(tmp_path):
    """За горизонтом фонда точка не считается: там другой знаменатель.

    Ревью волны 2 воспроизвело подмену на этой самой истории: lead 90 -> 0
    из 1 = 0.0%, lead 45 -> 1 из 3 = 33.3%. Кривая показывала рост темпа с
    нуля до трети на ночи, где не забронировали ничего. Занижение
    систематическое: ранний спрос всегда делится на один домик вместо трёх.
    """
    root = fund_history(tmp_path / "snapshots")
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    curve = {p["lead_days"]: p for p in
             ledger.pace_curve("obj_fund", "2026-11", out_dir=out)}

    assert sorted(curve) == [0, 45, 60, 90]
    for lead in (60, 90):
        assert curve[lead]["known"] == 0, lead
        assert curve[lead]["pct"] is None, lead       # было 0.0 — «продаж нет»
        assert curve[lead]["inventory_missing"] == 1, lead
    for lead in (0, 45):
        assert (curve[lead]["busy"], curve[lead]["known"]) == (1, 3), lead
        assert curve[lead]["pct"] == 33.3, lead
        assert curve[lead]["inventory_missing"] == 0, lead


def test_pace_point_carries_its_own_basis(tmp_path):
    """Основание точки видно в самой точке, а не выводится читателем."""
    root = fund_history(tmp_path / "snapshots")
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    rows = ledger.read_pace(out)
    flags = {r["lead_days"]: r["inventory_known"] for r in rows}
    assert flags == {90: False, 60: False, 45: True, 0: True}


def test_pace_ignores_the_fund_horizon_when_the_run_did_not_record_it(tmp_path):
    """Слой, собранный ДО тикета 06, читается как раньше.

    В 38 боевых снапшотах поля `inventory_until` нет вовсе: фонд тогда
    спрашивался на всю сетку. Отсутствие поля — «не знаем», и выкидывать по
    нему точки значило бы стереть всю снятую историю.
    """
    root = tmp_path / "snapshots"
    core.write_snapshot(root, "2026-08-10-0630",
                        {"started_at": "x", "targets": ["obj_type"]},
                        [make_obj("obj_type",
                                  {"А-фрейм": {"2026-08-11": {
                                      "state": "free", "units_total": 3,
                                      "units_free": 2}}},
                                  engine="travelline")])
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    row = ledger.read_pace(out)[0]
    assert row["inventory_known"] is None
    curve = {p["lead_days"]: p for p in
             ledger.pace_curve("obj_type", "2026-08", out_dir=out)}
    assert (curve[1]["busy"], curve[1]["known"], curve[1]["pct"]) == (1, 3, 33.3)


# ---------------------------------------------------------------------------
# Точки кривой считаются по РАЗНЫМ наборам ночей — это обязано быть видно
# ---------------------------------------------------------------------------

def pace_row(night, lead, *, busy=False, username="obj_home", unit="Дом"):
    return {"night": night, "username": username, "unit": unit,
            "lead_days": lead, "state": "busy" if busy else "free",
            "units_total": 1, "units_free": 0 if busy else 1,
            "run_id": "2026-09-01-0630"}


def test_pace_point_built_on_a_handful_of_nights_does_not_give_a_pct():
    """Точка на одной ночи из четырёх — не темп продаж, а выборка.

    Ревью волны 2 на боевом слое: объект-референс, сентябрь — lead 21 46.7%
    (20 ночей), lead 30 45.9% (16), lead 45 6.1% (ОДНА ночь), lead 0 47.6%
    (4 ночи). Обрыв с 46% до 6% в отчёте читался как «за полтора месяца до
    заезда продаж нет», хотя это одна ночь в выборке.
    """
    rows = [pace_row(f"2026-09-0{d}", 0, busy=True) for d in (1, 2, 3, 4)]
    rows.append(pace_row("2026-09-01", 45))
    curve = {p["lead_days"]: p for p in
             ledger.pace_curve("obj_home", "2026-09", rows=rows)}

    assert curve[0]["nights"] == 4 and curve[0]["pct"] == 100.0
    assert curve[0]["partial"] is False and curve[0]["coverage"] == 1.0
    thin = curve[45]
    assert thin["nights"] == 1 and thin["nights_base"] == 4
    assert thin["partial"] is True
    assert thin["pct"] is None          # в отчёт эта цифра не попадёт
    assert thin["pct_partial"] == 0.0   # но и не пропадёт из разбора


def test_pace_coverage_threshold_is_the_callers_to_move():
    rows = [pace_row(f"2026-09-0{d}", 0, busy=True) for d in (1, 2, 3, 4)]
    rows.append(pace_row("2026-09-01", 45))
    curve = {p["lead_days"]: p for p in
             ledger.pace_curve("obj_home", "2026-09", rows=rows,
                               min_coverage=0.0)}
    assert curve[45]["pct"] == 0.0 and curve[45]["partial"] is True


def test_pace_series_of_one_night_is_never_partial():
    """У одной ночи набор ночей один и тот же во всех точках."""
    rows = [pace_row("2026-09-01", 0, busy=True), pace_row("2026-09-01", 45)]
    series = ledger.pace_series("obj_home", "2026-09-01", rows=rows)
    assert [p["partial"] for p in series] == [False, False]
    assert [p["pct"] for p in series] == [100.0, 0.0]


# ---------------------------------------------------------------------------
# Целостность слоя: дозапись не чинит порчу, она её удваивает
# ---------------------------------------------------------------------------

def grow_history(root):
    """Дописать в историю ещё один прогон (14.08) — повод для append."""
    core.write_snapshot(root, "2026-08-14-0630",
                        {"started_at": "2026-08-14T06:30:00+03:00",
                         "targets": ["obj_home"]},
                        [make_obj("obj_home",
                                  {"Дом 1": {"2026-08-14": {"state": "busy"}},
                                   "Дом 2": {"2026-08-14": {"state": "free"}}})])


def test_append_refuses_when_the_row_file_disappeared(tmp_path):
    """Пропавший ряд при живом индексе — порча, а не пустой слой.

    Ветка дозаписи открывала файл на 'ab' и молча создавала его заново: на
    боевой истории (38 прогонов) слой съезжал с 3057 ночей и 65.4% до 157
    ночей и 39.1%, а индекс продолжал рапортовать 3214 строки и
    needs_rebuild=False. Узнать об этом было неоткуда.
    """
    root = history_copy(tmp_path)
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    (out / ledger.NIGHTS_FILE).unlink()
    grow_history(root)

    with pytest.raises(ledger.LedgerError) as e:
        ledger.append(root, out)
    assert ledger.NIGHTS_FILE in str(e.value)
    assert "rebuild" in str(e.value)


def test_append_refuses_when_the_row_file_lost_rows(tmp_path):
    """Файл на месте, но строк в нём меньше, чем обещает индекс."""
    root = history_copy(tmp_path)
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    rows = ledger.read_nights(out)
    with gzip.open(out / ledger.NIGHTS_FILE, "wt", encoding="utf-8") as fh:
        for row in rows[:-1]:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    grow_history(root)

    with pytest.raises(ledger.LedgerError) as e:
        ledger.append(root, out)
    assert "11" in str(e.value) and "12" in str(e.value)


def test_rebuild_heals_a_broken_layer(tmp_path):
    """Порча лечится пересбором: источник истины — снапшоты."""
    root = history_copy(tmp_path)
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    (out / ledger.NIGHTS_FILE).unlink()
    ledger.rebuild(root, out)
    assert len(ledger.read_nights(out)) == 12
    assert ledger.verify(out)["ok"] is True


def test_verify_reconciles_the_index_with_the_files(tmp_path):
    root = history_copy(tmp_path)
    out = tmp_path / "ledger"
    stats = ledger.rebuild(root, out)
    report = ledger.verify(out)
    assert report["ok"] is True and report["problems"] == []
    assert report["nights_rows"] == stats["nights_rows"]
    assert report["pace_rows"] == stats["pace_rows"]

    (out / ledger.PACE_FILE).unlink()
    broken = ledger.verify(out)
    assert broken["ok"] is False
    assert any(ledger.PACE_FILE in p for p in broken["problems"])


def test_verify_without_a_layer_says_so(tmp_path):
    report = ledger.verify(tmp_path / "nothing")
    assert report["ok"] is False
    assert report["index"] is None


def test_pace_rows_in_the_index_match_the_file(tmp_path):
    """Счётчик темпа = строк в ФАЙЛЕ, поэтому сверка с файлом возможна.

    В файле лежат дубли (два прогона за сутки пишут одну точку дважды), и
    именно поэтому счётчик у слоя, собранного дозаписью, больше, чем у
    пересобранного. Сверять его с файлом это не мешает — мешало бы
    обратное.
    """
    root = history_copy(tmp_path, ALL_RUNS[:2])
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    shutil.copytree(HISTORY / ALL_RUNS[2], root / ALL_RUNS[2])
    ledger.append(root, out)
    index = ledger.load_index(out)
    assert index["pace_rows"] == len(ledger.read_pace(out))
    assert index["nights_rows"] == len(ledger.read_nights(out))


# ---------------------------------------------------------------------------
# Оборванный gzip: verify обязана его НАЗВАТЬ, а не упасть на нём
# ---------------------------------------------------------------------------

def truncate_member(path: Path) -> None:
    """Обрубить хвост gzip-члена — след прерванной дозаписи слоя.

    Ровно это оставляет SIGTERM по TimeoutStartSec в хвосте планового
    прогона: член начат, конца у него нет.
    """
    blob = path.read_bytes()
    path.write_bytes(blob[:len(blob) - 6])


def test_verify_names_the_truncated_gzip_instead_of_dying_on_it(tmp_path):
    """Порча, которую verify обязана обнаруживать, не смеет её же ронять.

    `_count_rows` читал файл потоком и отдавал zlib.error/EOFError мимо
    контракта: вызывающий (в т.ч. `_require_intact` перед дозаписью) получал
    не отчёт и не LedgerError, а сырое исключение стандартной библиотеки.
    """
    root = history_copy(tmp_path)
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    truncate_member(out / ledger.NIGHTS_FILE)

    report = ledger.verify(out)
    assert report["ok"] is False
    assert any(ledger.NIGHTS_FILE in p and "rebuild" in p
               for p in report["problems"]), report["problems"]
    assert any("повреж" in p for p in report["problems"]), report["problems"]


def test_append_to_a_truncated_layer_raises_the_contract_error(tmp_path):
    """Дозапись в оборванный файл — LedgerError, а не zlib.error наружу."""
    root = history_copy(tmp_path)
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    truncate_member(out / ledger.NIGHTS_FILE)
    grow_history(root)

    with pytest.raises(ledger.LedgerError) as e:
        ledger.append(root, out)
    assert ledger.NIGHTS_FILE in str(e.value)


def test_verify_names_a_garbage_file_too(tmp_path):
    """Не-gzip на месте файла слоя — та же честная строка, а не BadGzipFile."""
    root = history_copy(tmp_path)
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    (out / ledger.PACE_FILE).write_bytes("это не gzip".encode("utf-8"))

    report = ledger.verify(out)
    assert report["ok"] is False
    assert any(ledger.PACE_FILE in p for p in report["problems"])


# ---------------------------------------------------------------------------
# Дыры слоя обязаны доходить до человека
# ---------------------------------------------------------------------------

def test_append_note_names_the_hole_in_words(tmp_path):
    root = history_copy(tmp_path, ALL_RUNS[1:])
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    shutil.copytree(HISTORY / ALL_RUNS[0], root / ALL_RUNS[0])
    stats = ledger.append(root, out)

    note = ledger.append_note(stats)
    assert ALL_RUNS[0] in note
    assert "пересбор" in note.lower()


def test_append_note_of_a_quiet_day_is_one_line(tmp_path):
    out = tmp_path / "ledger"
    stats = ledger.append(history_copy(tmp_path), out)
    note = ledger.append_note(stats)
    assert "\n" not in note
    assert "пересбор" not in note.lower()


def test_append_returns_the_ready_note_so_nobody_retells_it(tmp_path):
    """Готовая фраза едет в статистике: пересказ у вызывающего терял дыру.

    `run_scheduled.append_ledger` печатал СВОЮ копию первой половины фразы —
    счётчики без предупреждения о пересборе, — и прогон, появившийся задним
    числом, выглядел в журнале как обычный тихий день.
    """
    root = history_copy(tmp_path, ALL_RUNS[1:])
    out = tmp_path / "ledger"
    stats = ledger.rebuild(root, out)
    assert stats["note"] == ledger.append_note(stats)

    shutil.copytree(HISTORY / ALL_RUNS[0], root / ALL_RUNS[0])
    stats = ledger.append(root, out)
    assert stats["note"] == ledger.append_note(stats)
    assert ALL_RUNS[0] in stats["note"] and "пересбор" in stats["note"].lower()


def test_hole_note_of_a_healthy_index_is_empty(tmp_path):
    """Пометка обесценится, если её ставить всем подряд: чисто — пусто."""
    root = history_copy(tmp_path)
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    assert ledger.hole_note(ledger.load_index(out)) == ""
    assert ledger.hole_note(None) == ""


def test_hole_note_names_the_missed_runs_and_the_cure(tmp_path):
    """Дыра индекса пересказывается одной фразой — её печатает и отчёт."""
    root = history_copy(tmp_path, ALL_RUNS[1:])
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    shutil.copytree(HISTORY / ALL_RUNS[0], root / ALL_RUNS[0])
    ledger.append(root, out)

    note = ledger.hole_note(ledger.load_index(out))
    assert ALL_RUNS[0] in note
    assert "rebuild" in note
    assert "\n" not in note


def test_hole_note_catches_the_index_promising_more_rows_than_read(tmp_path):
    """Читатель слоя уже знает, сколько строк прочёл, — сверка ему бесплатна.

    Именно этим расхождением жила молчащая порча: индекс рапортовал 3214
    строки, а в файле их оставалось 157.
    """
    root = history_copy(tmp_path)
    out = tmp_path / "ledger"
    ledger.rebuild(root, out)
    index = ledger.load_index(out)

    note = ledger.hole_note(index, nights_read=len(ledger.read_nights(out)),
                            pace_read=len(ledger.read_pace(out)))
    assert note == ""
    note = ledger.hole_note(index, nights_read=157)
    assert "157" in note and str(index["nights_rows"]) in note
    assert "rebuild" in note


# ---------------------------------------------------------------------------
# Разрез по региону: пока поля нет — человеческое «недоступно», а не нули
# ---------------------------------------------------------------------------

def test_region_coverage_tells_whether_the_cut_is_available():
    ready = ledger.region_coverage([{"username": "a", "region": "samara"},
                                    {"username": "b", "region": "msk3h"}])
    assert ready["ok"] is True
    assert ready["regions"] == {"samara": 1, "msk3h": 1}
    assert ready["missing"] == []

    half = ledger.region_coverage([{"username": "a", "region": "samara"},
                                   {"username": "b"}])
    assert half["ok"] is False
    assert half["missing"] == ["b"]
    assert "b" in half["note"] and "region" in half["note"]


def test_region_error_says_what_to_do_without_ticket_jargon(tmp_path):
    out = tmp_path / "ledger"
    ledger.rebuild(history_copy(tmp_path), out)
    with pytest.raises(ledger.LedgerError) as e:
        ledger.realized(region="samara", out_dir=out,
                        targets=[{"username": "obj_home"}])
    text = str(e.value)
    assert "targets.json" in text and "region" in text
    assert "тикет" not in text.lower()


def test_everything_but_the_region_cut_works_without_the_field(tmp_path):
    """Отсутствие region гасит ТОЛЬКО региональный разрез (контракт волны 3)."""
    out = tmp_path / "ledger"
    ledger.rebuild(history_copy(tmp_path), out)
    assert ledger.realized(out_dir=out, targets=[{"username": "obj_home"}])[
        "pct"] == 58.8
    assert ledger.market_median(out_dir=out,
                                targets=[{"username": "obj_home"}])[
        "median_pct"] == 55.6


def make_obj(username, units, engine="homereserve", inventory_until=None):
    obj = {
        "username": username,
        "site": f"https://example.com/{username}",
        "engine": engine,
        "source_kind": "module",
        "granularity": "per_unit",
        "units": units,
        "checked_at": "2026-08-10T06:30:00+03:00",
        "status": "ok",
        "reason": "",
        "source_urls": [f"https://example.com/{username}/widget"],
    }
    if inventory_until is not None:
        # докуда прогон спрашивал ФОНД (тикет 06): дальше клетка весит один
        # домик, и точка кривой по ней несопоставима с ближними
        obj["inventory_until"] = inventory_until
    return obj
