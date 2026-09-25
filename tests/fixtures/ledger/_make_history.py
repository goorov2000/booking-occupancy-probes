# -*- coding: utf-8 -*-
"""Генератор статической фикстуры ledger/history — гоняется РУКАМИ один раз."""
import shutil
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "occupancy"   # <repo>/occupancy
sys.path.insert(0, str(SCRIPTS))
import occupancy_core as core  # noqa: E402

OUT = Path(__file__).resolve().parent / "history"


def free(price=None, total=None, free_units=None):
    cell = {"state": "free"}
    if price is not None:
        cell["price"] = price
    if total is not None:
        cell["units_total"] = total
        cell["units_free"] = free_units
    return cell


def busy(total=None):
    cell = {"state": "busy"}
    if total is not None:
        cell["units_total"] = total
        cell["units_free"] = 0
    return cell


SNO = {"state": "sales_not_open"}
UNK = {"state": "unknown"}


def obj(username, engine, units, checked_at):
    return {
        "username": username,
        "site": f"https://example.com/{username}",
        "engine": engine,
        "source_kind": "module",
        "granularity": "per_unit",
        "units": units,
        "checked_at": checked_at,
        "status": "ok",
        "reason": "",
        "source_urls": [f"https://example.com/{username}/widget"],
    }


RUNS = {
    "2026-08-10-0630": {
        "obj_home": {
            "Дом 1": {"2026-08-10": busy(), "2026-08-11": free(10000),
                      "2026-08-12": free(10000), "2026-08-13": free(11000),
                      "2026-08-14": free(12000)},
            "Дом 2": {"2026-08-10": free(9000), "2026-08-11": free(9000),
                      "2026-08-12": busy(), "2026-08-13": free(9500),
                      "2026-08-14": dict(UNK)},
        },
        "obj_type": {
            "А-фрейм": {"2026-08-10": free(15000, 3, 1), "2026-08-11": free(15000, 3, 3),
                        "2026-08-12": free(15000, 3, 2), "2026-08-13": busy(3),
                        "2026-08-14": free(16000, 3, 1)},
        },
        "obj_gap": {
            "Шатёр": {"2026-08-10": busy(), "2026-08-11": free(7000),
                      "2026-08-12": free(7000), "2026-08-13": free(7000),
                      "2026-08-14": dict(SNO)},
        },
    },
    "2026-08-11-0630": {
        "obj_home": {
            "Дом 1": {"2026-08-11": busy(), "2026-08-12": free(10500),
                      "2026-08-13": free(11000), "2026-08-14": free(12000),
                      "2026-08-15": free(12000)},
            "Дом 2": {"2026-08-11": free(9000), "2026-08-12": busy(),
                      "2026-08-13": free(9500), "2026-08-14": free(9800),
                      "2026-08-15": free(9800)},
        },
        "obj_type": {
            "А-фрейм": {"2026-08-11": free(15000, 3, 2), "2026-08-12": free(15000, 3, 1),
                        "2026-08-13": busy(3), "2026-08-14": free(16000, 3, 1),
                        "2026-08-15": free(16000, 3, 2)},
        },
        "obj_gap": {
            "Шатёр": {"2026-08-11": free(7000), "2026-08-12": busy(),
                      "2026-08-13": free(7000), "2026-08-14": dict(SNO),
                      "2026-08-15": dict(SNO)},
        },
    },
    # obj_gap в этом прогоне НЕ снят: его ночь 12.08 замёрзнет снимком 11.08
    "2026-08-12-0630": {
        "obj_home": {
            "Дом 1": {"2026-08-12": busy(), "2026-08-13": free(11000),
                      "2026-08-14": busy(), "2026-08-15": free(12000),
                      "2026-08-16": free(12000)},
            "Дом 2": {"2026-08-12": busy(), "2026-08-13": free(9500),
                      "2026-08-14": free(9800), "2026-08-15": free(9800),
                      "2026-08-16": free(9800)},
        },
        "obj_type": {
            "А-фрейм": {"2026-08-12": free(15000, 3, 1), "2026-08-13": busy(3),
                        "2026-08-14": free(16000, 3, 1), "2026-08-15": free(16000, 3, 2),
                        "2026-08-16": free(14000, 3, 3)},
        },
    },
    "2026-08-13-0630": {
        "obj_home": {
            "Дом 1": {"2026-08-13": busy(), "2026-08-14": busy(),
                      "2026-08-15": free(12000), "2026-08-16": free(12000),
                      "2026-08-17": free(12000)},
            "Дом 2": {"2026-08-13": free(9500), "2026-08-14": free(9800),
                      "2026-08-15": free(9800), "2026-08-16": free(9800),
                      "2026-08-17": free(9800)},
        },
        "obj_type": {
            "А-фрейм": {"2026-08-13": busy(3), "2026-08-14": free(16000, 3, 1),
                        "2026-08-15": free(16000, 3, 2), "2026-08-16": free(14000, 3, 3),
                        "2026-08-17": free(14000, 3, 3)},
        },
        "obj_gap": {
            "Шатёр": {"2026-08-13": free(7000), "2026-08-14": free(7500),
                      "2026-08-15": dict(SNO), "2026-08-16": dict(SNO),
                      "2026-08-17": dict(SNO)},
        },
    },
}

ENGINE = {"obj_home": "homereserve", "obj_type": "travelline", "obj_gap": "bnovo"}

if OUT.exists():
    shutil.rmtree(OUT)
for run_id, objects in RUNS.items():
    checked = f"{run_id[:10]}T{run_id[11:13]}:{run_id[13:]}:00+03:00"
    core.write_snapshot(
        OUT, run_id,
        {"started_at": checked, "targets": sorted(ENGINE)},
        [obj(u, ENGINE[u], units, checked) for u, units in sorted(objects.items())],
    )
print("ok", sorted(p.name for p in OUT.iterdir()))
