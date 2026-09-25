"""CLI fixture-режима: сетка из файла -> снапшот на диске + строка сводки в stdout.

Подставной диспетчер здесь объявляет ПОЛНУЮ сигнатуру probes.run_recipe
(вместе с inventory_date_to): cli зовёт её напрямую, шима, глотающего лишние
аргументы, больше нет — он делал --inventory-days молчаливым no-op.

Правка 04.09.2026 (тикет 01): месяцы сводки по умолчанию — скользящее окно от
СЕГОДНЯШНЕГО месяца, а не зашитые авг-окт 2026. Сетки здесь живут в августе
2026, поэтому месяцы задаются явным --months: тесты про fixture-режим, а не
про горизонт (горизонт проверяет tests/test_horizon.py).
"""
import json
from datetime import date

import pytest

import cli
import occupancy_core as core

MONTHS = ["--months", "2026-08,2026-09,2026-10"]


@pytest.fixture(autouse=True)
def machine_lock_in_tmp(tmp_path, monkeypatch):
    """Замок живого съёма — один на машину; в тестах уводим его в tmp."""
    monkeypatch.setenv(core.HOST_LOCK_ENV, str(tmp_path / ".hosts.lock"))


def fixture_grid(busy_extra=False):
    units = {
        "Дом 1": {
            "2026-08-03": {"state": "busy", "price": 12000},
            "2026-08-04": {"state": "free"},
            "2026-08-07": {"state": "busy"},
            "2026-08-08": {"state": "free"},
            "2026-09-05": {"state": "free"},
            "2026-09-12": {"state": "sales_not_open"},
            "2026-10-03": {"state": "unknown"},
        }
    }
    if busy_extra:
        units["Дом 1"]["2026-08-08"] = {"state": "busy"}
        units["Дом 1"]["2026-09-05"] = {"state": "busy"}
    return {
        "username": "test_obj",
        "site": "https://example.com",
        "engine": "travelline",
        "source_kind": "module",
        "granularity": "per_unit",
        "units": units,
        "source_urls": ["https://example.com/booking"],
    }


def write_fixture(tmp_path, name, grid):
    path = tmp_path / name
    path.write_text(json.dumps(grid, ensure_ascii=False), encoding="utf-8")
    return path


def test_first_run_writes_snapshot_and_prints_summary(tmp_path, capsys):
    fx = write_fixture(tmp_path, "grid.json", fixture_grid())
    root = tmp_path / "snapshots"
    rc = cli.main(["--fixture", str(fx), "--snapshot-root", str(root),
                   "--run-id", "2026-08-14-1000"] + MONTHS)
    assert rc == 0
    assert (root / "2026-08-14-1000" / "run.json").is_file()
    obj = json.loads((root / "2026-08-14-1000" / "test_obj.json").read_text(encoding="utf-8"))
    assert obj["status"] == "ok"
    assert obj["checked_at"]  # дозаполнено CLI

    out = capsys.readouterr().out
    assert "test_obj" in out
    assert "оценка сверху" in out
    assert "50%" in out  # август: 2 busy / 4 known
    assert "неизвестн" in out


def test_second_run_prints_diff(tmp_path, capsys):
    root = tmp_path / "snapshots"
    fx1 = write_fixture(tmp_path, "g1.json", fixture_grid())
    cli.main(["--fixture", str(fx1), "--snapshot-root", str(root),
              "--run-id", "2026-08-14-1000"] + MONTHS)
    capsys.readouterr()

    fx2 = write_fixture(tmp_path, "g2.json", fixture_grid(busy_extra=True))
    rc = cli.main(["--fixture", str(fx2), "--snapshot-root", str(root),
                   "--run-id", "2026-08-14-1100"] + MONTHS)
    assert rc == 0
    out = capsys.readouterr().out
    assert "2026-08-14-1000" in out   # с каким прогоном сравнили
    assert "пп" in out                # сдвиг occupancy
    assert "2026-08-08" in out        # новая занятая дата
    assert "2026-09-05" in out


def test_broken_fixture_json_clear_error(tmp_path, capsys):
    fx = tmp_path / "broken.json"
    fx.write_text("{broken", encoding="utf-8")
    rc = cli.main(["--fixture", str(fx), "--snapshot-root", str(tmp_path / "s"),
                   "--run-id", "2026-08-14-1000"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "broken.json" in err
    assert "Traceback" not in err


def test_invalid_state_clear_error(tmp_path, capsys):
    grid = fixture_grid()
    grid["units"]["Дом 1"]["2026-08-05"] = {"state": "occupied"}
    fx = write_fixture(tmp_path, "bad.json", grid)
    rc = cli.main(["--fixture", str(fx), "--snapshot-root", str(tmp_path / "s"),
                   "--run-id", "2026-08-14-1000"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "occupied" in err
    assert "Traceback" not in err


def test_empty_grid_marked_insufficient_data(tmp_path, capsys):
    grid = fixture_grid()
    grid["units"] = {}
    fx = write_fixture(tmp_path, "empty.json", grid)
    root = tmp_path / "snapshots"
    rc = cli.main(["--fixture", str(fx), "--snapshot-root", str(root),
                   "--run-id", "2026-08-14-1000"])
    assert rc == 0
    obj = json.loads((root / "2026-08-14-1000" / "test_obj.json").read_text(encoding="utf-8"))
    assert obj["status"] == "insufficient_data"
    assert obj["reason"]
    out = capsys.readouterr().out
    assert "insufficient_data" in out


# ---------------------------------------------------------------------------
# Probe-режим (живой съём подменён фейком — сети в тестах нет)
# ---------------------------------------------------------------------------

def probe_object(status="ok", reason=""):
    obj = fixture_grid()
    obj["username"] = "probed_obj"
    obj["checked_at"] = "2026-08-14T18:00:00+03:00"
    obj["status"] = status
    obj["reason"] = reason
    return obj


def write_recipes(tmp_path, status="ok"):
    import occupancy_core as core
    path = tmp_path / "recipes.json"
    core.save_recipes(path, {"probed_obj": {
        "site": "https://example.com",
        "engine": "travelline",
        "status": status,
        "request": {"url_template": "https://x/{date_from}", "method": "GET",
                    "params": {}, "headers": {}, "date_substitution": "iso"},
        "discovered_at": "2026-08-14T10:00:00+03:00",
        "notes": "", "source_urls": [],
    }})
    return path


def test_probe_mode_writes_snapshot(tmp_path, capsys, monkeypatch):
    recipes = write_recipes(tmp_path)
    calls = {}

    def fake_run(username, recipe, date_from, date_to, fetch=None,
                 inventory=True, inventory_date_to=None):
        calls["args"] = (username, date_from.isoformat(), date_to.isoformat())
        calls["inventory_date_to"] = inventory_date_to
        return probe_object(), None

    monkeypatch.setattr(cli.probes, "run_recipe", fake_run)
    root = tmp_path / "snapshots"
    rc = cli.main(["--probe", "probed_obj", "--recipes", str(recipes),
                   "--snapshot-root", str(root), "--run-id", "2026-08-14-1200",
                   "--date-from", "2026-08-14", "--date-to", "2026-10-31"])
    assert rc == 0
    assert calls["args"] == ("probed_obj", "2026-08-14", "2026-10-31")
    # горизонт фонда ближе горизонта сетки — иначе рычаг цены не работает
    assert calls["inventory_date_to"] <= date.fromisoformat("2026-10-31")
    obj = json.loads((root / "2026-08-14-1200" / "probed_obj.json")
                     .read_text(encoding="utf-8"))
    assert obj["status"] == "ok"
    assert "probed_obj" in capsys.readouterr().out


def test_probe_mode_marks_recipe_broken(tmp_path, capsys, monkeypatch):
    import occupancy_core as core
    recipes = write_recipes(tmp_path)

    def fake_run(username, recipe, date_from, date_to, fetch=None,
                 inventory=True, inventory_date_to=None):
        return (probe_object(status="insufficient_data",
                             reason="схема сменилась"), "схема сменилась")

    monkeypatch.setattr(cli.probes, "run_recipe", fake_run)
    rc = cli.main(["--probe", "probed_obj", "--recipes", str(recipes),
                   "--snapshot-root", str(tmp_path / "s"),
                   "--run-id", "2026-08-14-1200"])
    assert rc == 0
    saved = core.load_recipes(recipes)
    assert saved["probed_obj"]["status"] == "broken"
    assert saved["probed_obj"]["broken_reason"] == "схема сменилась"
    assert "broken" in capsys.readouterr().err


def test_probe_mode_missing_recipe_is_honest(tmp_path, capsys):
    recipes = write_recipes(tmp_path)
    root = tmp_path / "snapshots"
    rc = cli.main(["--probe", "no_such_obj", "--recipes", str(recipes),
                   "--snapshot-root", str(root), "--run-id", "2026-08-14-1200"])
    assert rc == 0
    obj = json.loads((root / "2026-08-14-1200" / "no_such_obj.json")
                     .read_text(encoding="utf-8"))
    assert obj["status"] == "insufficient_data"
    assert "рецепта нет" in obj["reason"]
