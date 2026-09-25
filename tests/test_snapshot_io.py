"""Снапшот прогона на диске: каталог, run.json, файл на объект, поиск предыдущего."""
import json

import pytest

import occupancy_core as core


def make_obj(username="obj_a"):
    return {
        "username": username,
        "site": "https://example.com",
        "engine": "bnovo",
        "source_kind": "module",
        "granularity": "aggregate",
        "units": {"__aggregate__": {"2026-08-03": {"state": "busy", "price": 9500}}},
        "checked_at": "2026-08-14T10:00:00+03:00",
        "status": "ok",
        "reason": "",
        "source_urls": ["https://example.com/booking"],
    }


def test_write_read_roundtrip(tmp_path):
    root = tmp_path / "snapshots"
    objs = [make_obj("obj_a"), make_obj("obj_b")]
    run_meta = {"started_at": "2026-08-14T10:00:00+03:00", "targets": ["obj_a", "obj_b"]}
    path = core.write_snapshot(root, "2026-08-14-1000", run_meta, objs)

    assert (path / "run.json").is_file()
    assert (path / "obj_a.json").is_file()
    assert (path / "obj_b.json").is_file()

    snap = core.read_snapshot(path)
    assert snap["run"]["schema_version"] == core.SCHEMA_VERSION
    assert snap["run"]["run_id"] == "2026-08-14-1000"
    assert snap["run"]["targets"] == ["obj_a", "obj_b"]
    assert snap["objects"]["obj_a"] == make_obj("obj_a")


def test_find_previous_snapshot_picks_latest_earlier(tmp_path):
    root = tmp_path / "snapshots"
    meta = {"started_at": "x", "targets": []}
    for run_id in ("2026-08-12-0900", "2026-08-13-0900", "2026-08-14-1200"):
        core.write_snapshot(root, run_id, meta, [make_obj()])
    prev = core.find_previous_snapshot(root, "2026-08-14-1200")
    assert prev is not None and prev.name == "2026-08-13-0900"
    assert core.find_previous_snapshot(root, "2026-08-12-0900") is None


def test_write_rejects_invalid_state(tmp_path):
    obj = make_obj()
    obj["units"]["__aggregate__"]["2026-08-04"] = {"state": "occupied"}
    with pytest.raises(core.SnapshotError) as e:
        core.write_snapshot(tmp_path, "2026-08-14-1000",
                            {"started_at": "x", "targets": []}, [obj])
    assert "occupied" in str(e.value)


def test_read_broken_object_json_is_clear_error(tmp_path):
    path = core.write_snapshot(tmp_path, "2026-08-14-1000",
                               {"started_at": "x", "targets": []}, [make_obj()])
    (path / "obj_a.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(core.SnapshotError) as e:
        core.read_snapshot(path)
    assert "obj_a.json" in str(e.value)
    assert not isinstance(e.value, json.JSONDecodeError)


# ---------------------------------------------------------------------------
# Фонд типа в клетке: пара обязательна, числа осмысленны, state не спорит
# с остатком (иначе метрика молча разъедется с сеткой)
# ---------------------------------------------------------------------------

def _obj_with_cell(cell):
    return {"username": "obj", "site": "", "engine": "travelline",
            "source_kind": "module", "granularity": "per_unit",
            "units": {"А-фрейм": {"2026-08-03": cell}},
            "status": "ok", "reason": "", "source_urls": []}


def test_capacity_pair_is_valid():
    core.validate_object(_obj_with_cell(
        {"state": "free", "units_total": 3, "units_free": 1}))
    core.validate_object(_obj_with_cell(
        {"state": "busy", "units_total": 3, "units_free": 0}))
    core.validate_object(_obj_with_cell({"state": "free"}))


def test_capacity_errors():
    bad_cells = [
        ({"state": "free", "units_total": 3}, "парой"),
        ({"state": "free", "units_total": 3, "units_free": 5}, "больше"),
        ({"state": "free", "units_total": 0, "units_free": 0}, "без домиков"),
        ({"state": "busy", "units_total": 3, "units_free": 1}, "state=busy"),
        ({"state": "free", "units_total": 3, "units_free": 0}, "state=free"),
        ({"state": "free", "units_total": "три", "units_free": 1}, "целое"),
    ]
    for cell, marker in bad_cells:
        with pytest.raises(core.SnapshotError) as e:
            core.validate_object(_obj_with_cell(cell))
        assert marker in str(e.value)
