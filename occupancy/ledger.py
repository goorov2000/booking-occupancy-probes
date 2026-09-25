# -*- coding: utf-8 -*-
"""Ledger — производный слой сезонности: замороженный факт и кривая темпа.

Зачем он есть
-------------
Сводка `/glamping-occupancy` живёт одним кадром: последний снимок плюс дельта к
предыдущему. Сезонность — это РЯД: чем ночь кончилась и как быстро она
заполнялась по мере приближения. Ряд в снапшотах уже лежит, но читать его в лоб
дорого: 38 прогонов августа-сентября 2026 — это 413 067 клеток и ~2 с на проход,
а при 53 целях и годе истории — десятки миллионов клеток и минуты на КАЖДЫЙ
отчёт. Поэтому история сворачивается один раз (`rebuild`), дальше дописывается по
прогону (`append`), а отчёты читают только производный слой.

Правило Р4 спеки (заморозка) и что показала проверка на данных
--------------------------------------------------------------
Реализованная занятость ночи D = состояние из ПОСЛЕДНЕГО снапшота, где D ещё
была в горизонте. Разведка спеки утверждала, что это ровно снимок утра самой
ночи; проверено поимённо на всех 38 каталогах
`agent-runtime/research/glamping/occupancy/snapshots/`:
  * у 429 снятых объектов из 433 минимальная дата сетки РАВНА дате прогона
    (остальные 4 — ручные прогоны агента 14-15.08 с урезанным окном, 32 объекта
    сняты пустыми);
  * для ночей 17.08-03.09 у всех 20 объектов последний снапшот, где ночь ещё
    была в горизонте, оказался прогоном ТОГО ЖЕ дня (360 пар из 360, ни одного
    исключения).
То есть в здоровом режиме ночь замерзает снимком своего утра (06:30-07:05).
Отсюда и практический критерий: ночь можно морозить только когда на диске есть
прогон СТРОГО ПОЗЖЕ неё, иначе второй прогон того же дня (а такие бывали —
14.08 их двенадцать) переписал бы уже замороженную строку.

Где заморозка врёт, и как это видно
-----------------------------------
Если объект в прогоне не снялся (отказ хоста) или машина простояла сутки, ночь
замерзает снимком, сделанным РАНЬШЕ её утра, — это уже «продано вперёд», а не
факт. Такая строка несёт `gap_days > 0` и по умолчанию выкидывается из
`realized()` и из рыночной медианы. В `gap_days` строки складываются два разных
опоздания, и берётся ХУДШЕЕ из них:
  * сколько суток между ночью и прогоном, который её заморозил;
  * `gap_days` самого объекта (тикет 10) — насколько прогон отстал от планового
    времени, то есть догонял простой машины.
Оба означают одно: снимок сделан не в эталонный момент.

Род строки (решение Р3 спеки)
-----------------------------
Ledger не решает, как строку ПОДПИСАТЬ (это делает отчёт), но фиксирует род,
чтобы отчёт не гадал по имени юнита: `home` — движок адресует физический домик,
`type` — тип с известным фондом («продано N из M»), `type_no_capacity` — тип, у
которого фонд не снят. Род определяется движком и фондом, а не именем: у
`wood_glamp` юниты называются «Сфера 1»…«Сфера 5» и выглядят домиками, но фонд
не снят, а у `a_ureki` «Этнодом» — четыре домика под одним именем.

Род — свойство ЮНИТА, а не отдельной ночи, поэтому он считается по фонду за ВСЮ
историю (`index["capacity"]`), а не по тому, что было известно в момент
заморозки конкретной ночи. Пока это было не так, 87 юнитов из 157 несли в ряду
по два разных рода: a_ureki/«Белый Дом» — ночь 14.08 `type_no_capacity`, ночи
15.08 и дальше `type`, потому что фонд впервые попал в снапшот 2026-08-15-1500.
Цифры при этом были верные, врала подпись. `read_nights` доводит род уже
записанных строк до общего знаменателя на чтении, поэтому дозапись слоя не
требует переписывания журнала. Слой, собранный ДО появления `index["capacity"]`,
читается как есть: фонд по строкам не восстановить (`units_total` у клетки без
снятого фонда равен единице — это вес клетки в метрике, а не число домиков).

Какой прогон вообще можно брать в работу
----------------------------------------
Заморозка необратима, поэтому в неё нельзя пускать прогон, который ЕЩЁ ПИШЕТСЯ.
После тикета 02 полупустой каталог прогона — норма в течение всего съёма (до 90
минут): `open_snapshot` кладёт run.json первым, объекты дописываются по ходу,
`close_snapshot` в конце дописывает `finished_at`. Прогон считается дочитанным,
если верно любое из трёх:
  * в run.json есть `finished_at` — прогон закрыт своим писателем;
  * на диске есть прогон СТРОГО ПОЗЖЕ — значит этот каталог уже никто не растит
    (так подхватывается и прогон, убитый таймаутом: он не вырастет никогда);
  * в run.json нет `planned_at` — это писатель ДО тикета 02, он писал run.json
    одним куском после съёма. Все 38 боевых снапшотов 17.08-04.09.2026 такие:
    ни `finished_at`, ни `planned_at` (проверено `grep -l` по каталогу), и
    требование `finished_at` в лоб выкинуло бы всю историю.
Прогон, не прошедший это сито, НЕ пишется в индекс: он не потерян, его возьмёт
следующий вызов. Отсюда требование к вызывающему (cli.py:305-307): открывая
снапшот, всегда класть в run.json `planned_at` — это единственное, чем открытый
прогон отличается от старого закрытого.

Кривая темпа и два её знаменателя
---------------------------------
Точка кривой — это средняя занятость ночей МЕСЯЦА в снимке за lead суток до
них, и складывать такие точки в ряд можно только при двух условиях. Оба
проверяются в самой точке, потому что нарушаются они молча:
  * ОДИН фонд. С тикета 06 фонд типов снимается на ближние 45 ночей (запрос на
    каждую ночь — почти вся цена прогона), а сетка идёт на год. За горизонтом
    фонда клетка весит один домик (`core.cell_units`), и тип из трёх домиков с
    одним проданным даёт 0% на lead 90 против 33% на lead 45 — «рост темпа» на
    ночи, где не забронировали ничего. Поэтому строка темпа несёт
    `inventory_known`, а точка — `inventory_missing`: строки без снятого фонда
    в счёт не идут вовсе. `None` в этом поле («прогон не сказал») — это все 38
    боевых снапшотов до тикета 06, у них фонд спрашивался на всю сетку.
  * ОДИН набор ночей. У глубоких lead ночей меньше — истории просто нет: на
    боевом слое одного объекта за сентябрь lead 21 стоял на 20 ночах, а lead
    45 — на ОДНОЙ, и его 6.1% против 46.7% рядом читались как «за полтора
    месяца до заезда продаж нет». Точка несёт `nights`/`nights_base`/
    `coverage`/`partial`, и не дотянувшая до `min_coverage` отдаёт `pct`
    пустым, сохраняя цифру в `pct_partial`.
Слой замороженных ночей это не касается: ночь морозится снимком своего утра
(2649 строк из 2649 на живой истории), то есть внутри горизонта фонда, а
редкая ночь, замёрзшая снимком старше 45 суток, и без того выброшена по
`gap_days`.

Формат слоя
-----------
`nights.jsonl.gz`, `pace.jsonl.gz` — JSONL под gzip (замер спеки: сжатие 26.4x),
`index.json` — до какого прогона слой собран и какой фонд у каждого юнита,
`state.json.gz` — рабочая память инкремента (незамороженные клетки и последние
виденные цены). `index.json` НЕ несёт времени сборки нарочно: повторный
`rebuild` обязан давать побайтно те же файлы, иначе идемпотентность не проверить.

Дозапись (`append`) кладёт в gzip-файл НОВЫЙ member: конкатенация gzip-членов —
законный gzip, и `gzip.open` читает их подряд. Батч собирается в памяти и
пишется одной записью; оборванная дозапись лечится `rebuild` (он переписывает
слой целиком из снапшотов — они и есть источник истины).

Дозапись верит индексу на слово, поэтому перед ней слой сверяется с индексом
(`verify`): столько ли строк в файлах, сколько обещано, и на месте ли рабочая
память. Пропавший `nights.jsonl.gz` при живом индексе раньше пересоздавался
молча — на боевой истории ряд съезжал с 3057 ночей (65.4%) до 157 (39.1%),
а индекс продолжал рапортовать 3214 строки и `needs_rebuild=False`. Счётчики
индекса считают строки ФАЙЛА, вместе с дублями (два прогона за сутки пишут
одну точку темпа дважды): слой, собранный дозаписью, поэтому крупнее
пересобранного — 40562 строки темпа против 39878 на тех же 38 прогонах. Это
и делает сверку «файл = индекс» возможной; дубли сворачивают читатели.

Сети здесь нет и быть не может: модуль читает только диск.
"""
from __future__ import annotations

import gzip
import io
import json
import os
import statistics
import zlib
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Optional

import occupancy_core as core

SCHEMA_VERSION = 1

DEFAULT_LEDGER_DIR = core.OCCUPANCY_ROOT / "ledger"
NIGHTS_FILE = "nights.jsonl.gz"
PACE_FILE = "pace.jsonl.gz"
INDEX_FILE = "index.json"
STATE_FILE = "state.json.gz"

# Кривая темпа хранится прорежённо: полный ряд «каждый прогон × каждая ночь» —
# это и есть та самая история, ради ухода от которой слой затевался.
LEAD_STEPS = (0, 1, 2, 3, 5, 7, 10, 14, 21, 30, 45, 60, 90)

# Движки, которые адресуют ФИЗИЧЕСКИЙ номер, а не тип размещения (решение Р3).
# bronirui попадает сюда с оговоркой: если тикет 04 вычитал из ответа
# rooms_count > 1, фонд у юнита известен и строка честно становится типом —
# первая ветка _row_basis() это ловит.
PER_UNIT_ENGINES = frozenset({"homereserve", "bronirui", "litepms", "rc-bookings"})

BASIS_HOME = "home"                       # «Домик: <имя>»
BASIS_TYPE = "type"                       # «Тип: <имя> — продано N из M»
BASIS_TYPE_NO_CAPACITY = "type_no_capacity"   # «Тип: <имя> — фонд не снят»

CUTS = ("all", "weekday", "weekend")


class LedgerError(Exception):
    """Слой сезонности не читается/не собирается: нет файлов, битые данные."""


# ---------------------------------------------------------------------------
# Сборка слоя
# ---------------------------------------------------------------------------

class _Builder:
    """Рабочая память прохода по снапшотам: незамороженные клетки и цены.

    Живёт ровно столько, сколько идёт проход, и целиком сохраняется в
    state.json.gz — иначе `append` не смог бы заморозить ночь, данные которой
    пришли из прогонов, прочитанных в прошлый раз.
    """

    def __init__(self, cells: Optional[dict] = None,
                 capacity: Optional[dict] = None,
                 last_run_date: Optional[date] = None):
        # (username, unit, night) -> последнее виденное состояние клетки
        self.cells: dict[tuple, dict] = cells or {}
        # (username, unit) -> максимальный снятый фонд юнита за всю историю:
        # тип, у которого хоть раз было три домика, типом и остаётся
        self.capacity: dict[tuple, int] = capacity or {}
        self.last_run_date: Optional[date] = last_run_date

    # -- проход ------------------------------------------------------------

    def consume(self, snapshot_root: Path,
                run_ids: list[str]) -> tuple[list[dict], list[dict]]:
        """Прочитать прогоны по порядку. -> (замороженные ночи, точки темпа)."""
        frozen: list[dict] = []
        pace: dict[tuple, dict] = {}
        for run_id in run_ids:
            run_date = _run_date(run_id)
            # Ночь морозим, только когда на диске есть прогон ПОЗЖЕ неё:
            # второй прогон того же дня иначе переписал бы готовую строку.
            frozen.extend(self._freeze_before(run_date))
            snap = _read_snapshot(snapshot_root / run_id)
            run_gap = core.gap_days(snap["run"].get("planned_at"),
                                    snap["run"].get("started_at")) or 0
            for username, obj in sorted(snap["objects"].items()):
                self._absorb(username, obj, run_id, run_date, run_gap, pace)
            self.last_run_date = (run_date if self.last_run_date is None
                                  else max(self.last_run_date, run_date))
        if self.last_run_date is not None:
            # прогон вправе принести ночь задним числом — она тоже прошедшая
            frozen.extend(self._freeze_before(self.last_run_date))
        # Род строки проставляется ПОСЛЕДНИМ проходом: фонд юнита мог впервые
        # появиться в прогоне, прочитанном уже после заморозки его ночи, а род
        # обязан быть один на юнит (иначе отчёт получает две подписи на ряд).
        for row in frozen:
            row["basis"] = _row_basis(row["engine"], row["granularity"],
                                      row["unit"],
                                      self.capacity.get((row["username"],
                                                         row["unit"])))
        frozen.sort(key=_night_sort_key)
        return frozen, [pace[k] for k in sorted(pace)]

    def _absorb(self, username: str, obj: dict, run_id: str, run_date: date,
                run_gap: int, pace: dict) -> None:
        engine = obj.get("engine") or ""
        source_kind = obj.get("source_kind") or ""
        granularity = obj.get("granularity") or ""
        # gap самого объекта (тикет 10) точнее прогонного: у объекта может быть
        # свой отказ. Нет поля — берём прогонный, нет и его — ноль.
        obj_gap = obj.get("gap_days")
        gap = int(obj_gap) if isinstance(obj_gap, int) else run_gap
        # Докуда прогон спрашивал ФОНД (тикет 06). Дальше этой ночи движок
        # отдаёт только «категория продаётся», клетка весит один домик, и
        # точка кривой по ней считается по ДРУГОМУ знаменателю.
        inventory_until = obj.get("inventory_until")
        for unit, cells in (obj.get("units") or {}).items():
            cap = core.unit_capacity(cells)
            if cap is not None:
                key = (username, unit)
                self.capacity[key] = max(self.capacity.get(key, 0), cap)
            for night, cell in cells.items():
                self._absorb_cell(username, unit, night, cell, run_id,
                                  run_date, gap, engine, source_kind,
                                  granularity, pace, inventory_until)

    def _absorb_cell(self, username, unit, night, cell, run_id, run_date, gap,
                     engine, source_kind, granularity, pace,
                     inventory_until=None) -> None:
        key = (username, unit, night)
        prev = self.cells.get(key)
        price = cell.get("price")
        # Последняя известная цена клетки: движки цену ПРОДАННОЙ ночи не отдают
        # (0 из 5799 занятых клеток с ценой), поэтому её приходится помнить с
        # тех пор, когда ночь ещё была свободна.
        last_price = price if price is not None else (prev or {}).get("last_price")
        self.cells[key] = {
            "cell": cell, "run_id": run_id, "gap": gap, "engine": engine,
            "source_kind": source_kind, "granularity": granularity,
            "last_price": last_price,
        }
        lead = (date.fromisoformat(night) - run_date).days
        if lead in LEAD_STEPS:
            total, free = _units_pair(cell)
            pace[(username, unit, night, lead)] = {
                "night": night, "username": username, "unit": unit,
                "lead_days": lead, "state": cell.get("state"),
                "units_total": total, "units_free": free, "run_id": run_id,
                # основание точки едет вместе с ней: без него читатель не
                # отличит «продано 0 из 3» от «фонд на эту глубину не снят»
                "inventory_known": _inventory_known(night, inventory_until),
            }

    def _freeze_before(self, boundary: date) -> list[dict]:
        """Выгрузить из памяти все ночи строго раньше boundary. -> строки ряда."""
        stale = [k for k in self.cells if date.fromisoformat(k[2]) < boundary]
        rows = []
        for key in stale:
            entry = self.cells.pop(key)
            rows.append(self._row(key, entry))
        return rows

    def _row(self, key: tuple, entry: dict) -> dict:
        username, unit, night = key
        cell = entry["cell"]
        total, free = _units_pair(cell)
        price = cell.get("price")
        # сколько суток снимок отстоит от эталона «утро самой ночи»
        snap_gap = (date.fromisoformat(night) - _run_date(entry["run_id"])).days
        # Цену до продажи подставляем ТОЛЬКО проданной ночи. У ночи, которую
        # мы потеряли из виду (unknown) или которую сняли с продажи, последняя
        # виденная цена — не выручка, а память; на живой истории таких строк
        # набралось 6, и в отчёте они выглядели бы деньгами, которых не было.
        sold = cell.get("state") == "busy" and price is None
        return {
            "night": night,
            "username": username,
            "unit": unit,
            "state": cell.get("state"),
            "units_total": total,
            "units_free": free,
            "price": price,
            "price_before_sale": entry["last_price"] if sold else None,
            "source_run_id": entry["run_id"],
            "gap_days": max(0, snap_gap, entry.get("gap") or 0),
            # окончательное значение проставит consume(), когда прочитает всё
            "basis": BASIS_TYPE_NO_CAPACITY,
            "engine": entry["engine"],
            # granularity держится в строке, чтобы род можно было пересчитать
            # на чтении: без него «домик» от «типа» не отличить
            "granularity": entry["granularity"],
            "source_kind": entry["source_kind"],
        }

    # -- сохранение памяти между прогонами ---------------------------------

    def dump(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "last_run_date": (self.last_run_date.isoformat()
                              if self.last_run_date else None),
            "capacity": [{"username": u, "unit": n, "capacity": c}
                         for (u, n), c in sorted(self.capacity.items())],
            "cells": [{"username": u, "unit": n, "night": d, **entry}
                      for (u, n, d), entry in sorted(self.cells.items())],
        }

    @classmethod
    def load(cls, data: dict) -> "_Builder":
        cells = {(r["username"], r["unit"], r["night"]):
                 {k: v for k, v in r.items()
                  if k not in ("username", "unit", "night")}
                 for r in data.get("cells", [])}
        capacity = {(r["username"], r["unit"]): r["capacity"]
                    for r in data.get("capacity", [])}
        last = data.get("last_run_date")
        return cls(cells, capacity, date.fromisoformat(last) if last else None)


def _row_basis(engine: str, granularity: str, unit: str,
               capacity: Optional[int]) -> str:
    """Род строки по движку и фонду (решение Р3), НИКОГДА не по имени юнита."""
    if capacity is not None and capacity > 1:
        return BASIS_TYPE
    if (granularity == "per_unit" and unit != core.AGGREGATE_UNIT
            and engine in PER_UNIT_ENGINES):
        return BASIS_HOME
    # тип с фондом 1 остаётся типом: движок торгует категорией, а не домиком
    return BASIS_TYPE if capacity is not None else BASIS_TYPE_NO_CAPACITY


def _inventory_known(night: str, inventory_until) -> Optional[bool]:
    """Спрашивался ли ФОНД на эту ночь в этом прогоне (тикет 06).

    None — прогон не сказал: так писали все 38 боевых снапшотов до тикета 06,
    и фонд у них спрашивался на всю сетку. «Не знаем» приравнивать к «фонда
    нет» нельзя — это стёрло бы всю снятую историю темпа.
    Даты ISO, поэтому сравнение строк — то же самое сравнение дат.
    """
    if not inventory_until:
        return None
    return night <= str(inventory_until)


def _units_pair(cell: dict) -> tuple[Optional[int], Optional[int]]:
    """(домиков всего, свободно) для клетки; (None, None) вне free/busy.

    У `unknown` и `sales_not_open` фонда нет по смыслу: core.cell_units отдал
    бы им «1 домик, занят 0», и ряд честно бы соврал, что ночь была свободна.
    """
    if cell.get("state") not in ("free", "busy"):
        return None, None
    total, busy = core.cell_units(cell)
    return total, total - busy


def _night_sort_key(row: dict) -> tuple:
    return (row["night"], row["username"], row["unit"])


def _run_date(run_id: str) -> date:
    try:
        return date.fromisoformat(run_id[:10])
    except ValueError:
        raise LedgerError(
            f"{run_id}: имя каталога прогона не начинается с YYYY-MM-DD") from None


def _read_snapshot(path: Path) -> dict:
    try:
        return core.read_snapshot(path)
    except core.SnapshotError as e:
        raise LedgerError(f"снапшот {path.name} не читается: {e}") from None


def _read_run_meta(snap_dir: Path) -> dict:
    """run.json прогона без чтения его объектов (их сотни килобайт)."""
    data = core.load_json(snap_dir / "run.json", LedgerError)
    return data if isinstance(data, dict) else {}


def _pending_runs(snapshot_root: Path, run_ids: list[str]) -> list[str]:
    """Прогоны, которые ещё ПИШУТСЯ, — их нельзя пускать в заморозку.

    Растёт только ПОСЛЕДНИЙ каталог: у любого более раннего на диске уже есть
    прогон позже, а значит писатель до него давно не дотягивается. Поэтому
    проверять run.json достаточно у одного прогона, а не у всей истории.
    """
    if not run_ids:
        return []
    last = run_ids[-1]
    run = _read_run_meta(Path(snapshot_root) / last)
    if run.get("finished_at") is not None:
        return []
    # нет `planned_at` — писатель ДО тикета 02: он закрывал run.json сам
    if run.get("planned_at") is None:
        return []
    return [last]


def _with_note(stats: dict) -> dict:
    """Дописать в статистику готовую фразу для человека (поле `note`).

    Складывается один раз здесь, а не у каждого вызывающего: пересказ терял
    предупреждение о дыре, и прогон задним числом читался как тихий день.
    """
    stats["note"] = append_note(stats)
    return stats


def rebuild(snapshot_root=None, out_dir=None, upto=None) -> dict:
    """Полный пересбор слоя из всей истории снапшотов. -> статистика.

    Идемпотентен: те же снапшоты дают побайтно те же файлы (в них нет ни
    времени сборки, ни порядка обхода каталога — строки сортируются).
    `upto` — run_id или дата: докуда читать историю (для тестов и разборов
    «а что слой знал на такое-то число»).
    """
    snapshot_root = Path(snapshot_root or core.DEFAULT_SNAPSHOT_ROOT)
    out_dir = Path(out_dir or DEFAULT_LEDGER_DIR)
    all_runs = _runs_upto(core.list_snapshots(snapshot_root), upto)
    # `upto` означает «что слой знал на такое-то число», поэтому готовность
    # прогона проверяется по УРЕЗАННОЙ истории: прогонов после отсечки тогда
    # ещё не было, и они не могут задним числом закрыть последний.
    pending = _pending_runs(snapshot_root, all_runs)
    run_ids = [r for r in all_runs if r not in pending]

    builder = _Builder()
    nights, pace = builder.consume(snapshot_root, run_ids)

    _write_rows(out_dir / NIGHTS_FILE, nights, append=False)
    _write_rows(out_dir / PACE_FILE, pace, append=False)
    _write_state(out_dir, builder)
    # пересбор читает историю целиком — отставших прогонов после него нет
    index = _make_index(run_ids, builder, len(nights), len(pace), [])
    _write_index(out_dir, index)
    return _with_note({"run_ids": run_ids, "nights_rows": len(nights),
                       "pace_rows": len(pace),
                       "frozen_through": index["frozen_through"],
                       "pending_runs": pending, "skipped_stale": [],
                       "stale_runs": [], "needs_rebuild": False,
                       "rebuilt": True})


def append(snapshot_root=None, out_dir=None) -> dict:
    """Инкремент за новые прогоны. -> статистика (в т.ч. что было пропущено).

    Зовётся из `run_scheduled.py` после сводки. Слоя нет — собирается целиком;
    новых прогонов нет — файлы не трогаются вовсе.

    Три вида «не взяли в работу» называются по отдельности, потому что чинятся
    по-разному: `pending_runs` — прогон ещё пишется (сам догонит следующим
    вызовом), `skipped_stale` — прогон появился задним числом (лечится только
    пересбором, поэтому взводится `needs_rebuild`), `stale_runs` — все такие
    дыры за всю жизнь слоя.
    """
    snapshot_root = Path(snapshot_root or core.DEFAULT_SNAPSHOT_ROOT)
    out_dir = Path(out_dir or DEFAULT_LEDGER_DIR)
    index = _read_index(out_dir)
    if index is None:
        stats = rebuild(snapshot_root, out_dir)
        stats.update({"new_runs": stats["run_ids"], "nights_rows_added":
                      stats["nights_rows"], "pace_rows_added":
                      stats["pace_rows"]})
        return _with_note(stats)

    # Дозапись верит индексу на слово (открывает ряд на 'ab'), поэтому
    # расхождение индекса с файлами ловится ДО первой записи.
    _require_intact(out_dir)

    seen_stale = list(index.get("stale_runs", []))
    known = set(index.get("runs", [])) | set(seen_stale)
    last_run_id = index.get("last_run_id") or ""
    all_runs = core.list_snapshots(snapshot_root)
    pending = _pending_runs(snapshot_root, all_runs)
    fresh, stale = [], []
    for run_id in all_runs:
        if run_id in known or run_id in pending:
            continue
        # Прогон СТАРШЕ уже обработанных трогать нельзя: его ночи давно
        # заморожены, и дозапись их удвоила бы. Такое лечится только rebuild.
        (stale if run_id < last_run_id else fresh).append(run_id)
    # Дыру записываем в индекс, иначе её будет находить и выбрасывать КАЖДЫЙ
    # прогон, а следа не останется нигде: пропуск дня обязан быть виден.
    stale_runs = sorted(set(seen_stale) | set(stale))

    if not fresh:
        if stale:
            _write_index(out_dir, dict(index, stale_runs=stale_runs))
        return _with_note(
            {"new_runs": [], "nights_rows_added": 0, "pace_rows_added": 0,
             "skipped_stale": stale, "stale_runs": stale_runs,
             "needs_rebuild": bool(stale_runs), "pending_runs": pending,
             "frozen_through": index.get("frozen_through"),
             "rebuilt": False})

    builder = _Builder.load(_read_state(out_dir))
    nights, pace = builder.consume(snapshot_root, fresh)
    _write_rows(out_dir / NIGHTS_FILE, nights, append=True)
    _write_rows(out_dir / PACE_FILE, pace, append=True)
    _write_state(out_dir, builder)
    new_index = _make_index(index.get("runs", []) + fresh, builder,
                            index.get("nights_rows", 0) + len(nights),
                            index.get("pace_rows", 0) + len(pace), stale_runs)
    _write_index(out_dir, new_index)
    return _with_note(
        {"new_runs": fresh, "nights_rows_added": len(nights),
         "pace_rows_added": len(pace), "skipped_stale": stale,
         "stale_runs": stale_runs, "needs_rebuild": bool(stale_runs),
         "pending_runs": pending,
         "frozen_through": new_index["frozen_through"], "rebuilt": False})


def _runs_upto(run_ids: list[str], upto) -> list[str]:
    if upto is None:
        return list(run_ids)
    if isinstance(upto, date):
        return [r for r in run_ids if _run_date(r) <= upto]
    return [r for r in run_ids if r <= str(upto)]


def _make_index(run_ids: list[str], builder: _Builder, nights_rows: int,
                pace_rows: int, stale_runs: list[str]) -> dict:
    frozen_through = None
    if builder.last_run_date is not None:
        frozen_through = (builder.last_run_date - timedelta(days=1)).isoformat()
    return {
        "schema_version": SCHEMA_VERSION,
        "runs": list(run_ids),
        "last_run_id": run_ids[-1] if run_ids else None,
        "frozen_through": frozen_through,
        "nights_rows": nights_rows,
        "pace_rows": pace_rows,
        "stale_runs": list(stale_runs),
        # фонд юнита за всю историю: по нему читатель приводит род строки к
        # одному на юнит, не перечитывая снапшоты
        "capacity": [{"username": u, "unit": n, "capacity": c}
                     for (u, n), c in sorted(builder.capacity.items())],
    }


# ---------------------------------------------------------------------------
# Файлы слоя
# ---------------------------------------------------------------------------

def _write_rows(path: Path, rows: Iterable[dict], *, append: bool) -> None:
    """Записать (или дописать новым gzip-членом) строки JSONL."""
    rows = list(rows)
    if append and not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    # mtime=0 и пустое имя внутри заголовка: иначе два одинаковых пересбора
    # дают разные байты и идемпотентность нечем проверить
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz:
        for row in rows:
            gz.write((json.dumps(row, ensure_ascii=False, sort_keys=True)
                      + "\n").encode("utf-8"))
    blob = buf.getvalue()
    if append:
        if not path.is_file():
            # 'ab' создал бы файл заново, и слой тихо начался бы с нуля при
            # живом индексе. Симметрия с _read_state: нет файла — не пишем.
            raise LedgerError(
                f"{path}: ряда слоя нет, дописывать не во что — соберите "
                f"слой заново (ledger.rebuild)")
        with path.open("ab") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        return
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_bytes(blob)
    os.replace(tmp, path)


def _count_rows(path: Path) -> Optional[int]:
    """Сколько строк лежит в файле слоя; None — файла нет.

    Читается потоком, без разбора JSON: на боевом слое (3214 ночей + 39878
    точек темпа) сверка стоит 0.2 с на дозапись — против 90-минутного прогона
    это цена, за которую покупается «в файле столько же, сколько обещано».

    Порча самого файла (оборванный член gzip, не-gzip, обрубленный поток)
    выходит отсюда `LedgerError`, а не сырым `zlib.error`/`EOFError`: считать
    строки в повреждённом файле нельзя, и это ровно та порча, которую
    `verify` обязана НАЗЫВАТЬ. Раньше она на ней падала — и вызывающий,
    включая `_require_intact` перед дозаписью, получал исключение мимо
    контракта модуля.
    """
    if not path.is_file():
        return None
    rows = 0
    try:
        with gzip.open(path, "rb") as fh:
            for line in fh:
                if line.strip():
                    rows += 1
    except (OSError, EOFError, zlib.error) as e:
        raise LedgerError(
            f"{path.name}: файл слоя повреждён ({type(e).__name__}: {e}) — "
            f"обычно это оборванная дозапись; снапшоты целы, соберите слой "
            f"заново (ledger.rebuild)") from e
    return rows


def verify(out_dir=None) -> dict:
    """Сверить индекс слоя с файлами. -> отчёт (ok, problems, счётчики).

    Слой производный, и порча в нём лечится пересбором — но сначала её надо
    заметить. Замер на боевой истории: пропавший `nights.jsonl.gz` при живом
    индексе ронял ряд с 3057 ночей (65.4%) до 157 (39.1%), а индекс
    продолжал рапортовать 3214 строки и `needs_rebuild=False`.
    """
    out_dir = Path(out_dir or DEFAULT_LEDGER_DIR)
    index = _read_index(out_dir)
    report = {"ok": False, "problems": [], "index": index,
              "nights_rows": None, "pace_rows": None,
              "needs_rebuild": bool((index or {}).get("stale_runs"))}
    if index is None:
        report["problems"].append(
            f"{out_dir}: слоя нет — соберите его (ledger.rebuild)")
        return report
    for name, key in ((NIGHTS_FILE, "nights_rows"), (PACE_FILE, "pace_rows")):
        try:
            rows = _count_rows(out_dir / name)
        except LedgerError as e:
            # Повреждённый файл — такая же находка отчёта, как пропавший:
            # ронять на ней проверку, которая ради неё и написана, нельзя.
            report[key] = None
            report["problems"].append(str(e))
            continue
        report[key] = rows
        promised = index.get(key)
        if rows is None:
            report["problems"].append(
                f"{name}: файла нет, а индекс обещает строк: {promised} — "
                f"соберите слой заново (ledger.rebuild)")
        elif isinstance(promised, int) and rows != promised:
            report["problems"].append(
                f"{name}: строк в файле {rows}, а индекс обещает {promised} — "
                f"соберите слой заново (ledger.rebuild)")
    if not (out_dir / STATE_FILE).is_file():
        report["problems"].append(
            f"{STATE_FILE}: рабочей памяти слоя нет — соберите слой заново "
            f"(ledger.rebuild)")
    report["ok"] = not report["problems"]
    return report


def _require_intact(out_dir: Path) -> None:
    """Дозапись в повреждённый слой запрещена: она порчу не лечит, а множит."""
    report = verify(out_dir)
    if not report["ok"]:
        raise LedgerError("; ".join(report["problems"]))


def hole_note(index_or_stats, *, nights_read=None, pace_read=None) -> str:
    """Дыра слоя одной фразой; слой цел — пустая строка.

    Одно место, где дыра называется словами, и три читателя: лента прогона
    (`append_note`), дайджест оператора и поюнитный отчёт для заказчика. Дыра,
    о которой знает только `index.json`, — это дыра, о которой не знает никто:
    прогон, появившийся задним числом, выглядит в журнале как обычный тихий
    день, а в отчёте — как честный ряд без этих суток.

    Принимает и индекс слоя, и статистику дозаписи: у обоих одни и те же поля
    `stale_runs`/`needs_rebuild`. `nights_read`/`pace_read` — сколько строк
    вызывающий ПРОЧЁЛ: он уже держит их в руках, и сверка с обещанием индекса
    ему бесплатна (`verify` ради неё читает файлы ещё раз).
    """
    src = index_or_stats or {}
    bits = []
    stale = src.get("stale_runs") or []
    if src.get("needs_rebuild") or stale:
        named = ", ".join(stale) if stale else "неизвестно какие"
        bits.append(f"прогоны задним числом ({named}) в слой не попали, "
                    f"нужен пересбор: ledger.rebuild")
    for key, read in (("nights_rows", nights_read), ("pace_rows", pace_read)):
        promised = src.get(key)
        if read is None or not isinstance(promised, int) or read == promised:
            continue
        bits.append(f"{key}: прочитано строк {read}, а индекс обещает "
                    f"{promised} — слой повреждён, соберите его заново "
                    f"(ledger.rebuild)")
    return "; ".join(bits)


def append_note(stats: dict) -> str:
    """Одна строка о дозаписи слоя — для ленты прогона и дайджеста оператора.

    Дыра в слое обязана доходить до человека словами: прогон, появившийся
    задним числом, иначе выглядит как обычный тихий день («новых прогонов 0,
    ночей +0»), и узнать о нём можно только вручную открыв index.json.

    Готовую фразу кладут в статистику сами `append`/`rebuild` (поле `note`):
    вызывающий, пересказывающий её своими словами, теряет вторую половину —
    ровно так дыра и не доходила до оператора.
    """
    stats = stats or {}
    note = (f"слой сезонности: новых прогонов "
            f"{len(stats.get('new_runs') or [])}, "
            f"ночей +{stats.get('nights_rows_added', 0)}, "
            f"темпа +{stats.get('pace_rows_added', 0)}")
    pending = stats.get("pending_runs") or []
    if pending:
        note += (f"; прогон {', '.join(pending)} ещё пишется — возьмём "
                 f"следующим заходом")
    hole = hole_note(stats)
    if hole:
        note += f"; ВНИМАНИЕ: {hole}"
    return note


def _read_rows(path: Path, what: str) -> list[dict]:
    if not path.is_file():
        raise LedgerError(
            f"{path}: слоя {what} нет — соберите его (ledger.rebuild)")
    rows = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _capacity_index(out_dir: Path) -> Optional[dict]:
    """Фонд каждого юнита из индекса; None — слой собран до появления поля.

    Фонд берётся ТОЛЬКО отсюда, а не из `units_total` строки: у клетки без
    снятого фонда `units_total` равен единице (вес клетки в метрике,
    `core.cell_units`), и по нему «фонд не снят» неотличимо от «домик один».
    """
    index = _read_index(out_dir) or {}
    if "capacity" not in index:
        return None
    return {(item["username"], item["unit"]): item["capacity"]
            for item in index["capacity"]}


def read_nights(out_dir=None) -> list[dict]:
    """Замороженные ночи как они лежат в файле (порядок = порядок дозаписи).

    Дубли не отсеиваются нарочно: их отсеивают потребители (`realized` и
    прочие) по ключу «ночь+объект+юнит», оставляя ПОСЛЕДНЮЮ строку. Файл —
    журнал, а не индекс.

    Единственное, что здесь ПЕРЕСЧИТЫВАЕТСЯ, — род строки (`basis`): фонд юнита
    мог быть снят уже после того, как его ранние ночи ушли в файл, а род обязан
    быть один на юнит. Перезаписывать ради этого журнал не нужно.
    """
    out_dir = Path(out_dir or DEFAULT_LEDGER_DIR)
    rows = _read_rows(out_dir / NIGHTS_FILE, "nights")
    capacity = _capacity_index(out_dir)
    if capacity is None:
        return rows
    for row in rows:
        row["basis"] = _row_basis(row.get("engine") or "",
                                  row.get("granularity") or "", row["unit"],
                                  capacity.get((row["username"], row["unit"])))
    return rows


def read_pace(out_dir=None) -> list[dict]:
    """Точки кривой темпа как они лежат в файле.

    Дубли, как и в `read_nights`, не отсеиваются: каждый прогон пишет свою
    точку, и в день с двумя прогонами одна пара «ночь-юнит-lead» попадает в
    файл дважды (на живой истории — 684 дубля на 40562 строки). Кто считает
    сам, обязан свернуть их по ключу «ночь+объект+юнит+lead_days», оставив
    последнюю строку, — иначе знаменатель таких дней удваивается. `pace_curve`
    и `pace_series` это уже делают.
    """
    return _read_rows(Path(out_dir or DEFAULT_LEDGER_DIR) / PACE_FILE, "pace")


def _write_state(out_dir: Path, builder: _Builder) -> None:
    path = Path(out_dir) / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz:
        gz.write(json.dumps(builder.dump(), ensure_ascii=False,
                            sort_keys=True).encode("utf-8"))
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_bytes(buf.getvalue())
    os.replace(tmp, path)


def _read_state(out_dir: Path) -> dict:
    path = Path(out_dir) / STATE_FILE
    if not path.is_file():
        raise LedgerError(
            f"{path}: рабочей памяти слоя нет — соберите слой заново "
            f"(ledger.rebuild)")
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _write_index(out_dir: Path, index: dict) -> None:
    path = Path(out_dir) / INDEX_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def _read_index(out_dir) -> Optional[dict]:
    path = Path(out_dir) / INDEX_FILE
    if not path.is_file():
        return None
    return core.load_json(path, LedgerError)


def load_index(out_dir=None) -> Optional[dict]:
    """Индекс слоя: какие прогоны учтены и до какой ночи слой заморожен."""
    return _read_index(Path(out_dir or DEFAULT_LEDGER_DIR))


# ---------------------------------------------------------------------------
# Чтение слоя: факт и темп
# ---------------------------------------------------------------------------

def _latest(rows: list[dict], key) -> list[dict]:
    """Оставить по одной строке на ключ — ПОСЛЕДНЮЮ (журнал дописывается)."""
    seen: dict[tuple, dict] = {}
    for row in rows:
        seen[key(row)] = row
    return list(seen.values())


def _targets_rows(targets) -> list[dict]:
    if targets is None:
        targets = core.load_targets(core.DEFAULT_TARGETS)
    elif isinstance(targets, (str, Path)):
        targets = core.load_targets(targets)
    return [t for t in targets if isinstance(t, dict) and t.get("username")]


def region_coverage(targets=None) -> dict:
    """Можно ли вообще резать по региону. -> {ok, regions, missing, note}.

    Спрашивается ДО расчёта: разрез по региону — единственное, что гаснет без
    поля `region` в реестре целей, и отчёт вправе сказать об этом строкой, а
    не поймать ошибку. Проверяется ПОКРЫТИЕ поля, а не факт его наличия у
    кого-нибудь: реестр, заполненный наполовину, молча отдал бы по
    подмосковью нули вместо честного «данных нет».
    """
    rows = _targets_rows(targets)
    missing = sorted(t["username"] for t in rows if not t.get("region"))
    regions: dict[str, int] = {}
    for t in rows:
        if t.get("region"):
            regions[t["region"]] = regions.get(t["region"], 0) + 1
    if missing:
        shown = ", ".join(missing[:5]) + ("…" if len(missing) > 5 else "")
        note = (f"разрез по региону недоступен: в реестре целей "
                f"(targets.json) поле region не проставлено у {len(missing)} "
                f"из {len(rows)} целей ({shown}). Цель без региона молча "
                f"выпала бы из знаменателя, поэтому региональные цифры не "
                f"считаются, пока поле не стоит у всех: region — samara или "
                f"msk3h.")
    else:
        note = (f"разрез по региону доступен: целей {len(rows)}, регионы "
                + ", ".join(f"{r} ({n})" for r, n in sorted(regions.items())))
    return {"ok": not missing, "regions": regions, "missing": missing,
            "targets": len(rows), "note": note}


def _region_usernames(region: str, targets) -> set:
    # реестр читается ОДИН раз: дальше и покрытие, и отбор идут по нему
    rows = _targets_rows(targets)
    coverage = region_coverage(rows)
    if not coverage["ok"]:
        raise LedgerError(coverage["note"])
    return {t["username"] for t in rows if t.get("region") == region}


def _empty_cuts() -> dict:
    return {cut: {"busy": 0, "known": 0} for cut in CUTS}


def _pct(busy: int, known: int) -> Optional[float]:
    return None if known == 0 else round(100.0 * busy / known, 1)


def _select(rows, username, region, month, targets, include_gaps,
            include_quota) -> tuple[list[dict], dict]:
    """Отбор строк ряда + счётчики выброшенного (выброс всегда называется)."""
    members = _region_usernames(region, targets) if region else None
    dropped = {"gap_excluded": 0, "quota_excluded": 0}
    kept = []
    for row in rows:
        if username and row["username"] != username:
            continue
        if members is not None and row["username"] not in members:
            continue
        if month and row["night"][:7] != month:
            continue
        if not include_gaps and (row.get("gap_days") or 0) > 0:
            dropped["gap_excluded"] += 1
            continue
        if not include_quota and row.get("source_kind") == "aggregator_quota":
            dropped["quota_excluded"] += 1
            continue
        kept.append(row)
    return kept, dropped


def _tally(rows: list[dict]) -> dict:
    cuts = _empty_cuts()
    unknown = sales_not_open = 0
    for row in rows:
        state = row.get("state")
        if state == "unknown":
            unknown += 1
            continue
        if state == "sales_not_open":
            sales_not_open += 1
            continue
        total = row.get("units_total") or 0
        busy = total - (row.get("units_free") or 0)
        for cut in ("all", core.day_class(date.fromisoformat(row["night"]))):
            cuts[cut]["known"] += total
            cuts[cut]["busy"] += busy
    for cut in cuts.values():
        cut["pct"] = _pct(cut["busy"], cut["known"])
    return {"cuts": cuts, "unknown": unknown, "sales_not_open": sales_not_open}


def realized(username=None, region=None, month=None, *, out_dir=None,
             rows=None, targets=None, include_gaps=False,
             include_quota=False) -> dict:
    """Фактическая занятость замороженных ночей: домико-ночи занятые/известные.

    Разрезы «все / будни / выходные» (пятница — выходной, как во всей метрике).
    По умолчанию выкидываются два вида строк, и обе выброшенные пачки
    называются числом: ночи с `gap_days > 0` (снимок сделан не утром самой
    ночи) и квоты агрегаторов (это доля площадки, а не загрузка объекта).
    """
    rows = read_nights(out_dir) if rows is None else rows
    rows = _latest(rows, _night_sort_key)
    kept, dropped = _select(rows, username, region, month, targets,
                            include_gaps, include_quota)
    tally = _tally(kept)
    cuts = tally["cuts"]
    # «Объект с цифрой» — тот, у кого в разрезе есть хоть одна известная
    # домико-ночь. Объект, у которого всё окно «продажи не открыты», в ряду
    # присутствует (правило №2 завода: он не исчезает), но в счёт снятых не
    # идёт — на живой истории такие двое из двадцати.
    measured = {r["username"] for r in kept
                if r.get("state") in ("free", "busy")}
    return {
        "busy": cuts["all"]["busy"], "known": cuts["all"]["known"],
        "pct": cuts["all"]["pct"], "cuts": cuts,
        "unknown": tally["unknown"], "sales_not_open": tally["sales_not_open"],
        "gap_excluded": dropped["gap_excluded"],
        "quota_excluded": dropped["quota_excluded"],
        "objects": len(measured),
        "objects_with_rows": len({r["username"] for r in kept}),
        "nights": len({r["night"] for r in kept}),
        "rows": len(kept),
    }


def market_median(region=None, month=None, *, out_dir=None, rows=None,
                  targets=None, include_gaps=False,
                  include_quota=False) -> dict:
    """Медиана занятости ПО ОБЪЕКТАМ (ниша), а не по домико-ночам.

    Средняя по всем клеткам сразу тонет в самом крупном объекте: у
    pineriver_hotel 3237 домико-ночей из 6305 в подмосковном ряду 17.08-03.09,
    то есть половина знаменателя — один отель. Медиана по объектам этого не
    делает. Ночи с `gap_days > 0` в медиану не идут (правило Р4).
    """
    rows = read_nights(out_dir) if rows is None else rows
    rows = _latest(rows, _night_sort_key)
    kept, dropped = _select(rows, None, region, month, targets, include_gaps,
                            include_quota)
    per_user: dict[str, list] = {}
    for row in kept:
        per_user.setdefault(row["username"], []).append(row)
    per_object = []
    for user in sorted(per_user):
        cuts = _tally(per_user[user])["cuts"]["all"]
        per_object.append({"username": user, "busy": cuts["busy"],
                           "known": cuts["known"], "pct": cuts["pct"]})
    pcts = [o["pct"] for o in per_object if o["pct"] is not None]
    return {
        "median_pct": round(statistics.median(pcts), 1) if pcts else None,
        "per_object": per_object,
        "objects": len(pcts),
        "gap_excluded": dropped["gap_excluded"],
        "quota_excluded": dropped["quota_excluded"],
    }


# Ниже этой доли ночей опорной точки процент не выдаётся как цифра кривой:
# на боевом слое (один объект, сентябрь) точка lead 45 стояла на ОДНОЙ
# ночи из двадцати и читалась в отчёте как «за полтора месяца до заезда
# продаж нет» — 6.1% против 46.7% рядом.
DEFAULT_MIN_COVERAGE = 0.5


def _pace_points(rows: list[dict], *,
                 min_coverage: float = DEFAULT_MIN_COVERAGE) -> list[dict]:
    """Точки кривой по lead_days, каждая — со своим основанием.

    Две вещи, которые точка обязана нести в себе, иначе кривая складывает
    несравнимое:
      * `inventory_missing` — строки, где фонд на эту ночь не спрашивался
        (тикет 06). Они в счёт не идут ВООБЩЕ: у них знаменатель «один
        домик» вместо фактических трёх, и точка получилась бы ниже правды.
      * `nights` / `nights_base` / `coverage` / `partial` — по скольким
        ночам точка посчитана против самой полной точки кривой. Точка,
        стоящая меньше чем на `min_coverage` опорного набора, отдаёт `pct`
        пустым (в отчёт такая цифра не попадёт) и держит её в `pct_partial`
        — чтобы разбор её видел, а таблица не печатала.

    Клетку за горизонтом фонда можно было бы спасать у юнита, у которого фонд
    за всю историю равен единице (там «один домик» — правда, а не заглушка),
    но фонд лежит в индексе слоя, а точки часто считают по готовым `rows` без
    каталога. Кривая, зависящая от способа вызова, хуже кривой с честной
    дырой.
    """
    by_lead: dict[int, list] = {}
    missing: dict[int, int] = {}
    for row in rows:
        lead = row["lead_days"]
        if row.get("inventory_known") is False:
            missing[lead] = missing.get(lead, 0) + 1
            continue
        by_lead.setdefault(lead, []).append(row)
    counted = {lead: len({r["night"] for r in chunk})
               for lead, chunk in by_lead.items()}
    # опорный набор — самая полная точка кривой: с ней и сравниваются
    # остальные, потому что «сколько ночей вообще бывает» знает только она
    base = max(counted.values(), default=0)
    points = []
    for lead in sorted(set(by_lead) | set(missing)):
        chunk = by_lead.get(lead, [])
        tally = _tally(chunk)
        cuts = tally["cuts"]["all"]
        nights = counted.get(lead, 0)
        coverage = round(nights / base, 3) if base else 0.0
        partial = coverage < 1.0
        point = {
            "lead_days": lead, "busy": cuts["busy"], "known": cuts["known"],
            "pct": cuts["pct"], "unknown": tally["unknown"],
            "sales_not_open": tally["sales_not_open"],
            "nights": nights, "nights_base": base, "coverage": coverage,
            "partial": partial, "pct_partial": cuts["pct"],
            "inventory_missing": missing.get(lead, 0),
        }
        if coverage < min_coverage:
            point["pct"] = None
        points.append(point)
    return points


def pace_curve(username: str, month: str, *, out_dir=None, rows=None,
               unit=None, min_coverage=DEFAULT_MIN_COVERAGE) -> list[dict]:
    """Кривая темпа объекта за месяц: занятость по lead_days (шаги LEAD_STEPS).

    lead_days — за сколько суток ДО ночи сделан снимок; lead 0 — утро самой
    ночи. Окно, в котором работает маркетинг, читается по убыванию lead.

    Читать её как единый ряд можно ТОЛЬКО по точкам с `partial=False` и
    `inventory_missing=0`: у каждого lead свой набор ночей (глубокие шаги
    сняты у меньшего числа ночей — истории просто нет) и своё основание
    (за горизонтом фонда домики в типе не сосчитаны). Точка, не дотянувшая
    до `min_coverage` ночей опорной, отдаёт `pct=None`; понизить порог
    вправе вызывающий, но тогда сравнивать точки между собой уже нельзя.
    """
    rows = read_pace(out_dir) if rows is None else rows
    rows = _latest(rows, lambda r: (r["night"], r["username"], r["unit"],
                                    r["lead_days"]))
    chunk = [r for r in rows
             if r["username"] == username and r["night"][:7] == month
             and (unit is None or r["unit"] == unit)]
    return _pace_points(chunk, min_coverage=min_coverage)


def pace_series(username: str, night: str, *, out_dir=None, rows=None,
                unit=None, min_coverage=DEFAULT_MIN_COVERAGE) -> list[dict]:
    """Как заполнялась ОДНА ночь одного объекта: точки по lead_days.

    Нужна для ручной сверки кривой с известной динамикой: спорить о средней по
    месяцу бессмысленно, пока не сошлась одна конкретная пара объект-ночь.
    """
    rows = read_pace(out_dir) if rows is None else rows
    rows = _latest(rows, lambda r: (r["night"], r["username"], r["unit"],
                                    r["lead_days"]))
    chunk = [r for r in rows
             if r["username"] == username and r["night"] == night
             and (unit is None or r["unit"] == unit)]
    # У одной ночи набор ночей во всех точках один, поэтому `partial` здесь
    # взводится только когда точку целиком съел горизонт фонда.
    return _pace_points(chunk, min_coverage=min_coverage)
