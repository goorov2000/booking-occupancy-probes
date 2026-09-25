# -*- coding: utf-8 -*-
"""Пробник litepms: рецепт -> сетка по юнитам из HTML-календаря виджета.

Разведка 14.08.2026 (тикет 04, чистый HTTP): у litepms нет JSON API наружу —
виджет отдаёт календарь ГОТОВЫМ HTML одним GET, без авторизации и cookies:
    https://litepms.ru/widget/calendar?id={property_id}&mode=embed[&d=…]
Цен в календаре нет ни в одном макете.

МАКЕТОВ ДВА, и это свойство АККАУНТА, а не движка (разведка 04.09.2026, три
независимых прохода: 7820, 7746, 12001). Определяется макет по разметке
СТРАНИЦЫ, а не по параметру рецепта: оформление аккаунта владелец меняет в
своей админке, и рецепт об этом не узнает.

(1) ВЕРТИКАЛЬНЫЙ (dachavsosnah 10547, smolarelaks 11820). Блок room-item на
    каждый юнит (room-title = имя) и month-table на 3 месяца от якоря
    d=01-MM-YYYY (без d — от текущего месяца). Клетка td.calendar-day: класс
    freeday — свободно, booking — занято, disabled — ночь не продаётся вовсе
    (start_booking/end_booking/holiday/today/lastday — модификаторы поверх).
    Дата клетки собирается из month-title («Август 2026») и day-number.

(2) СЕТОЧНЫЙ (mesto_sily 7820, pobeg_iz_goroda 7746, mesto_schastya 12001 —
    25 поимённых домиков Самары). Обёртка widget-wrapper page-calendar,
    горизонтальная таблица «номера x сутки»: строка юнита —
    <tr id="room<rid>" class="room">, имя — в отдельной колонке-таблице
    (td.firstcol span.room-name, rid в ссылке room_info). Клетка —
    td.freeday | td.booking с ГОТОВОЙ подписью title="Свободно. 1 октября
    2026, Чт". Якорь d=DD-MM-YYYY задаёт ПЕРВЫЙ день окна (у вертикального
    макета якорь месячный — не спутать), окно у одних аккаунтов 15 суток, у
    других 31: число НЕ зашито, следующий якорь считается от последней ночи,
    которую страница реально отдала. Якорь в прошлом виджет молча подтягивает
    к сегодня (проверено: d=01-09-2026 при сегодня 04.09 отдал окно с 4-го) —
    поэтому счёт «от отданной ночи», а не «от запрошенной», и есть
    единственный способ листать без дыр.

Грабли, каждая стоила прогона:
- ДАТА ТОЛЬКО ИЗ title. В data-id клетки лежит unix-полночь в поясе ОБЪЕКТА
  (у 7820 это вовсе Asia/Tashkent), и на границе суток дата уезжает.
  У вертикального макета по той же причине дата собирается из month-title.
- ПУСТОЕ ТЕЛО БЕЗ wid. У части аккаунтов (12001) календарь без второго
  параметра wid отвечает HTTP 200 и телом «модуль бронирования отключен» в
  54 байта. Разведка 04.09 приняла это за «у объекта нет юнитов». Пробник
  зовёт такой ответ broken и называет wid поимённо: это ошибка рецепта, а не
  антибот и не смена вёрстки.
- ПОЧАСОВЫЕ УСЛУГИ В ЗНАМЕНАТЕЛЕ. У mesto_sily среди 7 строк календаря
  «Баня» и «Чан» — услуги на час, а не ночлег. Признака в ответе НЕТ вовсе:
  строки бани и чана не отличаются от домиков ни классом, ни разметкой, ни
  атрибутом (сверено живьём 04.09). Поэтому решение принимает РЕЦЕПТ —
  params.stay_units, белый список имён-жилья, — а пробник говорит вслух в
  reason объекта, кого он выбросил и почему. Списка в рецепте нет — не
  выбрасывается никто: догадываться по слову «баня» в имени нельзя, домик
  «Баня-бочка» существует.

Глубина: якорь листает страницы сколь угодно далеко, но за пределами
открытого владельцем окна продаж ВСЕ дни всех юнитов рисуются booking —
«закрыто» и «занято» поклеточно неотличимы (живой пример: dachavsosnah
открыт авг-окт 2026, ноябрь и дальше — сплошной booking). Поэтому полный
КАЛЕНДАРНЫЙ месяц (не страница — у сеточного макета окно месяцу не равно),
в котором заняты все дни всех юнитов, пробник читает как sales_not_open:
ложные 100% занятости в сводке хуже недоучёта реально проданного месяца
(проценты — оценка сверху, а sales_not_open из знаменателя исключается).
Текущий месяц этим правилом не задевается: прошедшие дни виджет рисует
freeday.

Правила ошибок — единый каскад (транспорт probes/_common.py + правила
исхода probes/_outcome.py): 4xx или HTML без опорной разметки (ни room-item,
ни tr#room; якорный месяц вертикальной страницы) -> broken с причиной
(переразведка); сетевой сбой и 5xx на странице -> её месяцы unknown,
partial/insufficient_data, рецепт жив. Один запрос — одна попытка, пауза
>= 1.2 c между страницами (общий трекер хоста в _common), ничего не
бронируется. Глубина НЕ расширяется осознанно (ревью 14.08): у вертикального
макета каждые 3 месяца горизонта — лишняя страница, у сеточного каждые
15 суток, поэтому листание ограничено GRID_PAGE_CAP и останавливается, как
только окно перестало двигаться вперёд.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from typing import Callable, Optional

from ._common import (AccessRefused, SchemaChanged,
                      _base_object as _common_base, _iso_dates,
                      is_challenge, make_fetch as _make_fetch)
# Каскад и сборка объекта идут через правила ИСХОДА (probes/_outcome.py):
# 5xx рецепт не ломает никогда, а отказ хоста поверх уже снятой сетки не
# заводит счётчик суток. Транспорт про судьбу рецепта не знает.
from ._outcome import classify_response, finish as _finish

MONTHS_PER_PAGE = 3

# Потолок страниц ОДНОГО объекта. Год горизонта окнами по 15 суток — это 25
# страниц, по 31 — 12; потолок стоит выше обоих и ловит только вырождение
# (виджет отдаёт окно в сутки или дразнит бесконечным листанием). Прогон
# ограничен ещё и бюджетом объекта (probes/_common.request_budget), но
# бюджет — про время, а этот потолок — про вежливость к чужому хосту.
GRID_PAGE_CAP = 40

# Версия РАЗБОРА движка (поле probe_version снапшота). 4 — правка
# 09.09.2026: клетка disabled читается как sales_not_open, а не роняет
# рецепт сменой схемы. 3 — правка 04.09.2026: пробник читает ВТОРОЙ макет
# виджета (сеточный page-calendar), то есть смысл разбора расширился.
# 2 — страница-заслон и 403 как отказ хоста, а не смена вёрстки (тикет 03).
PROBE_VERSION = 4

_BROKEN_HINT = "похоже на смену виджета или антибот"

# Имена, под которыми разведчики называли якорь в url_template. Рецепты трёх
# сеточных объектов писали три разных прогона разведки, и каждый выбрал своё
# слово; пробник подставляет якорь в любое из них, а незнакомый плейсхолдер
# гасит пустой строкой (format_map по _Params) — иначе один KeyError валил
# бы съём целиком.
ANCHOR_KEYS = ("month_anchor", "day_anchor", "window_anchor", "anchor", "d")

# Тело, которым календарь отвечает без обязательного wid (HTTP 200, 54 байта).
_WIDGET_DISABLED = "модуль бронирования отключен"

RU_MONTHS = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6,
    "июль": 7, "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11,
    "декабрь": 12,
}

# Родительный падеж — так месяц стоит в подписи клетки сеточного макета
# («Свободно. 1 октября 2026, Чт»).
RU_MONTHS_IN_TITLE = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11,
    "декабря": 12,
}

_ROOM_SPLIT = re.compile(r'class="room-item"')
_ROOM_TITLE = re.compile(r'class="room-title">([^<]*)<')
_MONTH_SPLIT = re.compile(r'class="month-title">')
_MONTH_LABEL = re.compile(r'^\s*([А-Яа-яЁё]+)\s+(\d{4})')
_DAY_CELL = re.compile(
    r'class="(calendar-day[^"]*)"[^>]*>\s*<div class="day-number">(\d+)')

# --- сеточный макет ---
_GRID_WRAPPER = re.compile(r'class="widget-wrapper[^"]*\bpage-calendar\b')
_GRID_ROW = re.compile(r'<tr\s+id="room(\d+)"[^>]*>(.*?)</tr>', re.S)
_GRID_NAME_CELL = re.compile(r'<td[^>]*class="firstcol".*?</td>', re.S)
_GRID_NAME_SPAN = re.compile(r'<span\b([^>]*class="room-name"[^>]*)>(.*?)'
                             r'</span>', re.S)
_GRID_RID = re.compile(r'\brid=(\d+)')
_GRID_CELL = re.compile(r'<td\b([^>]*)>')
_ATTR = re.compile(r'(\w[\w-]*)\s*=\s*"([^"]*)"')
_TAGS = re.compile(r'<[^>]*>')
# Подпись клетки: «Свободно. 1 октября 2026, Чт».
_TITLE_DATE = re.compile(r'(\d{1,2})\s+([А-Яа-яЁё]+)\s+(\d{4})')


def _month_index(d: date) -> int:
    return d.year * 12 + (d.month - 1)


def _anchor_for(index: int) -> str:
    """Индекс месяца (год*12+месяц-1) -> якорь страницы 01-MM-YYYY."""
    return f"01-{index % 12 + 1:02d}-{index // 12}"


def page_anchors(date_from: date, date_to: date) -> list[str]:
    """Якоря страниц по 3 месяца, покрывающие date_from..date_to.

    Это ПОТОЛОК числа страниц, а не план: сколько месяцев аккаунт кладёт на
    страницу, знает только его ответ (см. next_vertical_anchor).
    """
    first = _month_index(date_from)
    last = _month_index(date_to)
    return [_anchor_for(i)
            for i in range(first, last + 1, MONTHS_PER_PAGE)]


def page_last_month(page_units: dict) -> Optional[int]:
    """Последний месяц, который страница реально отдала (индекс месяца)."""
    months = {d[:7] for cells in page_units.values() for d in cells}
    if not months:
        return None
    return max(int(m[:4]) * 12 + int(m[5:7]) - 1 for m in months)


def next_vertical_anchor(page_units: dict, date_to: date,
                         seen_month: Optional[int]) -> tuple:
    """Следующий якорь вертикального макета. -> (якорь | None, месяц | None).

    Шаг считается от МЕСЯЦА, который страница отдала, а не от константы
    MONTHS_PER_PAGE: 3 месяца на страницу — свойство аккаунта, а не движка.
    Живой villy_uley (id=5099&wid=967, 09.09.2026) кладёт ДВА месяца, и
    прежний шаг в три перепрыгивал ноябрь — 30 ночей каждого юнита молча
    уходили в unknown, а сводка печатала «нет данных» на открытый месяц.
    Окно, не сдвинувшееся вперёд, останавливает листание — иначе виджет,
    игнорирующий якорь, крутил бы страницы до потолка.
    """
    last = page_last_month(page_units)
    if last is None or (seen_month is not None and last <= seen_month):
        return None, last
    if last >= _month_index(date_to):
        return None, last
    return _anchor_for(last + 1), last


def parse_calendar_page(html: str, anchor: str) -> dict:
    """HTML страницы виджета -> {юнит: {"YYYY-MM-DD": "busy" | "free"}}.

    Проверяет опорную разметку и что якорный месяц реально пришёл
    (виджет, игнорирующий d, — смена схемы). Не та форма -> SchemaChanged.
    """
    chunks = _ROOM_SPLIT.split(html)[1:]
    if not chunks:
        raise SchemaChanged(
            "в HTML нет ни одного room-item — схема виджета litepms сменилась")
    units: dict[str, dict] = {}
    months_seen: set[str] = set()
    for chunk in chunks:
        title_match = _ROOM_TITLE.search(chunk)
        if not title_match:
            raise SchemaChanged("room-item без room-title — схема сменилась")
        title = title_match.group(1).strip()
        if title in units:  # дубли имён различаем номером, юнит уникален
            n = 2
            while f"{title} [{n}]" in units:
                n += 1
            title = f"{title} [{n}]"
        cells: dict[str, str] = {}
        sections = _MONTH_SPLIT.split(chunk)[1:]
        if not sections:
            raise SchemaChanged(
                f"юнит {title!r} без month-title — схема сменилась")
        for section in sections:
            label = _MONTH_LABEL.match(section)
            if not label or label.group(1).lower() not in RU_MONTHS:
                raise SchemaChanged(
                    "month-title не «<Месяц> <год>» — схема сменилась")
            year = int(label.group(2))
            month = RU_MONTHS[label.group(1).lower()]
            months_seen.add(f"{year:04d}-{month:02d}")
            for cls, day in _DAY_CELL.findall(section):
                state = _cell_state(cls, f"юнит {title!r}")
                cells[f"{year:04d}-{month:02d}-{int(day):02d}"] = state
        units[title] = cells
    day, month, year = anchor.split("-")
    anchor_month = f"{year}-{month}"
    if anchor_month not in months_seen:
        raise SchemaChanged(
            f"виджет проигнорировал якорь d={anchor}: месяца {anchor_month} "
            f"нет на странице — схема пейджинга сменилась")
    return units


def detect_layout(html: str) -> Optional[str]:
    """Какой макет виджета пришёл: "vertical" | "grid" | None (не опознан).

    Смотрим на РАЗМЕТКУ, а не на рецепт. Аккаунт вправе сменить оформление в
    админке litepms, и рецепт об этом не узнает: определяй мы макет по
    параметру рецепта, такая смена читалась бы как смена схемы движка и
    убивала бы живой рецепт до ручной переразведки.
    """
    text = html or ""
    # Признак сетки — СТРОКИ номеров tr#room<rid>, а не обёртка. Обёртку
    # class="widget-wrapper page-calendar" несёт и живая ВЕРТИКАЛЬНАЯ страница
    # (smr_smolarelaks, 05.09 00:15): по ней одной детектор уводил вертикальный
    # макет в разбор сетки, тот падал SchemaChanged, и приёмочный прогон пометил
    # живой рецепт broken. Обёртка остаётся лишь подсказкой для сообщения об
    # ошибке, когда строк не нашлось ни в одном макете.
    if _GRID_ROW.search(text):
        return "grid"
    if _ROOM_SPLIT.search(text):
        return "vertical"
    return None


def _attrs(raw: str) -> dict:
    """Атрибуты тега (кусок между именем тега и '>') -> словарь."""
    return {name.lower(): value for name, value in _ATTR.findall(raw)}


def _cell_state(cls: str, what: str) -> str:
    """Класс клетки -> "busy" | "free". Не то и не другое -> SchemaChanged.

    start_booking/end_booking/dayoff/today — модификаторы ПОВЕРХ основного
    класса, и смотреть надо на токены целиком: у клетки «freeday end_booking»
    подстрока booking есть, а занята она не бывает.
    """
    tokens = cls.split()
    # disabled — ночь ЗА КРАЕМ открытого владельцем окна продаж: у части
    # аккаунтов виджет рисует её отдельным классом, а не сплошным booking
    # (живой villy_uley 09.09.2026, якорь 01-12-2026: декабрь идёт
    # freeday/booking, январь 2027 — 30 суток disabled и одна busy-ночь
    # 1 января, хвост новогодней брони). Читать disabled как busy значит
    # выдать неоткрытый месяц за проданный; месячное правило closed_months
    # его и не поймает — из-за той самой busy-ночи месяц не «весь занят».
    # Проверяется ПЕРВЫМ: класс приходит с модификаторами («disabled
    # end_booking holiday»), и хвост чужой брони не должен перебивать
    # основной признак.
    if "disabled" in tokens:
        return "sales_not_open"
    if "booking" in tokens:
        return "busy"
    if "freeday" in tokens:
        return "free"
    raise SchemaChanged(
        f"{what}: клетка календаря без freeday/booking/disabled ({cls!r}) — "
        f"схема сменилась")


def _title_date(title: str, what: str) -> str:
    """Подпись клетки -> «YYYY-MM-DD». Дата берётся ТОЛЬКО отсюда.

    data-id клетки несёт unix-полночь в поясе объекта (у 7820 — вовсе
    Asia/Tashkent), и на границе суток она уезжает на день.
    """
    match = _TITLE_DATE.search(title or "")
    month = RU_MONTHS_IN_TITLE.get(match.group(2).lower()) if match else None
    if month is None:
        raise SchemaChanged(
            f"{what}: подпись клетки {title!r} не «<состояние>. D месяца "
            f"YYYY, Дн» — дату брать неоткуда, схема сменилась")
    return f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(1)):02d}"


def grid_unit_names(html: str) -> tuple[dict, list]:
    """Колонка имён сеточного макета -> ({rid: имя}, [имена по порядку]).

    Имена лежат в ОТДЕЛЬНОЙ таблице (calendar-table-fixed), выровненной со
    строками календаря; связь со строкой — rid из ссылки room_info. Порядок
    возвращается вторым значением как запасной ключ: обе таблицы виджет
    рисует строка в строку, и если ссылки room_info у аккаунта нет, имя
    берётся по позиции, а не теряется.
    """
    by_rid: dict[str, str] = {}
    ordered: list[str] = []
    for cell in _GRID_NAME_CELL.findall(html):
        span = _GRID_NAME_SPAN.search(cell)
        if span is None:
            continue
        name = (_attrs(span.group(1)).get("title")
                or _TAGS.sub("", span.group(2)))
        name = " ".join(name.split())
        if not name:
            continue
        ordered.append(name)
        rid = _GRID_RID.search(cell)
        if rid:
            by_rid.setdefault(rid.group(1), name)
    return by_rid, ordered


def parse_grid_page(html: str) -> dict:
    """Сеточная страница -> {юнит: {"YYYY-MM-DD": "busy" | "free"}}.

    Окно страницы НЕ проверяется на равенство 15 или 31 суткам: у разных
    аккаунтов оно разное, и зашитое число было бы догадкой. Проверяется
    только то, что строки есть, имя у строки нашлось и каждая клетка
    называет своё состояние и свою дату.
    """
    rows = _GRID_ROW.findall(html)
    if not rows:
        raise SchemaChanged(
            "в HTML сеточного макета нет ни одной строки tr#room<rid> — "
            "схема виджета litepms сменилась")
    by_rid, ordered = grid_unit_names(html)
    units: dict[str, dict] = {}
    for position, (rid, body) in enumerate(rows):
        title = by_rid.get(rid)
        if title is None and position < len(ordered):
            title = ordered[position]
        if not title:
            raise SchemaChanged(
                f"строка календаря room{rid} без имени в колонке "
                f"room-name — схема сменилась")
        if title in units:  # дубли имён различаем номером, юнит уникален
            n = 2
            while f"{title} [{n}]" in units:
                n += 1
            title = f"{title} [{n}]"
        cells: dict[str, str] = {}
        for raw in _GRID_CELL.findall(body):
            attrs = _attrs(raw)
            what = f"юнит {title!r}"
            state = _cell_state(attrs.get("class", ""), what)
            cells[_title_date(attrs.get("title", ""), what)] = state
        if not cells:
            raise SchemaChanged(
                f"строка календаря {title!r} без единой клетки — схема "
                f"сменилась")
        units[title] = cells
    return units


def closed_months(units: dict) -> set[str]:
    """Месяцы, где у ВСЕХ юнитов заняты ВСЕ дни, — закрытое окно продаж.

    Смотрит только полностью спарсенные месяцы (все дни месяца в сетке).
    """
    months = {d[:7] for cells in units.values() for d in cells}
    closed = set()
    for month in months:
        year, mon = (int(x) for x in month.split("-"))
        days_total = calendar.monthrange(year, mon)[1]
        for cells in units.values():
            month_cells = [state for d, state in cells.items()
                           if d[:7] == month]
            if len(month_cells) != days_total or any(
                    state != "busy" for state in month_cells):
                break
        else:
            closed.add(month)
    return closed


def _base_object(username: str, recipe: dict) -> dict:
    return _common_base(username, recipe, "per_unit",
                        default_engine="litepms")


class _Params(dict):
    """Значения для url_template: незнакомый плейсхолдер гасится пустотой."""

    def __missing__(self, key):
        return ""


def page_url(request: dict, anchor: str) -> str:
    """URL страницы по рецепту: все params плюс якорь под любым его именем.

    Прежняя редакция подставляла ровно два имени (property_id и
    month_anchor) — с ней рецепты сеточных объектов теряли обязательный wid
    и получали в ответ 200 с телом «модуль бронирования отключен».
    """
    params = request.get("params", {}) or {}
    values = _Params((key, value) for key, value in params.items()
                     if isinstance(value, (str, int, float)))
    for key in ANCHOR_KEYS:
        values[key] = anchor
    return request["url_template"].format_map(values)


def _unit_key(name: str) -> str:
    """Ключ сравнения имени юнита: без лишних пробелов и регистра."""
    return " ".join(str(name).split()).casefold()


def stay_units(params: dict) -> Optional[set]:
    """Белый список имён-ЖИЛЬЯ из рецепта (params.stay_units) или None.

    Зачем список, а не признак из ответа: у mesto_sily среди строк календаря
    «Баня» и «Чан» — почасовые услуги, а не ночлег, и в знаменателе загрузки
    им не место (7 строк вместо 5 — это минус треть процента). Признака в
    HTML нет: строки услуг не отличаются от домиков ничем (сверено живьём
    04.09.2026). Догадка по слову в имени тоже не годится — домик
    «Баня-бочка» существует. Значит решение принимает человек, а место
    решения — рецепт.

    None (поля нет) — фильтра нет вовсе: рабочие рецепты обоих макетов
    считаются ровно как считались.
    """
    raw = params.get("stay_units")
    if not raw:
        return None
    return {_unit_key(x) for x in raw if str(x).strip()}


def probe(username: str, recipe: dict, date_from: date, date_to: date,
          fetch: Optional[Callable] = None, inventory: bool = True,
          inventory_date_to: Optional[date] = None):
    """Снять сетку по рецепту litepms. -> (obj, broken_reason | None).

    Страницы листаются по МАКЕТУ ответа, а не по плану, составленному до
    первого запроса: вертикальная страница шагает якорями по 3 месяца
    (план известен заранее), сеточная — от последней ночи, которую сама и
    отдала. Первый якорь у обоих одинаков (01-MM-YYYY месяца date_from) —
    иначе макет пришлось бы угадывать до того, как виджет ответил.

    inventory и inventory_date_to принимаются для единообразия диспетчера и
    запросов не добавляют: строка календаря (room-item вертикального макета,
    tr#room сеточного) — это уже конкретный номер, фонд клетки всегда 1 и
    известен на всю сетку (тикет 06).
    """
    if fetch is None:
        fetch = _make_fetch("get_text")
    request = recipe.get("request", {})
    params = request.get("params", {}) or {}
    headers = request.get("headers", {})
    obj = _base_object(username, recipe)
    failures: list[str] = []
    refused: Optional[str] = None
    refusal_status: Optional[int] = None
    parsed: dict[str, dict] = {}  # юнит -> {дата: "busy"|"free"}
    month_plan = page_anchors(date_from, date_to)
    vertical_page_cap = (_month_index(date_to) - _month_index(date_from) + 1)
    anchor: Optional[str] = month_plan[0]
    layout: Optional[str] = None
    grid_edge: Optional[date] = None   # последняя ночь, отданная сеткой
    vertical_month: Optional[int] = None  # последний месяц вертикальной стр.
    pages = 0

    def next_month_anchor(current: str) -> Optional[str]:
        """Шаг ВСЛЕПУЮ после сбоя страницы: от неё мы не узнали ничего.

        Обычный шаг вертикального макета считается от месяцев, которые
        страница отдала (next_vertical_anchor); здесь страницы нет, поэтому
        остаётся прежнее допущение о трёх месяцах — и оно записано в
        failures как непрочитанный кусок горизонта.
        """
        day, month, year = current.split("-")
        index = int(year) * 12 + int(month) - 1 + MONTHS_PER_PAGE
        return (_anchor_for(index)
                if index <= _month_index(date_to) else None)

    while anchor is not None:
        if pages >= GRID_PAGE_CAP:
            failures.append(
                f"листание остановлено на {GRID_PAGE_CAP} страниц: окно "
                f"виджета короче, чем нужно горизонту — хвост не снят")
            break
        pages += 1
        url = page_url(request, anchor)
        try:
            status, html = fetch(url, headers)
        except AccessRefused as e:
            refused, refusal_status = e.reason, e.status
            break
        except OSError as e:
            failures.append(f"страница {anchor}: сетевой сбой ({e})")
            # Следующий якорь сеточного макета считается от ОТДАННОЙ ночи, а
            # её нет: листать дальше значит гадать. Вертикальный план
            # известен заранее, поэтому там сбой одной страницы прогон не
            # останавливает — как было до второго макета.
            if layout == "grid":
                break
            anchor = next_month_anchor(anchor)
            continue
        obj["source_urls"].append(url)
        # Тело здесь HTML, а не JSON, поэтому заслон проверяем сами: страница
        # Cloudflare приходит с кодом 200 и без разметки виджета, и до
        # каскада тикета 03 читалась как «схема сменилась».
        if is_challenge(html or ""):
            refused = (f"страница {anchor}: страница-заслон (антибот) — нас "
                       f"не пустили, рецепт не трогаем")
            refusal_status = status
            break
        verdict = classify_response(status, True, f"страница {anchor}",
                                    hint=_BROKEN_HINT)
        if verdict.kind == "refused":
            refused, refusal_status = verdict.reason, status
            break
        if verdict.kind == "broken":
            return _finish(obj, failures, verdict.reason)
        if verdict.kind == "network":
            failures.append(verdict.reason)
            if layout == "grid":
                break
            anchor = next_month_anchor(anchor)
            continue
        body = (html or "").strip()
        if not body or _WIDGET_DISABLED in body[:200].lower():
            # HTTP 200 с пустым телом — это не смена вёрстки и не антибот, а
            # недостающий параметр рецепта: часть аккаунтов отвечает так на
            # запрос без wid. Рецепт в переразведку, причина названа поимённо.
            return _finish(obj, failures, (
                f"страница {anchor}: HTTP {status} и тело без календаря "
                f"({body[:60]!r}) — у части аккаунтов litepms так отвечает "
                f"виджет без обязательного параметра wid; проверьте wid в "
                f"рецепте"))
        layout = detect_layout(body)
        try:
            if layout == "grid":
                page_units = parse_grid_page(body)
            elif layout == "vertical":
                page_units = parse_calendar_page(body, anchor)
            else:
                raise SchemaChanged(
                    f"страница {anchor}: в HTML нет ни одного room-item и ни "
                    f"одной строки tr#room — схема виджета litepms сменилась")
        except SchemaChanged as e:
            return _finish(obj, failures, str(e))
        for unit, cells in page_units.items():
            parsed.setdefault(unit, {}).update(cells)
        if layout == "vertical":
            if pages >= vertical_page_cap:
                # Потолок вежливости: больше страниц, чем месяцев в
                # горизонте, вертикальному макету не нужно ни при какой
                # раскладке — страница несёт хотя бы один месяц.
                anchor = None
                continue
            anchor, vertical_month = next_vertical_anchor(
                page_units, date_to, vertical_month)
            continue
        page_edge = max(
            (date.fromisoformat(d) for cells in page_units.values()
             for d in cells), default=None)
        if page_edge is None or (grid_edge is not None
                                 and page_edge <= grid_edge):
            failures.append(
                f"страница {anchor}: окно виджета не сдвинулось вперёд "
                f"(последняя ночь по-прежнему {grid_edge}) — хвост горизонта "
                f"не снят")
            break
        grid_edge = page_edge
        anchor = (None if page_edge >= date_to
                  else (page_edge + timedelta(days=1)).strftime("%d-%m-%Y"))

    dropped = _drop_non_stay_units(parsed, params)
    closed = closed_months(parsed)
    dates = _iso_dates(date_from, date_to)
    # Строка календаря — физический номер: фонд клетки известен без
    # отдельного запроса, то есть на всю глубину сетки (тикет 06).
    obj["inventory_until"] = date_to.isoformat()
    obj["grid_until"] = date_to.isoformat()
    for unit, cells in parsed.items():
        grid = {}
        for d in dates:
            if d[:7] in closed:
                grid[d] = {"state": "sales_not_open"}
            elif cells.get(d) == "sales_not_open":
                # Ночь, закрытая самим виджетом (класс disabled): фонда у неё
                # нет — иначе закрытый сезон попал бы в знаменатель загрузки
                # как проданный домик.
                grid[d] = {"state": "sales_not_open"}
            elif d in cells:
                # Строка виджета — ОДИН физический номер (дубли имён
                # различаются суффиксом выше), поэтому фонд клетки ровно 1:
                # объект считается по домикам, а не по типам.
                grid[d] = {"state": cells[d], "units_total": 1,
                           "units_free": 1 if cells[d] == "free" else 0}
            else:
                grid[d] = {"state": "unknown"}
        obj["units"][unit] = grid
    obj, broken = _finish(obj, failures, None, refused=refused,
                          refusal_status=refusal_status)
    if dropped and broken is None:
        # Отброшенное называется вслух и попадает в колонку «Источник»
        # сводки: молча уменьшать знаменатель нельзя, это та же выдумка,
        # только в другую сторону.
        note = (f"в знаменатель не взяты юниты вне списка жилья рецепта "
                f"(params.stay_units): {', '.join(dropped)}")
        obj["reason"] = "; ".join(x for x in (obj.get("reason"), note) if x)
    return obj, broken


def _drop_non_stay_units(parsed: dict, params: dict) -> list:
    """Убрать из сетки всё, что рецепт не назвал жильём. -> список убранного.

    Списка нет — не убирается ничего (рабочие рецепты обоих макетов). Список
    есть — за бортом остаются и почасовые услуги, и НОВЫЙ домик, которого в
    списке ещё нет; и то и другое пробник называет в reason, чтобы человек
    увидел расхождение рецепта с реальностью, а не читал молча уехавший
    процент.
    """
    allowed = stay_units(params)
    if allowed is None:
        return []
    dropped = [unit for unit in parsed if _unit_key(unit) not in allowed]
    for unit in dropped:
        parsed.pop(unit)
    return dropped
