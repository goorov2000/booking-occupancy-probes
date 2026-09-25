# -*- coding: utf-8 -*-
"""Тикет 07: закрытое окно продаж и минимальный срок не читаются как аншлаг.

Два разных механизма движков дают одну и ту же ложь «100% занято», и обе
пойманы на живых данных снапшота 04.09.2026:

1. ОКНО ПРОДАЖ. У bani_na_ozerah февраль-март 2027 — 336/336 и 372/372
   клеток busy. Это не аншлаг за полгода, это модуль, который дальние
   месяцы ещё не открыл: движок отвечает «заезд начать нельзя» одинаково
   и на проданную ночь, и на неоткрытую.
2. МИНИМАЛЬНЫЙ СРОК. Клетка отвечает «можно ли НАЧАТЬ заезд», и у объекта с
   минимумом в 2 ночи все клетки горизонта приходят busy при живых продажах
   (живой прецедент 15.08 — dacha_limerence). При 20 самарских bnovo это
   системный риск, а не редкость.

Обе проверки живут в ДИСПЕТЧЕРЕ (probes/__init__.py), а не в отдельных
пробниках: правило одно на все шесть движков, читает оно готовую сетку, и
третья копия признака «объект стоит стеной» — ровно та беда, которую разбор
10 из 14 рецептов уже показал на признаке «тема готова».

Правка волны 3 (ревью wave2-probes, блокер 1). Первая редакция правила
смотрела на хвост горизонта и переписывала клетки ЖИВЫХ месяцев: на живом
снапшоте 2026-09-04-0631 astroglamp терял октябрь 2026 (98,7% -> 50,0% по
восьми клеткам из 308), а дайджест показал бы это как обвал броней, потому
что diff_units сравнивает проценты и про смену кода не знает. Диф по всем 21
цели ДО и ПОСЛЕ — agent-runtime/research/glamping/occupancy/analysis/
2026-09-04-sales-wall-diff.md. Отсюда четыре условия волны 3, каждое со
своим тестом ниже: стена не трогает месяцы сводки, до неё должен быть
заметный участок живых продаж, хвост должен быть плотным, а неполный снимок
(часть юнитов не снялась) правилу не отдаётся.
"""
import json
from datetime import date, timedelta

import occupancy_core as core
import probes
import pytest

TODAY = date(2026, 9, 4)

HR_RECIPE = {
    "site": "https://baninaozerah.ru/",
    "engine": "homereserve",
    "status": "ok",
    "request": {
        "url_template": "https://realtycalendar.ru/v2/widget/{token}/calendar",
        "method": "POST",
        "params": {"token": "test-token"},
        "headers": {},
        "date_substitution": "begin_date/end_date = YYYY-MM-DD в теле POST",
    },
    "discovered_at": "2026-09-04T10:00:00+03:00",
    "notes": "",
    "source_urls": [],
}


@pytest.fixture(autouse=True)
def freeze_today(monkeypatch):
    """Глубина сетки считается от СЕГОДНЯ (тикет 01) — фиксируем день.

    Без этого горизонт уезжал бы вместе с календарём машины, а вместе с ним
    и граница «живых месяцев», от которой пляшет всё правило.
    """
    monkeypatch.setattr(core, "today", lambda: TODAY)


def hr_fetch(states, units=("Дом",)):
    """Фейковый homereserve: {дата: True(свободно)|False(занято)} -> ответ.

    units — сколько домиков отдаёт справочник; states можно задать словарём
    на домик ({имя: {дата: bool}}), чтобы собрать НЕПОЛНЫЙ снимок: домик,
    которого нет в states, отвечает сетевым сбоем, как живой chekhovapi
    04.09 (одна категория из 14 не снялась).
    """
    by_unit = states if isinstance(next(iter(states.values()), None), dict) \
        else {name: states for name in units}

    def fetch(url, headers, payload=None):
        if url.endswith("/apartments"):
            return 200, {"apartments": [{"id": i, "title": name}
                                        for i, name in enumerate(units, 1)]}
        index = int(payload["apartment_id"]) - 1
        name = units[index]
        if name not in by_unit:
            raise ConnectionError(f"обрыв на {name}")
        return 200, {"calendar": [{"date": d, "available": free,
                                   "price": 5000 if free else None}
                                  for d, free in sorted(by_unit[name].items())]}
    return fetch


def nights(start, count, free):
    return {(start + timedelta(days=i)).isoformat(): free
            for i in range(count)}


def run(states, date_from, date_to, units=("Дом",)):
    return probes.run_recipe("bani_na_ozerah", HR_RECIPE, date_from, date_to,
                             fetch=hr_fetch(states, units))


def covered(obj, states, unit="Дом"):
    """Клетки, про которые движок вообще что-то сказал.

    Сетка homereserve уходит глубже запрошенного окна (глубина у него
    бесплатна), и за краем фикстуры честно стоит unknown — сравнивать надо
    только покрытые ночи.
    """
    cells = obj["units"][unit]
    return {d: cells[d] for d in states if d in cells}


def pcts(units, months):
    """{месяц: процент занятости} — то, что сравнивает diff_units и читает
    человек в сводке."""
    return {m["month"]: m["cuts"]["all"]["pct"]
            for m in core.aggregate(units, months)}


# ---------------------------------------------------------------------------
# 1. Стена busy на хвосте горизонта — это закрытое окно продаж
# ---------------------------------------------------------------------------
# Стена засчитывается только В ДАЛЬНИХ месяцах: живые продажи идут на
# ближнем горизонте, и «100% занято» там — правдоподобный аншлаг, а не
# неоткрытый месяц. Граница — конец окна сводки (core.DEFAULT_SUMMARY_MONTHS
# месяцев), то есть ровно то, что человек читает глазами.

def test_far_wall_of_busy_is_sales_not_open():
    """Живой случай bani_na_ozerah: фев-мар 2027 стоят стеной busy."""
    start = TODAY
    states = {}
    states.update(nights(start, 150, True))                    # живые продажи
    states.update(nights(start + timedelta(days=150), 90, False))   # стена
    obj, broken = run(states, start, start + timedelta(days=239))
    assert broken is None
    cells = obj["units"]["Дом"]
    assert cells["2026-09-04"]["state"] == "free"
    assert cells["2027-02-01"]["state"] == "sales_not_open"
    assert cells["2027-04-30"]["state"] == "sales_not_open"
    assert "окно продаж закрыто с 2027-02-01" in obj["reason"]


def test_wall_close_to_today_is_left_alone():
    """Ближняя стена — возможно, настоящий аншлаг: правило её не трогает.

    Граница правила (SALES_WALL_FLOOR_DAYS = 21 сутки от начала сетки)
    защищает ровно этот случай: ближний уик-энд действительно бывает
    выкуплен целиком, и переписывать его догадкой нельзя.
    """
    start = TODAY
    states = {}
    states.update(nights(start, 14, True))                     # две недели живы
    states.update(nights(start + timedelta(days=14), 44, False))   # стена с 15-х суток
    obj, _ = run(states, start, start + timedelta(days=57))
    cells = obj["units"]["Дом"]
    # Ближние ночи остаются занятостью, дальние переразмечаются: граница
    # поднимает НАЧАЛО стены, а не отменяет её целиком.
    assert cells[(start + timedelta(days=15)).isoformat()]["state"] == "busy"
    assert cells[(start + timedelta(days=20)).isoformat()]["state"] == "busy"
    assert cells[(start + timedelta(days=25)).isoformat()]["state"] == "sales_not_open"


def test_wall_beyond_the_floor_is_relabelled_even_inside_summary_months():
    """Редакция 04.09: месяц сводки правило переписывает, если стена далеко.

    Самарская партия дала шесть объектов из 29 со «100% в октябре и ноябре»
    при сентябре в 6-38%. Глэмпинг не распродаёт октябрь целиком, не продав
    сентябрь: это неоткрытые продажи, и в ряде сезонности такая цифра
    переворачивает вывод — октябрь становится пиком сезона. Занятость и без
    того оценка сверху, а месяц без единой свободной клетки не несёт
    информации вовсе, поэтому честнее убрать его из знаменателя, чем
    показать выдуманные 100%.
    """
    start = TODAY
    states = {}
    states.update(nights(start, 27, True))                     # сентябрь жив
    states.update(nights(start + timedelta(days=27), 31, False))   # октябрь стеной
    obj, _ = run(states, start, start + timedelta(days=57))
    cells = obj["units"]["Дом"]
    assert cells["2026-10-02"]["state"] == "sales_not_open"
    assert "окно продаж закрыто" in obj["reason"]


def test_live_month_percentages_do_not_move_at_all():
    """Приёмка тикета 07 п.4: проценты месяцев СВОДКИ не двигаются.

    Считаем ровно то, что сравнивает occupancy_core.diff_units и печатает
    дайджест, — процент занятости по месяцам, а не отдельную клетку. Сетку
    ДО правила берём у самого пробника (диспетчер зовёт правило поверх неё).
    """
    from probes import homereserve

    start = TODAY
    states = {}
    states.update(nights(start, 150, True))
    for i in (3, 10, 40, 100):                                 # обычные брони
        states[(start + timedelta(days=i)).isoformat()] = False
    states.update(nights(start + timedelta(days=150), 90, False))
    months = core.summary_months(TODAY)
    before, _ = homereserve.probe("bani_na_ozerah", HR_RECIPE, start,
                                  start + timedelta(days=239),
                                  fetch=hr_fetch(states))
    after, _ = run(states, start, start + timedelta(days=239))
    assert "окно продаж закрыто" in after["reason"]     # правило сработало
    assert pcts(after["units"], months) == pcts(before["units"], months)


def test_short_busy_tail_stays_busy():
    """Хвост короче порога — это обычная распродажа ближних выходных."""
    start = TODAY
    states = {}
    states.update(nights(start, 150, True))
    states.update(nights(start + timedelta(days=150), 10, False))
    obj, _ = run(states, start, start + timedelta(days=159))
    cells = obj["units"]["Дом"]
    assert cells["2027-02-01"]["state"] == "busy"
    assert "окно продаж" not in obj["reason"]


def test_free_night_inside_the_tail_cancels_the_wall():
    """Свободная ночь обрывает хвост: стена считается только ПОСЛЕ неё.

    Продажи, открытые хоть на одну ночь, — доказательство, что окно ещё не
    закрылось: всё, что было до этой ночи, остаётся занятостью. Здесь после
    неё остаётся 19 ночей, то есть меньше порога, и стены нет вовсе.
    """
    start = TODAY
    states = {}
    states.update(nights(start, 150, True))
    states.update(nights(start + timedelta(days=150), 90, False))
    states[(start + timedelta(days=220)).isoformat()] = True
    obj, _ = run(states, start, start + timedelta(days=239))
    cells = obj["units"]["Дом"]
    assert cells["2027-02-01"]["state"] == "busy"
    assert "окно продаж" not in obj["reason"]


def test_wall_after_a_free_night_starts_where_sales_stopped():
    """Та же сетка, но свободная ночь раньше: стена начинается за ней."""
    start = TODAY
    states = {}
    states.update(nights(start, 150, True))
    states.update(nights(start + timedelta(days=150), 90, False))
    states[(start + timedelta(days=200)).isoformat()] = True
    obj, _ = run(states, start, start + timedelta(days=239))
    cells = obj["units"]["Дом"]
    assert cells["2027-02-01"]["state"] == "busy"      # до свободной ночи
    assert cells["2027-03-24"]["state"] == "sales_not_open"
    assert obj["sales_window"]["since"] == "2027-03-24"


def test_wall_needs_a_live_stretch_of_sales_before_it():
    """Объект, который продаёт три ночи и дальше стоит стеной, — не «окно
    продаж закрылось», а объект без продаж вовсе: догадываться не за что."""
    start = TODAY
    states = {}
    states.update(nights(start, 150, False))
    for i in (0, 1, 2):
        states[(start + timedelta(days=i)).isoformat()] = True
    states.update(nights(start + timedelta(days=150), 90, False))
    obj, _ = run(states, start, start + timedelta(days=239))
    # «Окно продаж закрылось» здесь не утверждаем (правило стены молчит), но и
    # 100% занятости не печатаем: см. test_few_free_nights_are_not_a_full_house.
    assert "окно продаж закрыто" not in obj["reason"]
    assert (obj.get("sales_window") or {}).get("rule") != "sales_wall"


def test_few_free_nights_are_not_a_full_house():
    """Ревью 14.09.2026. Между правилами «ни одной свободной клетки» и «стена»
    был зазор: от 1 до 13 свободных ночей за горизонт давали «сен 94%, окт 97%»
    со статусом ok и пустой причиной. Живой случай — les_glamping 10–14.09:
    свободны 14–20.09, дальше 359 ночей подряд busy до конца горизонта. Это тот же
    вырожденный случай, что ноль свободных клеток (минимальный срок или неоткрытые
    продажи), и цифра за ним — выдумка. Ближние SALES_WALL_FLOOR_DAYS суток правило
    не трогает: там настоящий аншлаг правдоподобен."""
    start = TODAY
    states = {}
    states.update(nights(start, 7, True))
    states.update(nights(start + timedelta(days=7), 233, False))
    obj, _ = run(states, start, start + timedelta(days=239))
    window = obj["sales_window"]
    floor = (start + timedelta(days=probes.SALES_WALL_FLOOR_DAYS)).isoformat()
    assert window["rule"] == "few_free_nights"
    assert window["to_state"] == "unknown" and window["since"] == floor
    assert obj["status"] == "partial"
    assert "минимальный срок" in obj["reason"]
    cells = obj["units"]["Дом"]
    near = (start + timedelta(days=10)).isoformat()
    far = (start + timedelta(days=60)).isoformat()
    assert cells[near]["state"] == "busy"            # ближние ночи не переписаны
    assert cells[far]["state"] == "unknown"


def test_scattered_busy_nights_are_not_a_wall():
    """Порог меряется СПЛОШНЫМ хвостом, а не суммой busy-ночей.

    30 занятых ночей, размазанных дырами по полугоду, — это обычные брони.
    Первая редакция правила считала их стеной: unknown хвост не разрывал.
    """
    start = TODAY
    states = {}
    states.update(nights(start, 150, True))
    tail = {}
    for i in range(150, 240):
        night = (start + timedelta(days=i)).isoformat()
        if i % 3 == 0:                      # каждая третья ночь занята
            tail[night] = False
    states.update(tail)
    obj, _ = run(states, start, start + timedelta(days=239))
    assert "окно продаж" not in obj["reason"]


def test_engine_that_marked_its_own_boundary_is_believed():
    """Ревью волны 2: аншлаг, примыкающий к границе окна продаж.

    Если движок САМ разметил конец горизонта sales_not_open (так делают
    TravelLine за availability_max_date и litepms), то выкупленные ночи
    перед границей — это продажи, а не догадка правила. Тридцать проданных
    новогодних ночей не должны уезжать из знаменателя занятости.
    """
    start = TODAY
    units = {"Дом": {}}
    grid = {}
    for i in range(0, 150):
        grid[(start + timedelta(days=i)).isoformat()] = {"state": "free"}
    for i in range(150, 190):                       # выкупленный хвост
        grid[(start + timedelta(days=i)).isoformat()] = {"state": "busy"}
    for i in range(190, 240):                       # движок: продаж ещё нет
        grid[(start + timedelta(days=i)).isoformat()] = {
            "state": "sales_not_open"}
    units["Дом"] = grid
    obj = {"username": "x", "status": "ok", "reason": "", "units": units}
    probes.apply_sales_window_rules(obj)
    assert obj["units"]["Дом"][(start + timedelta(days=150)).isoformat()][
        "state"] == "busy"
    assert "окно продаж" not in obj["reason"]


# ---------------------------------------------------------------------------
# 2. Неполный снимок правилу не отдаётся
# ---------------------------------------------------------------------------
# Клетки юнита, до которого пробник не дошёл, в объединении по юнитам не
# дают free, и хвост читается как закрытое окно продаж. Живое подтверждение:
# chekhovapi в снапшоте 2026-09-04-0631 — status=partial, одна категория из
# 14 упала на сетевом сбое.

def test_wall_is_not_read_from_a_half_taken_snapshot():
    """Половина юнитов не снялась — свободные ночи могли быть у них."""
    start = TODAY
    live = {}
    live.update(nights(start, 150, True))
    live.update(nights(start + timedelta(days=150), 90, False))
    obj, _ = run({"Дом 1": live}, start, start + timedelta(days=239),
                 units=("Дом 1", "Дом 2"))
    assert obj["status"] == "partial"
    assert "окно продаж" not in obj["reason"]


def test_one_lost_unit_out_of_many_does_not_block_the_wall():
    """Один упавший домик из четырнадцати — не повод молчать про стену.

    Ровно случай chekhovapi: без правила декабрь-март остались бы ровными
    100%, то есть заведомой ложью.
    """
    start = TODAY
    live = {}
    live.update(nights(start, 150, True))
    live.update(nights(start + timedelta(days=150), 90, False))
    units = tuple(f"Дом {i}" for i in range(1, 15))
    obj, _ = run({name: live for name in units[:-1]}, start,
                 start + timedelta(days=239), units=units)
    assert obj["status"] == "partial"
    assert "окно продаж закрыто с 2027-02-01" in obj["reason"]


# ---------------------------------------------------------------------------
# 3. Что остаётся в данных после разметки (блокер 2 ревью волны 2)
# ---------------------------------------------------------------------------
# Сводке нужно отличать «ночь не проверяли» от «ночь размечена правилом», а
# ещё знать, на скольких ночах держится процент месяца: после разметки в
# месяце может остаться одна проверенная ночь из 31.

def test_relabelled_cells_say_who_relabelled_them():
    start = TODAY
    states = {}
    states.update(nights(start, 150, True))
    states.update(nights(start + timedelta(days=150), 90, False))
    obj, _ = run(states, start, start + timedelta(days=239))
    cell = obj["units"]["Дом"]["2027-02-01"]
    assert cell["state"] == "sales_not_open"
    assert cell["relabeled_from"] == "busy"
    assert cell["relabeled_by"] == "sales_wall"
    # ночь, которую движок и правда не открывал, ничьей пометки не несёт
    assert "relabeled_by" not in obj["units"]["Дом"]["2026-09-04"]


def test_object_carries_the_size_of_the_relabelling():
    start = TODAY
    states = {}
    states.update(nights(start, 150, True))
    states.update(nights(start + timedelta(days=150), 90, False))
    obj, _ = run(states, start, start + timedelta(days=239))
    window = obj["sales_window"]
    assert window["rule"] == "sales_wall"
    assert window["since"] == "2027-02-01"
    assert window["to_state"] == "sales_not_open"
    assert window["relabeled_nights"] == 90       # 2027-02-01..2027-05-01
    assert window["relabeled_cells"] == 90        # один домик


# ---------------------------------------------------------------------------
# 4. Предохранитель минимального срока проживания
# ---------------------------------------------------------------------------

def test_object_without_a_single_free_cell_goes_partial():
    """Все клетки busy и ни одной free — цифру не показываем.

    Отличить «минимальный срок» от «окно продаж закрыто целиком» изнутри
    ответа нечем, а 100% занятости за год — заведомая ложь. Клетки уходят в
    unknown (то есть из знаменателя), объект — в partial с причиной.
    """
    start = TODAY
    obj, broken = run(nights(start, 60, False), start,
                      start + timedelta(days=59))
    assert broken is None
    assert obj["status"] == "partial"
    assert "минимальный срок" in obj["reason"]
    assert all(c["state"] == "unknown"
               for c in covered(obj, nights(start, 60, False)).values())
    assert obj["sales_window"]["rule"] == "no_free_cells"
    assert obj["sales_window"]["to_state"] == "unknown"


def test_short_horizon_without_free_cells_is_left_alone():
    """На коротком окне полная занятость правдоподобна — не трогаем."""
    start = TODAY
    states = nights(start, 5, False)
    obj, _ = run(states, start, start + timedelta(days=4))
    assert all(c["state"] == "busy" for c in covered(obj, states).values())
    assert obj["status"] == "ok"


def test_object_with_free_cells_is_not_touched():
    """Обычный объект: ни одна клетка не переписана."""
    start = TODAY
    states = nights(start, 60, True)
    states[(start + timedelta(days=5)).isoformat()] = False
    obj, _ = run(states, start, start + timedelta(days=59))
    cells = obj["units"]["Дом"]
    assert cells["2026-09-09"]["state"] == "busy"
    assert sum(1 for c in cells.values() if c["state"] == "free") == 59
    assert obj["status"] == "ok"
    assert obj["reason"] == ""
    assert "sales_window" not in obj


def test_sales_not_open_cells_are_not_counted_as_a_sellout():
    """Ночи, уже помеченные движком закрытыми, предохранитель не путает
    с занятостью: без busy-клеток объект не трогается вовсе."""
    start = TODAY
    recipe = json.loads(json.dumps(HR_RECIPE))
    obj, _ = probes.run_recipe("x", recipe, start, start + timedelta(days=59),
                               fetch=hr_fetch({}))
    assert obj["status"] == "insufficient_data"
    assert "минимальный срок" not in obj["reason"]


# ---------------------------------------------------------------------------
# 5. Разметка не стирает фонд клетки (ревью волны 3)
# ---------------------------------------------------------------------------
# Фонд юнита — его ФИЗИЧЕСКОЕ свойство («в этом типе три одинаковых домика»),
# а не прочтение ночи. Правило перечитывает ночь, а не пересчитывает домики,
# поэтому units_total остаётся в клетке. Иначе ветка no_free_cells, которая
# переписывает ВСЕ клетки объекта, отвечала на вопрос заказчика «сколько
# домиков» числом типов: у bronirui с rooms_count шесть домиков схлопывались
# в два типа.

def _object_with_fund(units_total, count, state="busy"):
    """Объект снапшота из одного типа с известным фондом на count ночей."""
    cells = {(TODAY + timedelta(days=i)).isoformat():
             {"state": state, "units_total": units_total, "units_free": 0}
             for i in range(count)}
    return {"username": "smr_test", "site": "https://example.ru",
            "engine": "bronirui", "source_kind": "module",
            "granularity": "per_unit", "status": "ok", "reason": "",
            "checked_at": "2026-09-04T06:31:00+03:00", "source_urls": [],
            "units": {"A-frame": cells}}


def test_relabelling_the_whole_horizon_keeps_the_fund():
    obj = probes.apply_sales_window_rules(_object_with_fund(3, 60))
    assert obj["sales_window"]["rule"] == "no_free_cells"
    cell = obj["units"]["A-frame"]["2026-09-04"]
    assert cell["state"] == "unknown"           # ночь мы больше не толкуем
    assert cell["units_total"] == 3             # а домиков по-прежнему три
    assert core.unit_capacity(obj["units"]["A-frame"]) == 3
    basis = core.unit_basis(obj["units"])
    assert basis["basis"] == "unit" and basis["homes"] == 3
    core.validate_object(obj)


def test_relabelling_the_tail_keeps_the_fund():
    obj = _object_with_fund(3, 240)
    for i in range(150):                        # живые продажи до стены
        night = (TODAY + timedelta(days=i)).isoformat()
        obj["units"]["A-frame"][night] = {"state": "free", "units_total": 3,
                                          "units_free": 3}
    obj = probes.apply_sales_window_rules(obj)
    assert obj["sales_window"]["rule"] == "sales_wall"
    cell = obj["units"]["A-frame"]["2027-02-01"]
    assert cell["state"] == "sales_not_open"
    assert cell["relabeled_by"] == "sales_wall"
    assert cell["units_total"] == 3
    core.validate_object(obj)


def test_relabelling_a_cell_without_fund_adds_none():
    """Где фонда не было, он и не появляется: догадки правило не заводит."""
    obj = _object_with_fund(3, 60)
    for cells in obj["units"].values():
        for cell in cells.values():
            cell.pop("units_total"), cell.pop("units_free")
    obj = probes.apply_sales_window_rules(obj)
    cell = obj["units"]["A-frame"]["2026-09-04"]
    assert cell == {"state": "unknown", "relabeled_from": "busy",
                    "relabeled_by": "no_free_cells"}
