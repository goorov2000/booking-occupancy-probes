# -*- coding: utf-8 -*-
"""CLI /glamping-occupancy: fixture-режим (тикет 01) и probe-режим (тикет 02+).

Fixture-режим — скормить готовую сетку (JSON-объект по SCHEMA (б) из
occupancy_core.py, поля checked_at/status/reason опциональны — дозаполняются):
  .venv/bin/python occupancy/cli.py \
      --fixture grid.json [--fixture grid2.json ...]

Probe-режим — живой съём по рецептам реестра (пробники в probes/):
  .venv/bin/python occupancy/cli.py \
      --probe yck_kuzminskoe [--probe istracottage ...] \
      [--recipes PATH] [--date-from 2026-08-14] [--date-to 2027-08-14] \
      [--inventory-days 45] [--deadline-min 90] [--per-target-max-sec 600]
      [--host-workers 6]
Рецепт, у которого пробник увидел смену схемы/антибот, помечается в реестре
status=broken с причиной (broken_reason) — это сигнал переразведки (тикет 05).

Общее: пишет датированный снапшот на диск, печатает человеческую строку
сводки по объекту; при наличии предыдущего прогона — строку динамики.

Что изменилось 04.09.2026 (спека поюнитной сезонности)
------------------------------------------------------
- Горизонты СКОЛЬЗЯТ от сегодня (тикет 01). Конец горизонта считался из
  DEFAULT_MONTHS (авг-окт 2026), и с 01.11.2026 прогон снимал бы НОЛЬ ночей —
  проверено запуском на 05.11. Теперь сетка спрашивается на core.grid_horizon()
  (год вперёд), а фонд типов — только на ближние --inventory-days ночей: фонд
  стоит запроса на КАЖДУЮ ночь и составляет почти всю цену прогона.
- Прогон не теряет день (тикет 02). Снапшот писался одним куском в самом конце,
  и таймаут systemd на 50-й цели из 53 стоил всего дня. Теперь run.json пишется
  в начале, объект — сразу после съёма, итог — в конце. У прогона есть дедлайн
  и бюджет на объект, а необработанное исключение одного пробника даёт одну
  честную строку insufficient_data, а не падение.
- В снимке записано, чем и по какому рецепту он снят (тикет 10).
- Отказ хоста (403/429) считается сутками, а не запросами (тикет 03): один
  403 больше не помечает рецепт broken навсегда.
- Цели снимаются ПАРАЛЛЕЛЬНО ПО ХОСТАМ (тикет 05, волна 2). Замер 04.09:
  900 запросов и 1591 с на 21 цель, то есть ~87 минут на ~53 цели против
  окна таймера. Пауза приличия >= 1.2 с нужна ОДНОМУ хосту, а цели разных
  движков ждали друг друга без всякой нужды. Теперь цели группируются по
  хосту из url_template рецепта: внутри группы строго последовательно (пауза
  держится замком в probes/_common), группы — одновременно (--host-workers).
  Отчёт по объектам печатается ПОСЛЕ съёма, в порядке целей: порядок целей —
  это их важность, и перемешивать его выводом «кто первым закончил» нельзя.
- Волна 3 (ревью wave2-core2): невалидный объект ЛЮБОГО пробника больше не
  обрывает закрытие прогона — проверка схемы стоит снаружи защиты _probe_one,
  и одна клетка «занято при одном свободном» стоила дня по всем целям
  (_append_or_degrade). Блокировку прогона держит РЕЕСТР рецептов, а не
  только каталог снапшотов: замер во временный --snapshot-root шёл мимо неё.
  objects_written считает снятое, а не запланированное, и цель, до которой
  очередь не дошла, не подписывается версией пробника и хэшем рецепта.
- Волна 4 (ревью wave3-core): волна 3 закрывала ВИДЫ сбоев, эта — классы.
  * Прогон закрывается ВСЕГДА (_closing_run): любое необработанное
    исключение между open_snapshot и close_snapshot оставляло прогон без
    finished_at (слой сезонности такой в работу не берёт) и вылетало наверх,
    в процесс планировщика — то есть без сводки, ledger, дайджеста и строки
    в Run Log. Наружу из main теперь не летит ничего, кроме BaseException.
  * Замок живого съёма — ОДИН НА МАШИНУ (core.host_lock_path): он защищает
    чужие ХОСТЫ, а не каталог. Проверочный прогон в песочницу расходился с
    плановым по обоим прежним замкам (свой реестр-копия, свой каталог) и шёл
    по тем же 6 хостам вторым потоком, а пауза >= 1.2 с процессами не
    делится.
  * Снято — это стоившее запроса: рецепт broken, неподдержанный движок и
    цель без рецепта в objects_written не идут и подписи пробника не носят.
  * Бюджет на объект РЕЖЕТ запросы (transport.request_budget), а не клеит
    ярлык постфактум.
  * Дыра в ряду пишется в run.json (days_since_prev_run): догон после
    простоя машины gap_days не ловит и поймать не может.
- Волна 5 (ревью wave4-core): защита стоит там, где потери считаются НЕ
  одной целью.
  * Исключение в потоке съёма, но ВНЕ защиты _probe_one (подпись снимка,
    запись, строка в журнал), обрывало всю очередь СВОЕГО хоста: цели за
    упавшей не получали в снапшоте ни строки. На reservationsteps.ru таких
    целей 22 — теперь каждая падает поодиночке (work_queue -> fell), и день
    стоит одной строки insufficient_data, а не 22 пропавших объектов.
  * Провал ЗАКРЫТИЯ прогона больше не отдаёт код 0: незакрытый прогон слой
    сезонности не берёт никогда, а планировщик при коде 0 считал день
    удачным и писал в Run Log «success» — день терялся молча.
  * Битая ЗАПИСЬ реестра (не словарь) карантинится при чтении
    (core.quarantine_broken_entries), а не роняет прогон до снапшота.
"""
from __future__ import annotations

import argparse
import contextlib
import sys
import threading
import time
from concurrent import futures
from datetime import date, datetime
from pathlib import Path

import occupancy_core as core
import probes
# Транспортный слой пробников: из него берутся версия пробника для снимка и
# приведение чужого текста ошибки к одной строке.
from probes import _common as transport

# Дедлайн прогона — 90 мин при TimeoutStartSec=120min у cf-occupancy.service.
# Пара двигается ТОЛЬКО вместе: дедлайн должен срабатывать заметно раньше
# systemd, иначе SIGTERM прилетает вперёд штатного закрытия и снапшот
# остаётся без finished_at — «прогон оборвали» неотличимо от «прогон идёт».
# Оставшиеся полчаса — не запас на съём, а время хвоста: сводка, слой
# сезонности, телеграм и строка в Run Log.
# 08.09.2026: 90 -> 150 вместе с TimeoutStartSec 120 -> 180 (третий трек
# целей, см. run_scheduled.DEFAULT_DEADLINE_MIN).
DEFAULT_DEADLINE_MIN = 150.0
# Бюджет на объект. 600 с, а не прежние 300: после перехода на скользящий
# горизонт (тикет 01) фонд у TravelLine/Bnovo/UHotels — это запрос на КАЖДУЮ
# ночь ближнего окна, и 300 с превышал бы почти каждый такой объект, молча
# переклеивая ему ярлык «частично» при целых данных.
DEFAULT_PER_TARGET_SEC = 600.0
# Сколько хост-очередей идёт одновременно. Потолок нужен, чтобы параллелизм
# не превратился в шторм по сети машины: хостов больше, чем каналов.
DEFAULT_HOST_WORKERS = 6
LOCK_NAME = ".run.lock"


def _prepare_object(raw: dict, now_iso: str) -> dict:
    """Дозаполнить fixture-объект до полного объекта снапшота (SCHEMA (б))."""
    obj = dict(raw)
    obj.setdefault("checked_at", now_iso)
    obj.setdefault("source_urls", [])
    obj.setdefault("reason", "")
    if "status" not in obj:
        has_data = any(cells for cells in obj.get("units", {}).values())
        if has_data:
            obj["status"] = "ok"
        else:
            obj["status"] = "insufficient_data"
            obj["reason"] = obj["reason"] or "сетка пуста: нет ни одной даты"
    return obj


def _probe_one(username: str, recipe: dict, date_from: date, date_to: date,
               budget_sec: float, **extra):
    """Снять один объект. -> (obj, broken_reason | None).

    Горизонты уходят в диспетчер как есть: шим, отбрасывавший незнакомые
    диспетчеру аргументы, снят вместе с волной 2. Он делал главный рычаг цены
    прогона (--inventory-days) молчаливым no-op — а тихий no-op в ночном
    прогоне хуже громкого TypeError, который видно в первую же секунду.

    Общий except вокруг ОДНОГО объекта: любое необработанное исключение — это
    строка insufficient_data, а не потерянный прогон. BaseException
    (KeyboardInterrupt, SystemExit, таймаут systemd) наверх пропускается —
    снятое уже лежит на диске.

    Бюджет объекта не только меряется, но и РЕЖЕТ: он уходит в транспорт
    (transport.request_budget), и пробник, вышедший за него, новых запросов
    не отправляет. Прежде бюджет мерился только ПОСЛЕ возврата пробника и
    переклеивал ярлык на partial задним числом, а начатая цель шла до конца —
    и одна цель, начатая на 89-й минуте при дедлайне 90 мин и
    TimeoutStartSec=120min, съедала весь хвост прогона.
    """
    started = time.monotonic()
    try:
        with transport.request_budget(budget_sec):
            obj, broken_reason = probes.run_recipe(username, recipe, date_from,
                                                   date_to, **extra)
    except Exception as e:  # noqa: BLE001 — падение пробника стоит одной строки
        obj = probes.insufficient(
            username, recipe,
            transport.one_line(f"пробник упал необработанной ошибкой: "
                               f"{e.__class__.__name__}: {e}"))
        broken_reason = None
    elapsed = time.monotonic() - started
    if elapsed > budget_sec:
        note = (f"съём занял {elapsed:.0f} с при бюджете {budget_sec:.0f} с — "
                f"остаток запросов не отправлен, данные объекта неполные")
        if obj.get("status") == "ok":
            obj["status"] = "partial"
        obj["reason"] = "; ".join(x for x in (obj.get("reason"), note) if x)
    return obj, broken_reason


def _account_recipe(recipes: dict, username: str, recipe: dict, obj: dict,
                    broken_reason) -> bool:
    """Отразить исход съёма в рецепте. -> менялся ли реестр.

    Три исхода: смена схемы/4xx ломает рецепт сразу; «нас не пустили» копит
    сутки (core.note_refusal) и ломает только когда отказ повторился подряд;
    удачный съём обнуляет счётчик отказов.
    """
    if broken_reason:
        probes.mark_recipe_broken(recipes, username, broken_reason)
        print(f"рецепт {username} помечен broken: {broken_reason}",
              file=sys.stderr)
        return True
    if recipe is None:
        return False
    refusal = obj.get("refusal")
    if refusal:
        reason = refusal.get("reason") or "нас не пустили"
        broken = core.note_refusal(recipe, reason)
        if broken:
            probes.mark_recipe_broken(recipes, username, broken)
            print(f"рецепт {username} помечен broken: {broken}",
                  file=sys.stderr)
        else:
            print(f"{username}: {reason} — рецепт не трогаем, "
                  f"отказ {recipe['refusals']['count']} сут. подряд",
                  file=sys.stderr)
        return True
    if obj.get("status") in ("ok", "partial"):
        return core.clear_refusals(recipe)
    return False


def _registry_lock_path(recipes_path) -> Path:
    """Файл блокировки РЕЕСТРА рецептов — рядом с самим реестром.

    Почему не каталог снапшотов: блокировка защищает recipes.json, а прогон
    во ВРЕМЕННЫЙ --snapshot-root (замер тикета 05, окно таймера 06:43 плюс
    RandomizedDelaySec) проходил мимо неё и писал тот же реестр параллельно с
    плановым. Оба процесса читают реестр в начале и переписывают целиком в
    конце — последний писатель молча стирает чужие правки: счётчики отказов,
    broken_reason, снятые clear_refusals. Атомарность записи от этого не
    спасает: файл целый, потеряно обновление.
    """
    path = Path(recipes_path)
    return path.with_name(f".{path.name}.lock")


def _append_or_degrade(snap_dir, obj: dict, username: str, recipe,
                       stamp=None) -> dict:
    """Записать снятый объект; не прошёл проверку схемы -> честная строка.

    Проверка схемы живёт в core.append_object, то есть СНАРУЖИ общего except
    вокруг съёма одного объекта (_probe_one). Одна клетка вида state=busy при
    units_free=1 роняла поток очереди, а вместе с ним и весь хвост прогона:
    run.json оставался без finished_at, реестр рецептов не сохранялся, сводка
    и динамика не печатались, cli отдавал 2 — и плановый прогон при коде >= 2
    не собирал сводку, не писал слой сезонности и не оставлял строки в Run
    Log. Один плохой объект стоил дня по ВСЕМ целям, ровно того, против чего
    писался тикет 02.

    Рецепт при этом НЕ ломается: broken — работа переразведки, а не реакция
    на одну невалидную клетку чужого движка.
    """
    try:
        core.append_object(snap_dir, obj)
        return obj
    except core.SnapshotError as e:
        fixed = probes.insufficient(
            username, recipe or {},
            transport.one_line(
                f"объект не прошёл проверку схемы снапшота: {e}"))
        if stamp is not None:
            stamp(fixed)
        core.append_object(snap_dir, fixed)
        print(f"{username}: объект забракован проверкой схемы — {e}",
              file=sys.stderr)
        return fixed


def _recipe_host(recipe) -> str:
    """Хост рецепта — ключ очереди прогона (пусто, если рецепта нет).

    Группируем по ХОСТУ, а не по движку: у bnovo и варианта reservationsteps
    хост общий (бить его двумя очередями нельзя), а у TravelLine Integration
    свой. Пауза приличия принадлежит хосту, а не названию движка.

    Ключ очереди — не проверка рецепта: рецепты пишет разведчик и правит
    рука, и request списком вместо словаря здесь стоил бы AttributeError
    ВСЕГО прогона (ревью wave3-core). Непонятный рецепт получает пустой ключ
    и свою честную строку, а не роняет остальные 52 цели.
    """
    if not isinstance(recipe, dict):
        return ""
    request = recipe.get("request")
    url = request.get("url_template", "") if isinstance(request, dict) else ""
    return transport.host_of(url) if isinstance(url, str) else ""


def _probe_reaches_host(recipe) -> bool:
    """Пойдёт ли пробник в сеть по этому рецепту.

    Три ветки диспетчера отвечают insufficient_data, НЕ сделав ни одного
    запроса: рецепта нет, движок не поддержан, рецепт помечен broken
    (probes.run_recipe). Такую цель нельзя ни считать снятой
    (objects_written), ни подписывать версией пробника и хэшем рецепта: при
    разборе ряда через полгода подписанный снимок неотличим от настоящего, а
    ровно эту неотличимость поля провенанса и заводились убрать (тикет 10).
    Завтра это уже сработало бы: shale_aframe идёт в очередь с рецептом
    status=broken (24 снимка insufficient_data подряд).
    """
    if not isinstance(recipe, dict):
        return False
    if recipe.get("status") == "broken":
        return False
    return recipe.get("engine", "") in probes.ENGINES


def _host_queues(usernames: list[str], recipes: dict) -> list[list[str]]:
    """Цели -> очереди по хосту. Порядок целей внутри очереди сохранён."""
    queues: dict[str, list[str]] = {}
    for username in usernames:
        queues.setdefault(_recipe_host(recipes.get(username)), []).append(
            username)
    return list(queues.values())


def _report(obj: dict, months: list[str], prev_objects: dict,
            prev_name) -> None:
    """Человеческая строка объекта и, если есть с чем, строка динамики."""
    print(core.summary_line(obj, months))
    prev = prev_objects.get(obj["username"])
    if prev is not None:
        diff = core.diff_units(obj.get("units", {}), prev.get("units", {}),
                               months)
        print(core.diff_line(diff, prev_name))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="glamping-occupancy",
        description="Снапшот загрузки глэмпингов: fixture-режим или живой "
                    "съём по рецептам (--probe).")
    parser.add_argument("--fixture", action="append", default=[],
                        metavar="FILE", help="JSON-сетка объекта (можно несколько)")
    parser.add_argument("--probe", action="append", default=[],
                        metavar="USERNAME",
                        help="снять объект живьём по рецепту из реестра")
    parser.add_argument("--recipes", default=str(core.DEFAULT_RECIPES),
                        help="реестр рецептов (дефолт: agent-runtime/.../occupancy/recipes.json)")
    parser.add_argument("--date-from", default=None,
                        help="первая дата сетки YYYY-MM-DD (дефолт: сегодня)")
    parser.add_argument("--date-to", default=None,
                        help="последняя дата сетки YYYY-MM-DD (дефолт: "
                             "скользящий горизонт сегодня + 12 месяцев)")
    parser.add_argument("--inventory-days", type=int,
                        default=core.DEFAULT_INVENTORY_DAYS,
                        help="на сколько ближних ночей спрашивать ФОНД типа "
                             "(сколько домиков категории свободно). Главный "
                             "рычаг цены прогона: фонд стоит запроса на "
                             "каждую ночь")
    parser.add_argument("--snapshot-root",
                        default=str(core.DEFAULT_SNAPSHOT_ROOT),
                        help="каталог снапшотов (дефолт: agent-runtime/.../occupancy/snapshots)")
    parser.add_argument("--run-id", default=None,
                        help="имя прогона YYYY-MM-DD-HHMM (дефолт: сейчас)")
    parser.add_argument("--planned-at", default=None,
                        help="плановое время прогона ISO8601 (дефолт: старт). "
                             "Отставание от него пишется в снимок как "
                             "gap_days: ночь, снятая с опозданием, в рыночную "
                             "медиану не идёт")
    parser.add_argument("--months", default=None,
                        help="месяцы сводки через запятую, YYYY-MM (дефолт: "
                             "скользящее окно от текущего месяца)")
    parser.add_argument("--deadline-min", type=float,
                        default=DEFAULT_DEADLINE_MIN,
                        help="дедлайн прогона в минутах: цели, до которых "
                             "очередь не дошла, получают честную причину, а "
                             "прогон закрывается штатно")
    parser.add_argument("--per-target-max-sec", type=float,
                        default=DEFAULT_PER_TARGET_SEC,
                        help="бюджет на объект в секундах: превысил — объект "
                             "помечается partial, прогон идёт дальше")
    parser.add_argument("--host-workers", type=int,
                        default=DEFAULT_HOST_WORKERS,
                        help="сколько хостов снимается одновременно. Внутри "
                             "хоста цели всегда идут последовательно с "
                             "паузой; 1 — прежний последовательный прогон")
    parser.add_argument("--no-inventory", action="store_true",
                        help="не снимать фонд типов (сколько номеров категории "
                             "свободно). Быстро, но занятость считается по "
                             "типам и занижена там, где одинаковых домиков "
                             "несколько — TravelLine и Bnovo платят за фонд "
                             "одним запросом на ночь горизонта")
    return parser


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.fixture and not args.probe:
        parser.error("нужен хотя бы один --fixture или --probe")

    now = datetime.now().astimezone()
    started_iso = now.isoformat(timespec="seconds")
    run_id = args.run_id or now.strftime("%Y-%m-%d-%H%M")
    planned_iso = args.planned_at or started_iso
    months = ([m.strip() for m in args.months.split(",") if m.strip()]
              if args.months else core.summary_months())
    try:
        date_from = (date.fromisoformat(args.date_from) if args.date_from
                     else core.today())
        date_to = (date.fromisoformat(args.date_to) if args.date_to
                   else core.grid_horizon())
    except ValueError as e:
        print(f"ошибка: даты --date-from/--date-to не YYYY-MM-DD ({e})",
              file=sys.stderr)
        return 2
    # Фонд ближе сетки всегда: спрашивать «сколько домиков свободно» на год
    # вперёд — это запрос на каждую ночь года, то есть прогон длиной в сутки.
    inventory_date_to = min(
        date_to, core.inventory_horizon(date_from, args.inventory_days))

    root = Path(args.snapshot_root)
    try:
        # Замка три, и держат они РАЗНОЕ: чужие ХОСТЫ (один на машину),
        # реестр рецептов (общий у любых двух прогонов) и каталог снапшотов
        # (свой у каждого --snapshot-root). Замка на хосты не было, и
        # проверочный прогон в песочницу расходился с плановым по обоим
        # остальным — реестр он берёт копией, снапшоты пишет к себе, — то
        # есть шёл по тем же 6 хостам вторым потоком без общей паузы.
        # Все неблокирующие, так что встречный порядок захвата даёт честный
        # отказ, а не взаимоблокировку.
        with contextlib.ExitStack() as locks:
            if args.probe:
                locks.enter_context(core.run_lock(
                    core.host_lock_path(),
                    note="живой съём уже идёт на этой машине — паузу "
                         ">= 1.2 с к чужому хосту процессы не делят, и "
                         "второй поток бьёт хост вдвое чаще правила"))
                locks.enter_context(
                    core.run_lock(_registry_lock_path(args.recipes)))
            locks.enter_context(core.run_lock(root / LOCK_NAME))
            return _run(args, root, run_id, started_iso, planned_iso, months,
                        date_from, date_to, inventory_date_to)
    except core.RunLockError as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 2
    except (core.SnapshotError, core.RegistryError) as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 — см. ниже
        # Наружу не летит НИЧЕГО, кроме BaseException. cli.main зовётся в том
        # же процессе, что и планировщик (run_scheduled: code = probe(argv)),
        # и необработанное исключение там — это не код 2, а traceback вместо
        # всего хвоста: сводка не пересобирается, ledger не пишется, дайджест
        # не уходит, строки в CF Run Log нет (правило №6 завода).
        print(f"ошибка: прогон оборвался необработанной ошибкой — "
              f"{transport.one_line(f'{e.__class__.__name__}: {e}')}",
              file=sys.stderr)
        return 2


def _previous_objects(prev_dir) -> dict:
    """Объекты прошлого прогона — только ради строки динамики.

    Битый вчерашний снапшот (оборванная запись, правка руки) не должен
    стоить сегодняшнего прогона: динамика — это приписка «стало на 4 п.п.
    больше», а съём — сами данные. Читается он ДО открытия своего каталога,
    так что закрывать при таком отказе было бы нечего.
    """
    if prev_dir is None:
        return {}
    try:
        return core.read_snapshot(prev_dir)["objects"]
    except (core.SnapshotError, OSError) as e:
        print(f"прошлый прогон {prev_dir.name} не читается ({e}) — "
              f"строки динамики не будет", file=sys.stderr)
        return {}


@contextlib.contextmanager
def _closing_run(snap_dir: Path, stats: dict, outcome: dict):
    """Прогон ЗАКРЫВАЕТСЯ всегда, чем бы тело ни оборвалось.

    Незакрытый прогон (run.json без finished_at) слой сезонности в работу не
    берёт — и правильно: отличить «оборвали» от «идёт прямо сейчас» больше
    нечем. Поэтому необработанное исключение между open_snapshot и
    close_snapshot стоило дня по ВСЕМ целям сразу, ровно того, против чего
    писался тикет 02. Волна 3 закрыла один ВИД сбоя (брак проверки схемы у
    объекта), а не класс: рецепты пишет разведчик и правит рука, и любая
    неожиданная форма данных выносила прогон другой дверью.

    Исключение не глотается молча: его текст ложится в run.json полем error
    и уходит в stderr (журнал systemd), а прогон отдаёт код 2.

    BaseException (SIGTERM от systemd, Ctrl+C) наверх пропускается и прогон
    НЕ закрывает: «оборвали снаружи» и «закрылся сам» — разные события, и
    метка finished_at на убитом прогоне была бы враньём.

    Провал САМОГО закрытия (битый run.json, кончилось место, снятый том)
    ложится в outcome["close_error"] и стоит прогону кода 2. Прежде он
    печатался в stderr и отдавал 0: планировщик считал день удачным —
    пересобирал сводку, писал ledger и строку «success» в Run Log, — а слой
    сезонности незакрытый прогон не берёт НИКОГДА (ledger читает
    finished_at). День терялся молча, то есть худшим из способов.
    """
    try:
        yield
    except Exception as e:  # noqa: BLE001 — день данных дороже трассировки
        stats["error"] = transport.one_line(f"{e.__class__.__name__}: {e}")
        print(f"ошибка: прогон оборвался необработанной ошибкой — "
              f"{stats['error']}", file=sys.stderr)
    try:
        core.close_snapshot(snap_dir, stats)
    except Exception as e:  # noqa: BLE001
        outcome["close_error"] = transport.one_line(f"{e}")
        print(f"ошибка: прогон НЕ ЗАКРЫТ (run.json без finished_at) — "
              f"{outcome['close_error']}. Слой сезонности такой прогон в "
              f"работу не возьмёт: снятое лежит в {snap_dir}",
              file=sys.stderr)


def _run(args, root: Path, run_id: str, started_iso: str, planned_iso: str,
         months: list[str], date_from: date, date_to: date,
         inventory_date_to: date) -> int:
    """Тело прогона под взятой блокировкой. -> код возврата."""
    fixtures = [_prepare_object(core.load_json(path), started_iso)
                for path in args.fixture]
    recipes = core.load_recipes(args.recipes) if args.probe else {}
    # Повтор цели в аргументах — это один объект и один файл снапшота.
    targets = list(dict.fromkeys(args.probe))
    planned = [obj["username"] for obj in fixtures] + targets

    prev_dir = core.find_previous_snapshot(root, run_id)
    prev_objects = _previous_objects(prev_dir)

    snap_dir = core.open_snapshot(root, run_id, {
        "started_at": started_iso, "planned_at": planned_iso,
        "targets": planned})
    print(f"снапшот: {snap_dir}")

    # Итог прогона копится ПО ХОДУ и дописывается в run.json при закрытии —
    # в том числе аварийном. Прогон, оборвавшийся на середине, честно
    # говорит, сколько целей успел снять.
    stats: dict = {"objects_written": 0, "targets_planned": len(planned),
                   "targets_late": []}
    if prev_dir is not None:
        stats["prev_run_id"] = prev_dir.name
        # Молчание ряда: догон после простоя машины виден только здесь —
        # gap_days его не ловит (см. core.gap_days).
        since = core.days_between_runs(prev_dir.name, run_id)
        if since is not None:
            stats["days_since_prev_run"] = since
    code = 0
    outcome: dict = {}
    with _closing_run(snap_dir, stats, outcome):
        code = _snap_targets(args, snap_dir, fixtures, recipes, targets,
                             months, prev_objects, prev_dir, started_iso,
                             planned_iso, date_from, date_to,
                             inventory_date_to, stats)
    if outcome.get("close_error") or stats.get("error"):
        return 2
    return code


def _snap_targets(args, snap_dir: Path, fixtures: list, recipes: dict,
                  targets: list, months: list, prev_objects: dict, prev_dir,
                  started_iso: str, planned_iso: str, date_from: date,
                  date_to: date, inventory_date_to: date,
                  stats: dict) -> int:
    """Съём всех целей под открытым снапшотом. -> код возврата.

    Отделено от _run ровно затем, чтобы закрытие прогона (_closing_run) не
    зависело ни от одной строки этого тела.
    """
    deadline = time.monotonic() + args.deadline_min * 60.0
    results: dict[str, tuple] = {}
    late: set[str] = set()
    # Цели, оборвавшиеся вне защиты пробника: строка на диске у них есть,
    # съёма — нет (см. fell).
    degraded: set[str] = set()
    # Один замок на запись снапшота и на общие списки: потоков столько же,
    # сколько хостов, а диск и словари у них общие.
    guard = threading.Lock()

    for obj in fixtures:
        core.stamp_provenance(obj, run_planned_at=planned_iso,
                              started_at=started_iso)
        core.append_object(snap_dir, obj)
        stats["objects_written"] += 1
        _report(obj, months, prev_objects, prev_dir.name if prev_dir else "")

    def snap_one(username: str) -> None:
        """Снять цель и сразу положить её на диск (в потоке своего хоста)."""
        began = time.monotonic()
        recipe = recipes.get(username)
        # Трогал ли цель пробник — для подписи снимка и для счёта снятого.
        probed = _probe_reaches_host(recipe)
        if recipe is None:
            obj, broken_reason = probes.insufficient(
                username, {}, "рецепта нет в реестре — нужна разведка"), None
        elif time.monotonic() >= deadline:
            probed = False
            with guard:
                late.add(username)
            obj, broken_reason = probes.insufficient(
                username, recipe,
                f"не дошла очередь до дедлайна прогона "
                f"({args.deadline_min:.0f} мин)"), None
        else:
            obj, broken_reason = _probe_one(
                username, recipe, date_from, date_to,
                args.per_target_max_sec,
                inventory=not args.no_inventory,
                inventory_date_to=inventory_date_to)

        def stamp(target: dict) -> dict:
            """Подписать снимок: чем и по какому рецепту снят (тикет 10).

            Цель, которой пробник не касался, версией пробника и хэшем
            рецепта не подписывается: при разборе ряда через полгода такой
            снимок был бы неотличим от настоящего снятого, а ровно эту
            неотличимость поля и заводились убрать. Не касался он четырёх:
            рецепта нет, очередь не дошла до дедлайна, рецепт broken, движок
            не поддержан (_probe_reaches_host). К прогону снимок привязан в
            любом случае (run_planned_at).
            """
            engine = (recipe or {}).get("engine", "") if probed else ""
            return core.stamp_provenance(
                target, recipe=recipe if probed else None,
                probe_version=transport.probe_version_of(
                    engine, probes.ENGINES.get(engine)) if engine else None,
                run_planned_at=planned_iso, started_at=started_iso)

        stamp(obj)
        # Запись СРАЗУ после съёма (тикет 02): оборванный прогон оставляет
        # на диске всё, что успел снять, а не теряет день целиком.
        with guard:
            obj = _append_or_degrade(snap_dir, obj, username, recipe,
                                     stamp=stamp)
            results[username] = (obj, broken_reason)
            if probed:
                # Снято — это стоившее хотя бы одного запроса. Цель без
                # рецепта, с рецептом broken, с неподдержанным движком и
                # опоздавшая к дедлайну файл на диске получают, но в счёт
                # снятого не идут: иначе расхождение «планировали 21, сняли
                # 20», ради которого поле заводилось, из снапшота не читается.
                stats["objects_written"] += 1
            # Ход прогона — в stderr (журнал systemd): отчёт по объектам
            # печатается только в конце, и без этой строки полтора часа
            # ночного прогона выглядят как зависший процесс. Заодно это
            # готовый замер «хост -> секунды» из боевого прогона.
            print(f"снято {username} [{_recipe_host(recipe) or 'без рецепта'}]"
                  f": {obj['status']} за {time.monotonic() - began:.0f} с",
                  file=sys.stderr)

    def fell(username: str, error: Exception) -> None:
        """Цель, оборвавшаяся ВНЕ защиты _probe_one, -> честная строка.

        Общий except волны 2 стоит вокруг одного вызова пробника, а после
        него в потоке съёма есть ещё подпись снимка, запись на диск и строка
        в журнал. Исключение оттуда уносило ВСЮ очередь своего хоста: цели
        за упавшей не получали в снапшоте ни съёма, ни причины — их просто
        не было, и сводка читала это как «объект не снимался», не отличая от
        «объекта нет в плане». На reservationsteps.ru в одной очереди 22
        цели (реестр 04.09), то есть цена ОДНОГО исключения — 22 объекта.

        Рецепт при этом НЕ ломается: обрыв в нашем коде — не смена схемы
        чужого движка и не повод слать рецепт на переразведку.
        """
        reason = transport.one_line(
            f"цель оборвалась ошибкой съёмщика вне защиты пробника: "
            f"{error.__class__.__name__}: {error}")
        print(f"ошибка: {username}: {reason} — очередь хоста идёт дальше",
              file=sys.stderr)
        recipe = recipes.get(username)
        recipe = recipe if isinstance(recipe, dict) else {}
        with guard:
            degraded.add(username)
            written = username in results
        if written:
            # Цель успела лечь на диск, а упало то, что после неё (строка в
            # журнал). Снятое данными дороже строки об обрыве — не трогаем.
            return
        try:
            obj = probes.insufficient(username, recipe, reason)
            with guard:
                results[username] = (
                    _append_or_degrade(snap_dir, obj, username, recipe), None)
        except Exception as e:  # noqa: BLE001 — тогда хотя бы имя в run.json
            print(f"ошибка: {username}: строку об обрыве записать не удалось "
                  f"({e})", file=sys.stderr)

    def work_queue(queue: list[str]) -> None:
        """Очередь одного хоста: строго последовательно, как раньше.

        Падение ОДНОЙ цели дальше своей строки не идёт (fell): очередь — это
        22 чужих объекта, а не один.
        """
        for username in queue:
            try:
                snap_one(username)
            except Exception as e:  # noqa: BLE001 — см. fell()
                fell(username, e)

    broken_queues: list[str] = []
    queues = _host_queues(targets, recipes)
    if queues:
        workers = max(1, min(args.host_workers, len(queues)))
        with futures.ThreadPoolExecutor(max_workers=workers,
                                        thread_name_prefix="occ") as pool:
            pending = [pool.submit(work_queue, queue) for queue in queues]
        for future in pending:
            try:
                future.result()
            except Exception as e:  # noqa: BLE001
                # Упавшая очередь одного хоста не должна стоить отчёта,
                # реестра и закрытия по остальным пяти: её сбой — строка в
                # журнале и код 2, а не конец прогона.
                broken_queues.append(
                    transport.one_line(f"{e.__class__.__name__}: {e}"))
                print(f"ошибка: очередь хоста оборвалась — "
                      f"{broken_queues[-1]}", file=sys.stderr)

    # Реестр, отчёт и динамика — в главном потоке и в порядке целей: порядок
    # целей это их важность, и он не должен зависеть от того, чей хост
    # ответил первым.
    for username in targets:
        snapped = results.get(username)
        if snapped is None:
            continue          # очередь этой цели оборвалась — сказано выше
        obj, broken_reason = snapped
        if _account_recipe(recipes, username, recipes.get(username), obj,
                           broken_reason):
            core.save_recipes(args.recipes, recipes)
        _report(obj, months, prev_objects, prev_dir.name if prev_dir else "")

    overdue = [name for name in targets if name in late]
    stats["targets_late"] = overdue
    fallen = [name for name in targets if name in degraded]
    if fallen:
        # В run.json, а не только в stderr: журнал systemd ротируется, а
        # снапшот живёт годами вместе с рядом сезонности — и «эта цель
        # оборвалась» надо будет прочитать при разборе ряда, а не при
        # разборе инцидента.
        stats["targets_degraded"] = fallen
    if broken_queues:
        stats["error"] = "; ".join(broken_queues)
    if overdue:
        print(f"ошибка: до дедлайна прогона не дошла очередь до "
              f"{len(overdue)} целей: {', '.join(overdue)}", file=sys.stderr)
        return 1
    # Код 1, а не 2: снапшот полон и валиден, сводку, ledger и Run Log
    # гасить нельзя (при коде >= 2 планировщик их не делает) — но и молчать
    # об исключении в своём коде нельзя, это дефект, а не свойство хоста.
    return 1 if fallen else 0


if __name__ == "__main__":
    sys.exit(main())
