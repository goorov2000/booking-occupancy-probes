# -*- coding: utf-8 -*-
"""Генератор статической фикстуры units_report/history — гоняется РУКАМИ один раз.

Зачем отдельная от `fixtures/ledger/history`: поюнитному отчёту нужны вещи,
которых у той фикстуры нет и быть не должно, — тип с фондом 1, юнит с
«домиковым» ИМЕНЕМ и неснятым фондом, ближний горизонт фонда (тикет 06),
закрытое окно продаж и контрольный разгон одной ночи для кривой темпа.

Модель бронирования тут не случайная, а причинная: у каждого домика есть свой
«лид» L — за сколько суток до ночи его выкупают. В снимке за d суток до ночи
домик занят ровно когда d <= L. Отсюда и кривая темпа получается настоящей
(чем ближе ночь, тем больше продано), а не нарисованной по точкам.

Календарь: 2026-08-10 — понедельник; пятница в метрике относится к ВЫХОДНЫМ.
"""
import hashlib
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "occupancy"   # <repo>/occupancy
sys.path.insert(0, str(SCRIPTS))
import occupancy_core as core  # noqa: E402

OUT = Path(__file__).resolve().parent / "history"

FIRST_RUN = date(2026, 8, 10)
LAST_RUN = date(2026, 8, 18)          # ночи 10-17.08 успевают замёрзнуть
GRID_DAYS = 56                        # хватает, чтобы задеть октябрь

# Ночи, после которых модуль объекта вообще не отвечает: «продажи не открыты».
# Нужны, чтобы отчёт не читал закрытое окно как аншлаг (тикет 07).
SALES_WALL = {("obj_spheres", "Сфера 2"): date(2026, 9, 20)}

# Докуда прогон спрашивал ФОНД (тикет 06). Разрезает сентябрь пополам, а
# октябрь оставляет за границей целиком — обе оговорки нужны отчёту.
INVENTORY_UNTIL = {"obj_types": "2026-09-15"}

# username -> (движок, гранулярность, {юнит: фонд или None})
OBJECTS = {
    "obj_homes": ("homereserve", "per_unit", {"Дом 1": None, "Дом 2": None}),
    "obj_types": ("travelline", "per_unit", {"А-фрейм": 3, "Люкс": 1}),
    "obj_spheres": ("bnovo", "per_unit", {"Сфера 1": None, "Сфера 2": None}),
    "obj_ramp": ("travelline", "per_unit", {"Купол": 7}),
    # Квота агрегатора — это доля ПЛОЩАДКИ, а не загрузка объекта, и в факт
    # она не идёт (ledger выбрасывает её из ряда). Отчёт обязан сказать об
    # этом словами: молча пустая ячейка читается как «объект простаивает».
    "obj_quota": ("aggregator-ostrovok", "per_unit", {"Шатёр": None}),
}

# Объекты, снятые не модулем объекта, а квотой площадки-агрегатора.
QUOTA_OBJECTS = {"obj_quota"}

# Контрольный разгон для кривой темпа: ночь 17.08 у obj_ramp/Купол должна
# пройти 0% на lead 3 -> 42.9% (3 из 7) на lead 0. Значит трое суток до ночи
# не продано ничего, а дальше выкупают по домику в сутки.
RAMP_NIGHT = date(2026, 8, 17)
RAMP_LEADS = [2, 1, 0]                # лиды трёх проданных домиков из семи


def _leads(username: str, unit: str, night: date, homes: int) -> list[int]:
    """Лиды домиков юнита на ночь: детерминированы именем, а не random."""
    if (username, unit) == ("obj_ramp", "Купол") and night == RAMP_NIGHT:
        return RAMP_LEADS
    seed = f"{username}|{unit}|{night.isoformat()}".encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    leads = []
    for i in range(homes):
        # 0..40 суток либо -1 («не выкупят никогда»): доля непроданных ночей
        # получается около трети, что близко к живой истории (62.7% занятых)
        raw = digest[i] % 48
        leads.append(-1 if raw >= 41 else raw)
    return leads


def _price(username: str, unit: str, night: date) -> int:
    seed = f"price|{username}|{unit}|{night.isoformat()}".encode("utf-8")
    return 7000 + (hashlib.sha256(seed).digest()[0] % 12) * 500


def _cell(username, unit, capacity, night, run_day):
    wall = SALES_WALL.get((username, unit))
    if wall is not None and night > wall:
        return {"state": "sales_not_open"}
    homes = capacity or 1
    lead = (night - run_day).days
    leads = _leads(username, unit, night, homes)
    sold = sum(1 for L in leads if L >= lead)
    cell = {}
    if capacity is not None:
        cell["units_total"] = capacity
        cell["units_free"] = capacity - sold
    if sold >= homes:
        cell["state"] = "busy"
    else:
        cell["state"] = "free"
        cell["price"] = _price(username, unit, night)
    return cell


def build() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    run_day = FIRST_RUN
    while run_day <= LAST_RUN:
        run_id = f"{run_day.isoformat()}-0630"
        checked = f"{run_day.isoformat()}T06:30:00+03:00"
        objects = []
        for username, (engine, granularity, units) in sorted(OBJECTS.items()):
            grid = {}
            for unit, capacity in units.items():
                cells = {}
                for i in range(GRID_DAYS):
                    night = run_day + timedelta(days=i)
                    cells[night.isoformat()] = _cell(username, unit, capacity,
                                                     night, run_day)
                grid[unit] = cells
            obj = {
                "username": username,
                "site": f"https://example.com/{username}",
                "engine": engine,
                "source_kind": ("aggregator_quota" if username in QUOTA_OBJECTS
                                else "module"),
                "granularity": granularity,
                "units": grid,
                "checked_at": checked,
                "status": "ok",
                "reason": "",
                "source_urls": [f"https://example.com/{username}/widget"],
            }
            if username in INVENTORY_UNTIL:
                obj["inventory_until"] = INVENTORY_UNTIL[username]
            objects.append(obj)
        core.write_snapshot(OUT, run_id,
                            {"started_at": checked, "targets": sorted(OBJECTS)},
                            objects)
        run_day += timedelta(days=1)
    print("ok", sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    build()
