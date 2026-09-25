# -*- coding: utf-8 -*-
"""Пробники движков: рецепт из реестра -> объект снапшота (SCHEMA (б) ядра).

Диспетчер по полю engine рецепта. Каждый модуль движка обязан дать функцию
    probe(username, recipe, date_from, date_to, fetch=None, inventory=True,
          inventory_date_to=None)
        -> (obj, broken_reason | None)
где obj — полный объект снапшота (units/granularity/status/reason/...),
а broken_reason — непустая строка, когда рецепт надо пометить broken
(смена схемы ответа, 4xx кроме 403/429). Сетевой сбой (таймаут, обрыв, 5xx
с ЛЮБЫМ телом — страница 502 от nginx JSON-ом не приходит никогда) рецепт НЕ
ломает: объект получает insufficient_data/partial с причиной, рецепт
остаётся ok — переразведка не нужна, нужен повтор прогона. Правила исхода
(что ответ делает с рецептом) живут в probes/_outcome.py одни на все шесть
движков; probes/_common.py — транспорт, он про судьбу рецепта не знает.

Отдельный исход — «нас не пустили» (403/429/страница-заслон, тикет 03):
рецепт тоже НЕ ломается, объект несёт поле refusal, а счётчик суток подряд
и решение «пора ломать» живут в CLI (occupancy_core.note_refusal). Один 403
чужого WAF при 53 целях выбивал бы рецепты пачками.

Объект снапшота несёт ещё несколько полей, которые ставит этот слой:
    inventory_until — до какой ночи спрашивался ФОНД (тикет 06); месяцы за
        этой границей сводка помечает «фонд не снимался», а не «движок не
        отдал» — это разные вещи. Поле объявляется ВСЕГДА (ревью волны 4):
        когда фонд не спрашивался вовсе (inventory=False, рецепт без
        категорий), граница равна date_from — ноль ночей фонда. Молчание
        поля сводка читает как «фонд снят везде», и объект без единой ночи
        фонда выглядел как объект с полным. Граница эта ползёт КАЖДЫЙ ДЕНЬ
        (фонд идёт на ближние 45 ночей), то есть основание счёта одной и той
        же ночи вчера и сегодня разное, — потребитель, который сравнивает
        два снапшота, обязан сверяться с ней, иначе смена основания читается
        как продажа;
    grid_until — до какой ночи спрашивалась ДОСТУПНОСТЬ. У большинства
        движков глубина сетки бесплатна и граница совпадает с концом
        горизонта, у uhotels и ветки min_stay Bnovo ночь сетки стоит
        запроса и граница совпадает с фондом. Без этого поля месяц за
        краем съёма читался сводкой как обычное «нет данных», то есть
        «не спрашивали» путалось с «движок не ответил»;
    reason/sales_not_open — разметка закрытого окна продаж и предохранитель
        минимального срока (тикет 07, apply_sales_window_rules ниже);
    sales_window — что именно эта разметка сделала: правило, с какой ночи,
        во что и сколько клеток и ночей переписано. Сводке нужно отличать
        ночь, которую движок и правда не открывал, от ночи, размеченной
        правилом, — иначе процент месяца печатается по одной ночи из 31
        без оговорки (ревью wave2-probes).

Железные правила пробников: ничего не бронировать, формы не сабмитить,
капчи и антибот не обходить; один запрос — одна попытка (никаких ретраев),
пауза >= 1.2 с между запросами к одному хосту (общий трекер хоста —
probes/_common.py, действует и МЕЖДУ объектами одного прогона), щадящие
таймауты.
"""
from __future__ import annotations

from datetime import date, datetime as _datetime, timedelta
from typing import Optional

import occupancy_core as _core

from . import bnovo, bronirui, homereserve, litepms, travelline, uhotels
from . import bookonline24, frontdesk24, rc_bookings, sutochno
from . import _common
from ._common import _base_object

# Четыре движка v1 по спеке (тикеты 02-04) плюс HomeReserve, добавленный
# 15.08 разведкой хвоста топ-24: у двух объектов списка (chill_house__barn,
# bani_na_ozerah) один и тот же движок realtycalendar, и он отдаёт календарь
# обычным POST без браузера. UHotels добавлен 16.08 разведкой pineriver_hotel
# (запрос заказчика «сколько домиков и какая загрузка»): его API отдаёт остаток
# домиков категории тем же ответом, что и доступность.
# bnovo — алиас варианта reservationsteps (одна платформа, см. bnovo.py).
ENGINES = {
    "travelline": travelline,
    "bnovo": bnovo,
    "bronirui": bronirui,
    "litepms": litepms,
    "homereserve": homereserve,
    "uhotels": uhotels,
    "bookonline24": bookonline24,
    "frontdesk24": frontdesk24,
    "rc-bookings": rc_bookings,
    "sutochno": sutochno,
}


# Порог «стены»: сколько ночей подряд без единой свободной клетки считать
# закрытым окном продаж, а не аншлагом (тикет 07, стартовое значение).
SALES_WALL_MIN_NIGHTS = 30

# Правило волны 3 (ревью wave2-probes, блокер 1): стена не переписывает
# ближние ночи. Первая редакция смотрела только на конец горизонта, и у
# объекта с коротким окном продаж хвост начинался уже в следующем месяце.
#
# Редакция 04.09 (вечер): граница — РАССТОЯНИЕ В СУТКАХ от начала сетки, а
# не окно сводки. Прежняя привязка к окну сводки давала стене фору в 2-4
# месяца, и на самарской партии это вылезло ложью прямо в главную цифру:
# шесть объектов из 29 снятых показали «октябрь 100%, ноябрь 100%» при
# сентябре в 6-38%. Глэмпинг не распродаёт октябрь целиком, не продав
# сентябрь, — это неоткрытые продажи, и в ряде сезонности такая цифра не
# просто неверна, она переворачивает вывод: октябрь становится пиком сезона.
#
# Какая ошибка дешевле. Занятость и без того «оценка сверху»: занято и
# закрыто владельцем в виджете неотличимы. Месяц, где ВСЕ клетки ВСЕХ
# юнитов заняты и нет ни одной свободной, — вырожденный случай, где эта
# оценка не несёт информации вовсе. Показать «продажи не открыты» и убрать
# месяц из знаменателя — потеря; показать 100% занятости — выдумка. Для
# задачи про сезонность выдумка дороже.
#
# 21 сутки — расстояние, на котором настоящий аншлаг ещё правдоподобен
# (ближний уик-энд действительно бывает выкуплен целиком), а сплошная стена
# до конца горизонта уже нет. Остальные четыре условия правила не тронуты:
# плотность, полнота съёма, 14 ночей живых продаж до стены и молчание там,
# где движок сам назвал границу окна.
SALES_WALL_FLOOR_DAYS = 21

# Хвост должен быть ПЛОТНЫМ: доля ночей с busy внутри стены. 30 занятых
# ночей, размазанных дырами по полугоду, — обычные брони, а не стена.
SALES_WALL_MIN_BUSY_SHARE = 0.8

# Доля НЕ-unknown клеток внутри стены. Юнит, до которого пробник не дошёл,
# в объединении по юнитам не даёт free, и неполный снимок читался бы как
# закрытое окно продаж (живой пример: chekhovapi 04.09, status=partial,
# одна категория из 14 упала на сетевом сбое). При 13 юнитах из 14 доля
# 0,93 — правило работает; при половине снятых юнитов молчит.
SALES_WALL_MIN_KNOWN_SHARE = 0.8

# Сколько ночей живых продаж должно быть ДО стены. Окно продаж «закрылось»
# только у того, у кого оно было открыто: объект, продающий три ночи и
# дальше стоящий стеной, — это объект без продаж, и догадываться не за что.
SALES_WALL_MIN_LIVE_NIGHTS = 14


# Имена, под которыми транспорт отдаёт тело последнего ответа (контракт
# волны 3, ревью wave2-probes). Cloudflare и DDoS-Guard отдают страницу-заслон
# с кодом HTTP 200 и HTML-телом: у JSON-пробника это data=None, и без текста
# тела каскад читал такой ответ как «схема сменилась» и слал живой рецепт на
# переразведку. classify_response умеет опознать заслон по телу с самого
# начала (аргумент body_text), но передать его было некому.
_BODY_ACCESSORS = ("last_body_text", "last_body")


def body_text_of(fetch) -> str:
    """Текст тела последнего ответа этого fetch. Не отдаёт транспорт -> ''.

    Единственное место, где пробники спрашивают тело: сам аксессор живёт в
    транспорте (probes/_common.py), и заводить его копию в шести модулях
    значит повторить историю с тремя копиями одного признака. Пусто — не
    беда: заслон тогда просто не опознаётся по телу, как было до тикета 03,
    и всё остальное работает как раньше.
    """
    for name in _BODY_ACCESSORS:
        getter = getattr(_common, name, None)
        if getter is not None:
            text = _read_body(getter, fetch)
            if text:
                return text
        attr = getattr(fetch, name, None)
        if attr is not None:
            text = _read_body(attr)
            if text:
                return text
    return ""


def _read_body(source, *args) -> str:
    """Вызвать аксессор или взять готовую строку; чужой сбой не роняет съём.

    Аксессор транспорта может как принимать fetch (когда тело помнит сам
    fetch), так и не принимать его вовсе (когда тело помнит модуль) — обе
    формы годятся, тело здесь необязательное удобство, а не данные съёма.
    """
    if not callable(source):
        return source if isinstance(source, str) else ""
    for call_args in (args, ()):
        try:
            value = source(*call_args)
        except TypeError:
            continue
        except Exception:                # noqa: BLE001 - тело необязательно
            return ""
        return value if isinstance(value, str) else ""
    return ""


def _grid_nights(units: dict) -> list[str]:
    """Все ночи сетки объекта по возрастанию (объединение по юнитам)."""
    return sorted({night for cells in units.values() for night in cells})


def _states_by_night(units: dict) -> dict:
    """{ночь: множество состояний} — один проход по сетке вместо N проходов.

    Сетка выросла с 58 ночей до 366 (тикет 01), а юнитов у объекта бывает
    27: перебирать её заново на каждую проверку правила стало дорого.
    """
    out: dict[str, set] = {}
    for cells in units.values():
        for night, cell in cells.items():
            out.setdefault(night, set()).add(cell.get("state"))
    return out


def sales_wall_floor(origin: str,
                     floor_days: int = SALES_WALL_FLOOR_DAYS) -> str:
    """Первая ночь, которую правилу «стены» разрешено переписывать.

    Это origin + floor_days суток. Ближе этой границы «ни одной свободной
    ночи ни у одного юнита» ещё может быть настоящим аншлагом; дальше —
    объясняется закрытым окном продаж, и показывать там 100% занятости
    значит выдумывать пик сезона на пустом месте.
    """
    return (date.fromisoformat(origin) + timedelta(days=floor_days)).isoformat()


def sales_wall_since(units: dict,
                     min_nights: int = SALES_WALL_MIN_NIGHTS,
                     *, not_before: Optional[str] = None,
                     min_live_nights: int = SALES_WALL_MIN_LIVE_NIGHTS
                     ) -> Optional[str]:
    """Первая ночь стены — закрытого окна продаж. Иначе None.

    Стена ищется на ЗНАЕМОЙ части сетки: от последней ночи, про которую
    движок хоть что-то сказал, назад до последней ночи со свободной клеткой.
    Ночи за краем ответа движка (сплошной unknown в конце горизонта) стеной
    не считаются — это «не спрашивали/не ответил», а не «продажи закрыты».

    Пять условий, каждое из живой ошибки (ревью wave2-probes):
    1. движок НЕ разметил конец сам: если последняя знаемая ночь уже
       sales_not_open (так делают TravelLine за availability_max_date и
       litepms), границу окна он знает лучше правила, а выкупленные ночи
       перед ней — это продажи, а не догадка;
    2. в стене не меньше min_nights ночей с busy;
    3. стена плотная (SALES_WALL_MIN_BUSY_SHARE) — дырявый хвост это брони;
    4. стена снята почти целиком (SALES_WALL_MIN_KNOWN_SHARE) — иначе
       правило читает не окно продаж, а собственный сетевой сбой;
    5. до стены есть SALES_WALL_MIN_LIVE_NIGHTS ночей с живыми продажами.

    not_before — ночь, раньше которой стену засчитывать нельзя (окно
    сводки, см. sales_wall_floor). Начало стены поднимается до неё, и
    условие 2 проверяется уже по поднятому началу: месяцы, которые человек
    читает, правило не переписывает.
    """
    nights = _grid_nights(units)
    if not nights:
        return None
    states = _states_by_night(units)
    known = [n for n in nights if states[n] - {"unknown"}]
    if not known:
        return None
    last_known = known[-1]
    if "sales_not_open" in states[last_known] \
            and "busy" not in states[last_known]:
        return None                      # движок сам показал границу окна
    free_nights = [n for n in nights if "free" in states[n]]
    if not free_nights:
        return None                      # это случай предохранителя ниже
    last_free = free_nights[-1]
    tail = [n for n in nights if last_free < n <= last_known]
    if not tail:
        return None
    busy_tail = [n for n in tail if "busy" in states[n]]
    if len(busy_tail) < len(tail) * SALES_WALL_MIN_BUSY_SHARE:
        return None                      # дырявый хвост — это брони
    since = busy_tail[0]
    if not_before and since < not_before:
        since = not_before
        busy_tail = [n for n in busy_tail if n >= since]
    if len(busy_tail) < min_nights:
        return None
    if len([n for n in free_nights if n < since]) < min_live_nights:
        return None                      # продаж до стены и не было
    cells_total = cells_known = 0
    for cells in units.values():
        for night, cell in cells.items():
            if night < since or night > last_known:
                continue
            cells_total += 1
            cells_known += cell.get("state") != "unknown"
    if not cells_total or cells_known < cells_total * SALES_WALL_MIN_KNOWN_SHARE:
        return None                      # снимок неполный — не догадываемся
    return since


def _relabel(units: dict, since: Optional[str], state: str,
             rule: str) -> tuple[int, int]:
    """Переписать busy-клетки (с ночи since, если задана) в state.

    -> (сколько клеток, сколько ночей). Клетка несёт, ЧЕМ она переписана:
    сводке нужно отличать ночь, которую движок и правда не открывал, от
    ночи, размеченной нашим правилом, а ещё знать, на скольких ночах после
    разметки держится процент месяца (ревью wave2-probes, блокер 2).
    """
    changed = 0
    touched: set[str] = set()
    for cells in units.values():
        for night, cell in cells.items():
            if cell.get("state") != "busy" or (since and night < since):
                continue
            # Цена клетки относилась к прочтению «занято»; после переразметки
            # она была бы обещанием, которого движок не давал. А вот ФОНД —
            # физическое свойство юнита («в этом типе три одинаковых
            # домика»), и правило перечитывает ночь, а не пересчитывает
            # домики: без него ветка no_free_cells, переписывающая ВСЕ
            # клетки объекта, отвечала на вопрос «сколько домиков» числом
            # типов (ревью волны 3). Пара пишется целиком — этого требует
            # схема (occupancy_core._validate_capacity), — но в знаменатель
            # занятости не идёт: unknown и sales_not_open отбрасываются по
            # состоянию раньше, чем кто-либо смотрит на units_free.
            fresh = {"state": state, "relabeled_from": "busy",
                     "relabeled_by": rule}
            total = cell.get("units_total")
            if isinstance(total, int) and not isinstance(total, bool) \
                    and total > 0:
                fresh["units_total"] = total
                fresh["units_free"] = 0
            cells[night] = fresh
            changed += 1
            touched.add(night)
    return changed, len(touched)


def apply_sales_window_rules(obj: dict, *,
                             not_before: Optional[str] = None) -> dict:
    """Тикет 07: не читать закрытые продажи и минимальный срок как аншлаг.

    Правило одно на все движки и живёт в диспетчере: оно смотрит на готовую
    сетку, а не на протокол, и заводить его копию в каждом пробнике значит
    повторить историю с тремя копиями признака «тема готова» (из-за неё 10
    утверждённых рецептов из 14 молча не давали сценариев).

    Два исхода:
    * ХВОСТ. Плотная стена busy в ДАЛЬНЕЙ части горизонта (условия — в
      sales_wall_since) — это неоткрытое окно продаж: busy становится
      sales_not_open (из знаменателя занятости он исключается, в отличие от
      busy) и объект говорит, с какой ночи. Месяцы сводки правило не
      трогает: их проценты человек читает и сравнивает с вчерашними.
    * ВЕСЬ ГОРИЗОНТ. Ни одной свободной клетки нигде — отличить «минимальный
      срок проживания» от «продажи закрыты целиком» изнутри ответа нечем, а
      100% занятости за месяцы вперёд заведомо ложь. Клетки уходят в
      unknown (то есть из знаменателя), объект — в partial с причиной:
      честное «не знаем» вместо выдуманной полной загрузки.

    Сработавшее правило оставляет в объекте поле sales_window: чем размечено
    (rule), с какой ночи (since), во что (to_state) и сколько клеток и ночей
    переписано. Без него сводка не может отличить «ночь не проверяли» от
    «ночь размечена правилом» и печатает процент по одной ночи из 31 без
    оговорки (ревью wave2-probes, блокер 2).

    not_before — ночь, раньше которой размечать нельзя. По умолчанию
    считается от НАЧАЛА сетки (sales_wall_floor): сетка начинается сегодня,
    и сверяться с календарём машины отдельно значит развести снимок и
    правило, которое его толкует.
    """
    units = obj.get("units") or {}
    if not units:
        return obj
    nights = _grid_nights(units)
    states = _states_by_night(units)
    busy_nights = [n for n in nights if "busy" in states[n]]
    if not busy_nights:
        return obj
    has_free = any("free" in states[n] for n in nights)
    if not has_free:
        if len(busy_nights) < SALES_WALL_MIN_NIGHTS:
            # Короткое окно: полная занятость на нём правдоподобна.
            return obj
        cells, touched = _relabel(units, None, "unknown", "no_free_cells")
        if obj.get("status") == "ok":
            obj["status"] = "partial"
        note = (f"ни одной свободной клетки на {len(busy_nights)} ноч. "
                f"горизонта: похоже на минимальный срок проживания или "
                f"закрытое окно продаж — цифра не показывается")
        obj["reason"] = "; ".join(x for x in (obj.get("reason"), note) if x)
        obj["sales_window"] = {"rule": "no_free_cells", "since": nights[0],
                               "to_state": "unknown",
                               "relabeled_cells": cells,
                               "relabeled_nights": touched}
        return obj
    floor = (not_before if not_before is not None
             else sales_wall_floor(nights[0]))
    since = sales_wall_since(units, not_before=floor)
    free_total = sum(1 for n in nights if "free" in states[n])
    if not since and free_total < SALES_WALL_MIN_LIVE_NIGHTS:
        # Зазор между двумя правилами (ревью 14.09.2026): 1–13 свободных ночей за
        # горизонт и дальше плотная стена busy — тот же вырожденный случай, что
        # «ни одной свободной клетки». Условие 5 стены («14 ночей живых продаж»)
        # раньше отменяло разметку целиком, и сводка печатала «окт 97%» как факт
        # (les_glamping 10–14.09). Окно продаж закрытым не объявляем — не знаем,
        # минимальный ли это срок, — поэтому unknown, а не sales_not_open. Ближние
        # SALES_WALL_FLOOR_DAYS суток не трогаем: там аншлаг правдоподобен.
        gap_since = sales_wall_since(units, not_before=floor, min_live_nights=0)
        if gap_since:
            cells, touched = _relabel(units, gap_since, "unknown", "few_free_nights")
            if obj.get("status") == "ok":
                obj["status"] = "partial"
            note = (f"свободных ночей за горизонт {free_total}, дальше с {gap_since} "
                    f"сплошь занято: похоже на минимальный срок проживания или "
                    f"неоткрытые продажи — цифра с этой ночи не показывается")
            obj["reason"] = "; ".join(x for x in (obj.get("reason"), note) if x)
            obj["sales_window"] = {"rule": "few_free_nights", "since": gap_since,
                                   "to_state": "unknown",
                                   "relabeled_cells": cells,
                                   "relabeled_nights": touched}
            return obj
    if since:
        cells, touched = _relabel(units, since, "sales_not_open", "sales_wall")
        note = (f"окно продаж закрыто с {since}: хвост горизонта без единой "
                f"свободной клетки размечен как закрытые продажи, а не "
                f"занятость")
        obj["reason"] = "; ".join(x for x in (obj.get("reason"), note) if x)
        obj["sales_window"] = {"rule": "sales_wall", "since": since,
                               "to_state": "sales_not_open",
                               "relabeled_cells": cells,
                               "relabeled_nights": touched}
    return obj


def insufficient(username: str, recipe: dict, reason: str) -> dict:
    """Честный объект insufficient_data без сетки (сбой до/вместо съёма)."""
    obj = _base_object(username, recipe, "aggregate")
    obj["source_kind"] = getattr(ENGINES.get(recipe.get("engine")),
                                 "SOURCE_KIND", "module")
    obj["status"] = "insufficient_data"
    obj["reason"] = reason
    obj["source_urls"] = list(recipe.get("source_urls", []))
    return obj


def run_recipe(username: str, recipe: dict, date_from: date, date_to: date,
               fetch=None, inventory: bool = True,
               inventory_date_to: Optional[date] = None):
    """Снять объект по рецепту. Возвращает (obj, broken_reason | None).

    inventory — снимать ли фонд типов (сколько номеров категории свободно).
    Движки, где юнит и так один физический номер (bronirui, litepms), фонд
    знают без запросов и флаг игнорируют; TravelLine и Bnovo платят за него
    одним запросом на ночь горизонта.

    inventory_date_to — последняя ночь, на которую спрашивается ФОНД
    (тикет 06). None = прежнее поведение, фонд до date_to. Разведён с
    горизонтом СЕТКИ потому, что шаг фонда — один HTTP-запрос на ночь и в
    нём почти вся цена прогона: после перехода на скользящий горизонт
    (тикет 01) сетка выросла с 58 до 366 ночей, и без этого рычага плановый
    прогон 06:30 не уложился бы в окно таймера. Сетка при этом остаётся
    полной — глубина доступности у большинства движков бесплатна.
    """
    engine = recipe.get("engine", "")
    module = ENGINES.get(engine)
    if module is None:
        return insufficient(
            username, recipe,
            f"движок {engine!r} пока не поддержан пробником"), None
    if recipe.get("status") == "broken":
        return insufficient(username, recipe, broken_recipe_reason(recipe)), None
    obj, broken_reason = module.probe(
        username, recipe, date_from, date_to, fetch=fetch,
        inventory=inventory, inventory_date_to=inventory_date_to)
    if broken_reason is None:
        # У сломанного рецепта сетки всё равно нет — толковать нечего.
        apply_sales_window_rules(obj)
    return obj, broken_reason


def broken_recipe_reason(recipe: dict) -> str:
    """Почему у broken-рецепта сегодня нет цифр — строкой для сводки и чата.

    Раньше здесь стояло одно дежурное «рецепт помечен broken — нужна
    переразведка», и оно печаталось КАЖДЫЙ ДЕНЬ у объектов, про которые всё
    давно известно: онлайн-продажи выключил сам отель (shale_aframe,
    smr_domik_u_ozera, air.glamping95 — три из десяти на 09.09.2026).
    Человек читал это как невыполненную работу, хотя делать ему там нечего:
    прогон подхватит цифры сам, как только продажи включат. Причина
    (broken_reason) и дата её проверки (discovered_at) отвечают на этот
    вопрос ровно, поэтому печатаются они, а дежурная фраза остаётся только
    там, где причины в рецепте нет вовсе.
    """
    reason = _common.one_line(recipe.get("broken_reason") or "")
    raw = str(recipe.get("discovered_at") or "")
    try:
        when = f" (проверено {_datetime.fromisoformat(raw):%d.%m.%Y})"
    except ValueError:
        when = ""
    if reason:
        return f"рецепт не снимается{when}: {reason}"
    return f"рецепт помечен broken{when} — ждёт разведки"


def mark_recipe_broken(recipes: dict, username: str, reason: str) -> None:
    """Пометить рецепт broken с причиной (поле broken_reason, in-place)."""
    recipe = recipes.get(username)
    if recipe is None:
        return
    recipe["status"] = "broken"
    recipe["broken_reason"] = reason
