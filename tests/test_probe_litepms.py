# -*- coding: utf-8 -*-
"""Пробник litepms: живые HTML-фикстуры ДВУХ макетов виджета и ошибки.

Разведка 14.08.2026 (тикет 04, чистый HTTP): виджет litepms отдаёт календарь
готовым HTML — GET litepms.ru/widget/calendar?id={property_id}&mode=embed
[&d=01-MM-YYYY]. Страница несёт блоки room-item (room-title = юнит) с
month-table по 3 месяца от якоря d; клетки td.calendar-day: класс freeday =
свободно, booking = занято (start_booking/end_booking/holiday/today —
модификаторы). За пределами открытого окна продаж ВСЕ дни всех юнитов
рисуются booking — «закрыто» и «занято» поклеточно неотличимы, поэтому
полный календарный месяц, где заняты все дни всех юнитов, пробник читает
как sales_not_open (иначе сводка получила бы ложные 100%).

Разведка 04.09.2026: у ЧАСТИ аккаунтов тот же URL отдаёт ВТОРОЙ макет —
горизонтальную сетку «номера x сутки» (widget-wrapper page-calendar,
table.calendar-table, строки tr#room<rid>). Дата клетки берётся из готовой
подписи title, окно страницы у разных аккаунтов 15 или 31 сутки, якорь d
задаёт ПЕРВЫЙ день окна. Оба макета обязаны разбираться одним рецептом, а
цифры вертикальных рецептов — не двигаться (тест ниже сверяет их поимённо).

Фикстуры — обезличенный живой HTML litepms.ru, вырезаны фото, sid-ссылки и
счётчики, клетки нетронуты.

Вертикальный макет (dachavsosnah, id=10547, снято 14.08.2026):
- widget_calendar_dachavsosnah.html — якорь авг 2026: «Дача ЛЕС» (авг 14
  занятых, сен 3, окт 0), «Дача ПОЛЕ с проектором» (авг 11, сен 0, окт 2);
- widget_calendar_closed_months.html — якорь ноя 2026: все дни ноя-янв у
  обоих юнитов booking (окно продаж кончилось — живой пример закрытых
  месяцев).

Сеточный макет (снято 04.09.2026, 6 GET суммарно, пауза >= 1.5 c):
- widget_grid_mesto_sily.html — id=7820&wid=759, якорь 01-10-2026, окно 15
  суток, 4 строки из 7: «Дом на компанию для празднования событий» (занят
  10 октября), «Дом 1», «Баня», «Чан». Баня и Чан — ПОЧАСОВЫЕ услуги, на
  них держится тест отсева знаменателя;
- widget_grid_mesto_sily_p2.html — тот же объект, якорь 16-10-2026: вторая
  страница окна (16-30 октября) для проверки листания;
- widget_grid_pobeg_31d.html — id=7746 без wid, окно 31 сутки через
  границу месяца (4 сентября - 4 октября 2026), 2 строки из 11;
- widget_grid_mesto_schastya.html — id=12001&wid=2172, окно 15 суток,
  2 строки из 7 (третий аккаунт, шаблон URL с другим именем параметра).
"""
import json
from datetime import date, timedelta
from pathlib import Path

import probes
from probes import litepms

FIXTURES = Path(__file__).parent / "fixtures" / "litepms"


def load(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


LITEPMS_RECIPE = {
    "site": "https://dachavsosnah.ru/",
    "engine": "litepms",
    "status": "ok",
    "request": {
        "url_template": ("https://litepms.ru/widget/calendar"
                         "?id={property_id}&mode=embed&d={month_anchor}"),
        "method": "GET",
        "params": {"property_id": "10547"},
        "headers": {"Referer": "https://dachavsosnah.ru/"},
        "date_substitution": "month_anchor = 01-MM-YYYY первого месяца "
                             "страницы из 3 месяцев; пробник шагает якорями "
                             "по 3 месяца до покрытия date_from..date_to",
    },
    "discovered_at": "2026-08-14T20:00:00+03:00",
    "notes": "",
    "source_urls": [],
}


def fake_fetch(pages, default=None):
    """Фейковый fetch: якорь d=... из url -> (status, html) | html | исключение.

    default — ответ на любой незаявленный якорь (нужен там, где число
    страниц определяет сам ответ виджета, а не тест). Без него незнакомый
    якорь — ошибка теста: сеть в тестах не трогается никогда.
    """
    def fetch(url, headers):
        anchor = url.split("&d=")[-1]
        fetch.urls.append(url)
        if anchor in pages:
            answer = pages[anchor]
        elif default is not None:
            answer = default(anchor) if callable(default) else default
        else:
            raise AssertionError(f"неожиданный якорь страницы: {anchor}")
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, tuple) else (200, answer)
    fetch.urls = []
    return fetch


# Рецепты сеточного макета — живые параметры разведки 04.09.2026. Имена
# плейсхолдеров у трёх объектов РАЗНЫЕ (window_anchor/day_anchor), и это не
# опечатка: рецепты писали три разных прогона разведки, а пробник обязан
# подставлять якорь в любой из них.
GRID_RECIPE = {
    "site": "https://mesto96.com/",
    "engine": "litepms",
    "status": "ok",
    "request": {
        "url_template": ("https://litepms.ru/widget/calendar"
                         "?id={property_id}&wid={wid}&mode=embed"
                         "&d={window_anchor}"),
        "method": "GET",
        "params": {"property_id": "7820", "wid": "759", "mode": "embed"},
        "headers": {"Referer": "https://mesto96.com/"},
        "date_substitution": "window_anchor = DD-MM-YYYY первого дня окна",
    },
    "discovered_at": "2026-09-04T00:00:00+03:00",
    "notes": "",
    "source_urls": [],
}


def grid_recipe(**params):
    """Копия сеточного рецепта с доп. параметрами (stay_units и прочее)."""
    recipe = json.loads(json.dumps(GRID_RECIPE))
    recipe["request"]["params"].update(params)
    return recipe


RU_MONTHS_GENITIVE = ["января", "февраля", "марта", "апреля", "мая", "июня",
                      "июля", "августа", "сентября", "октября", "ноября",
                      "декабря"]


def synthetic_grid_page(anchor: str, days: int, state: str = "free") -> str:
    """Сеточная страница на days суток от якоря — для правил, а не для чисел.

    Живые фикстуры отвечают на вопрос «читаем ли мы вёрстку»; эта страница —
    на вопрос «что делает пробник, когда виджет отвечает бесконечно» и «как
    считается закрытый месяц». Разметка ровно та, что у живой страницы.
    """
    first = date(*(int(x) for x in reversed(anchor.split("-"))))
    cls = "freeday" if state == "free" else "booking"
    word = "Свободно" if state == "free" else "Занято"
    cells = []
    for i in range(days):
        day = first + timedelta(days=i)
        title = (f"{word}. {day.day} {RU_MONTHS_GENITIVE[day.month - 1]} "
                 f"{day.year}, Пн")
        cells.append(f'<td title="{title}" class="{cls}" '
                     f'data-id="r101d{i}b0">&nbsp;</td>')
    return (
        '<div class="widget-wrapper page-calendar">\n'
        '<table class="table calendar-table calendar-table-fixed"><tbody>\n'
        '<tr class="room"><td class="firstcol"><div class="room-name-wrap">'
        '<span title="Домик" class="room-name"><a class="dashed" '
        'href="javascript:;" onclick="parentMessage({\'action\': \'iframe\', '
        '\'src\': \'room_info?id=1&rid=101\'});">Домик</a></span></div></td>'
        '</tr>\n</tbody></table>\n'
        '<table class="table calendar-table" id="calendar"><tbody>\n'
        f'<tr id="room101" class="room">{"".join(cells)}</tr>\n'
        '</tbody></table>\n</div>\n')


def one_day_grid_page(anchor: str) -> str:
    return synthetic_grid_page(anchor, 1)


def busy_grid_page(anchor: str, days: int) -> str:
    return synthetic_grid_page(anchor, days, state="busy")


# ---------------------------------------------------------------------------
# Живая фикстура dachavsosnah: сетка по юнитам через диспетчер
# ---------------------------------------------------------------------------

def test_run_recipe_dispatches_litepms_and_builds_per_unit_grid():
    fetch = fake_fetch({"01-08-2026": load("widget_calendar_dachavsosnah.html")})
    obj, broken = probes.run_recipe("dachavsosnah", LITEPMS_RECIPE,
                                    date(2026, 8, 14), date(2026, 10, 31),
                                    fetch=fetch)
    assert broken is None
    assert obj["status"] == "ok"
    assert obj["engine"] == "litepms"
    assert obj["granularity"] == "per_unit"
    assert set(obj["units"]) == {"Дача ЛЕС", "Дача ПОЛЕ с проектором"}
    les = obj["units"]["Дача ЛЕС"]
    assert les["2026-08-14"] == {"state": "busy", "units_total": 1, "units_free": 0}
    assert les["2026-08-23"] == {"state": "free", "units_total": 1, "units_free": 1}
    assert les["2026-09-07"] == {"state": "busy", "units_total": 1, "units_free": 0}
    assert les["2026-10-05"] == {"state": "free", "units_total": 1, "units_free": 1}
    pole = obj["units"]["Дача ПОЛЕ с проектором"]
    assert pole["2026-10-02"] == {"state": "busy", "units_total": 1, "units_free": 0}
    assert obj["source_urls"] and "01-08-2026" in obj["source_urls"][0]


def test_live_fixture_month_pattern_matches_wave2_calendar():
    """Счёт занятости по фикстуре сходится с litepms-календарём волны 2."""
    fetch = fake_fetch({"01-08-2026": load("widget_calendar_dachavsosnah.html")})
    obj, _ = probes.run_recipe("dachavsosnah", LITEPMS_RECIPE,
                               date(2026, 8, 14), date(2026, 10, 31),
                               fetch=fetch)
    les = obj["units"]["Дача ЛЕС"]
    les_aug_busy = [d for d, c in les.items()
                    if d.startswith("2026-08") and c["state"] == "busy"]
    les_sep_busy = [d for d, c in les.items()
                    if d.startswith("2026-09") and c["state"] == "busy"]
    assert len(les_aug_busy) == 14
    assert les_sep_busy == ["2026-09-07", "2026-09-08", "2026-09-09"]
    pole = obj["units"]["Дача ПОЛЕ с проектором"]
    assert [d for d, c in pole.items() if c["state"] == "busy"
            and d.startswith("2026-10")] == ["2026-10-02", "2026-10-03"]


def test_fully_booked_months_beyond_sales_window_are_sales_not_open():
    """Живой ноябрьский якорь: все дни всех юнитов booking -> sales_not_open."""
    fetch = fake_fetch({"01-11-2026": load("widget_calendar_closed_months.html")})
    obj, broken = probes.run_recipe("dachavsosnah", LITEPMS_RECIPE,
                                    date(2026, 11, 1), date(2026, 11, 30),
                                    fetch=fetch)
    assert broken is None
    assert obj["status"] == "ok"
    for unit_cells in obj["units"].values():
        assert all(c["state"] == "sales_not_open" for c in unit_cells.values())


def test_horizon_beyond_one_page_walks_anchors_by_three_months():
    """Горизонт длиннее 3 месяцев -> вторая страница с якорем +3 месяца."""
    fetch = fake_fetch({
        "01-08-2026": load("widget_calendar_dachavsosnah.html"),
        "01-11-2026": load("widget_calendar_closed_months.html"),
    })
    obj, broken = probes.run_recipe("dachavsosnah", LITEPMS_RECIPE,
                                    date(2026, 8, 14), date(2026, 11, 30),
                                    fetch=fetch)
    assert broken is None
    les = obj["units"]["Дача ЛЕС"]
    assert les["2026-08-23"] == {"state": "free", "units_total": 1, "units_free": 1}
    assert les["2026-10-31"] == {"state": "free", "units_total": 1, "units_free": 1}
    assert les["2026-11-15"] == {"state": "sales_not_open"}
    assert len(obj["source_urls"]) == 2


def test_failed_second_page_leaves_unknown_and_partial():
    fetch = fake_fetch({
        "01-08-2026": load("widget_calendar_dachavsosnah.html"),
        "01-11-2026": ConnectionError("таймаут"),
    })
    obj, broken = probes.run_recipe("dachavsosnah", LITEPMS_RECIPE,
                                    date(2026, 8, 14), date(2026, 11, 30),
                                    fetch=fetch)
    assert broken is None
    assert obj["status"] == "partial"
    assert "сетевой сбой" in obj["reason"]
    les = obj["units"]["Дача ЛЕС"]
    assert les["2026-08-23"] == {"state": "free", "units_total": 1, "units_free": 1}
    assert les["2026-11-15"] == {"state": "unknown"}


# ---------------------------------------------------------------------------
# Ошибки: сеть не ломает рецепт, схема/4xx ломают
# ---------------------------------------------------------------------------

def test_network_failure_on_single_page_is_insufficient_data():
    fetch = fake_fetch({"01-08-2026": ConnectionError("обрыв")})
    obj, broken = probes.run_recipe("dachavsosnah", LITEPMS_RECIPE,
                                    date(2026, 8, 14), date(2026, 8, 20),
                                    fetch=fetch)
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "сетевой сбой" in obj["reason"]


def test_http_404_marks_broken():
    fetch = fake_fetch({"01-08-2026": (404, "not found")})
    obj, broken = probes.run_recipe("dachavsosnah", LITEPMS_RECIPE,
                                    date(2026, 8, 14), date(2026, 8, 20),
                                    fetch=fetch)
    assert broken is not None and "404" in broken
    assert obj["status"] == "insufficient_data"


def test_403_is_refusal_not_broken():
    """Тикет 03: 403 — отказ хоста, а не смена виджета."""
    fetch = fake_fetch({"01-08-2026": (403, "forbidden")})
    obj, broken = probes.run_recipe("dachavsosnah", LITEPMS_RECIPE,
                                    date(2026, 8, 1), date(2026, 8, 31),
                                    fetch=fetch)
    assert broken is None
    assert obj["refusal"]["status"] == 403
    assert obj["status"] == "insufficient_data"


def test_challenge_page_is_refusal_not_schema_change():
    """Страница-заслон приходит с кодом 200 и без разметки виджета.

    До каскада тикета 03 это читалось как «схема сменилась» и ломало рецепт
    навсегда; заслон мы не обходим (запреты SKILL.md), а честно говорим,
    что нас не пустили.
    """
    page = ("<html><head><title>Just a moment...</title></head>"
            "<body>Checking your browser before accessing</body></html>")
    fetch = fake_fetch({"01-08-2026": page})
    obj, broken = probes.run_recipe("dachavsosnah", LITEPMS_RECIPE,
                                    date(2026, 8, 1), date(2026, 8, 31),
                                    fetch=fetch)
    assert broken is None
    assert "заслон" in obj["refusal"]["reason"]
    assert obj["status"] == "insufficient_data"


def test_html_without_rooms_marks_broken():
    fetch = fake_fetch({"01-08-2026": "<html><body>тут пусто</body></html>"})
    obj, broken = probes.run_recipe("dachavsosnah", LITEPMS_RECIPE,
                                    date(2026, 8, 14), date(2026, 8, 20),
                                    fetch=fetch)
    assert broken is not None and "room" in broken
    assert obj["status"] == "insufficient_data"


def test_page_without_requested_month_marks_broken():
    """Виджет проигнорировал якорь d (отдал не те месяцы) -> смена схемы."""
    fetch = fake_fetch({
        "01-08-2026": load("widget_calendar_closed_months.html")})
    obj, broken = probes.run_recipe("dachavsosnah", LITEPMS_RECIPE,
                                    date(2026, 8, 14), date(2026, 8, 20),
                                    fetch=fetch)
    assert broken is not None and "якор" in broken
    assert obj["status"] == "insufficient_data"


def test_litepms_registered_in_dispatcher():
    assert probes.ENGINES["litepms"] is litepms


# ---------------------------------------------------------------------------
# Ревью волны 3: авария чужого хостера и частичный съём рецепт не ломают
# ---------------------------------------------------------------------------

def test_503_page_keeps_the_recipe_alive():
    """503 балансировщика — повтор прогона, а не переразведка виджета."""
    fetch = fake_fetch({"01-08-2026": (503, "<html>503 Service Unavailable")})
    obj, broken = probes.run_recipe("dachavsosnah", LITEPMS_RECIPE,
                                    date(2026, 8, 14), date(2026, 8, 20),
                                    fetch=fetch)
    assert broken is None
    assert obj["status"] == "insufficient_data"
    assert "503" in obj["reason"]


def test_403_after_the_first_page_was_taken_is_not_a_refusal():
    """Первая страница отдала сетку — съём состоялся, счётчик суток не идёт."""
    fetch = fake_fetch({
        "01-08-2026": load("widget_calendar_dachavsosnah.html"),
        "01-11-2026": (403, "forbidden"),
    })
    obj, broken = probes.run_recipe("dachavsosnah", LITEPMS_RECIPE,
                                    date(2026, 8, 14), date(2026, 11, 30),
                                    fetch=fetch)
    assert broken is None
    assert "refusal" not in obj
    assert "снято не до конца" in obj["reason"]


# ---------------------------------------------------------------------------
# Второй макет виджета: горизонтальная сетка «номера x сутки»
# ---------------------------------------------------------------------------

def test_grid_layout_is_read_by_the_same_probe():
    """Живая страница mesto_sily: 4 юнита, окно 15 суток, дата из title."""
    fetch = fake_fetch({"01-10-2026": load("widget_grid_mesto_sily.html")})
    obj, broken = probes.run_recipe("smr_mesto_sily", GRID_RECIPE,
                                    date(2026, 10, 1), date(2026, 10, 15),
                                    fetch=fetch)
    assert broken is None
    assert obj["status"] == "ok"
    assert obj["granularity"] == "per_unit"
    assert set(obj["units"]) == {"Дом на компанию для празднования событий",
                                 "Дом 1", "Баня", "Чан"}
    dom = obj["units"]["Дом на компанию для празднования событий"]
    assert len(dom) == 15
    assert dom["2026-10-10"] == {"state": "busy", "units_total": 1,
                                 "units_free": 0}
    # 11 октября: класс freeday с модификатором end_booking — это свободно.
    assert dom["2026-10-11"] == {"state": "free", "units_total": 1,
                                 "units_free": 1}
    assert obj["units"]["Дом 1"]["2026-10-10"]["state"] == "free"
    assert len(fetch.urls) == 1


def test_grid_url_carries_every_recipe_parameter():
    """У части аккаунтов без wid календарь пуст — параметр обязан доехать."""
    fetch = fake_fetch({"01-10-2026": load("widget_grid_mesto_sily.html")})
    obj, _ = probes.run_recipe("smr_mesto_sily", GRID_RECIPE,
                               date(2026, 10, 1), date(2026, 10, 15),
                               fetch=fetch)
    assert obj["source_urls"] == [
        "https://litepms.ru/widget/calendar"
        "?id=7820&wid=759&mode=embed&d=01-10-2026"]


def test_hourly_services_are_kept_out_of_the_denominator():
    """«Баня» и «Чан» — почасовые услуги, а не жильё: список жилья в рецепте.

    Признака в самом ответе нет (проверено живьём 04.09: строки бани и чана
    не отличаются от домиков ни классом, ни разметкой), поэтому решение
    принимает рецепт, а пробник говорит вслух, кого он выбросил.
    """
    recipe = grid_recipe(stay_units=["Дом на компанию для празднования "
                                     "событий", "Дом 1"])
    fetch = fake_fetch({"01-10-2026": load("widget_grid_mesto_sily.html")})
    obj, broken = probes.run_recipe("smr_mesto_sily", recipe,
                                    date(2026, 10, 1), date(2026, 10, 15),
                                    fetch=fetch)
    assert broken is None
    assert obj["status"] == "ok"
    assert set(obj["units"]) == {"Дом на компанию для празднования событий",
                                 "Дом 1"}
    assert "Баня" in obj["reason"] and "Чан" in obj["reason"]
    assert "stay_units" in obj["reason"]


def test_unit_outside_the_recipe_list_is_named_in_the_reason():
    """Новый домик, которого нет в списке жилья, виден человеку."""
    recipe = grid_recipe(stay_units=["Дом 1"])
    fetch = fake_fetch({"01-10-2026": load("widget_grid_mesto_sily.html")})
    obj, _ = probes.run_recipe("smr_mesto_sily", recipe,
                               date(2026, 10, 1), date(2026, 10, 15),
                               fetch=fetch)
    assert set(obj["units"]) == {"Дом 1"}
    assert "Дом на компанию для празднования событий" in obj["reason"]


def test_grid_window_length_is_taken_from_the_page_not_hardcoded():
    """У pobeg окно 31 сутки и через границу месяца — число не зашито."""
    fetch = fake_fetch({"01-09-2026": load("widget_grid_pobeg_31d.html")})
    obj, broken = probes.run_recipe("smr_pobeg_iz_goroda", GRID_RECIPE,
                                    date(2026, 9, 4), date(2026, 10, 4),
                                    fetch=fetch)
    assert broken is None
    assert set(obj["units"]) == {"GÜDAS", "KEVÄT"}
    gudas = obj["units"]["GÜDAS"]
    assert len(gudas) == 31
    assert gudas["2026-09-04"]["state"] == "busy"
    assert gudas["2026-09-30"]["state"] in ("free", "busy")
    assert gudas["2026-10-01"]["state"] in ("free", "busy")
    assert gudas["2026-10-04"]["state"] == "free"
    assert len(fetch.urls) == 1


def test_grid_pages_are_walked_from_the_last_night_of_the_window():
    """Следующий якорь — день ПОСЛЕ последней ночи страницы, а не +15/+31."""
    fetch = fake_fetch({
        "01-10-2026": load("widget_grid_mesto_sily.html"),
        "16-10-2026": load("widget_grid_mesto_sily_p2.html"),
    })
    obj, broken = probes.run_recipe("smr_mesto_sily", GRID_RECIPE,
                                    date(2026, 10, 1), date(2026, 10, 30),
                                    fetch=fetch)
    assert broken is None
    assert obj["status"] == "ok"
    dom = obj["units"]["Дом 1"]
    assert len(dom) == 30
    assert dom["2026-10-15"]["state"] == "free"
    assert dom["2026-10-16"]["state"] == "free"
    assert dom["2026-10-30"]["state"] == "free"
    assert len(obj["source_urls"]) == 2


def test_grid_page_that_does_not_move_forward_stops_the_walk():
    """Виджет вернул то же окно — листание прекращается, хвост unknown.

    Рецепт при этом жив: часть сетки снята честно, а вечный цикл по
    неподвижному окну — это не смена схемы, а упёршийся горизонт.
    """
    page = load("widget_grid_mesto_sily.html")
    fetch = fake_fetch({"01-10-2026": page}, default=page)
    obj, broken = probes.run_recipe("smr_mesto_sily", GRID_RECIPE,
                                    date(2026, 10, 1), date(2026, 10, 31),
                                    fetch=fetch)
    assert broken is None
    assert obj["status"] == "partial"
    assert "окно" in obj["reason"]
    assert obj["units"]["Дом 1"]["2026-10-31"] == {"state": "unknown"}
    assert len(fetch.urls) == 2


def test_grid_walk_has_a_hard_page_cap():
    """Страница по сутки на год вперёд не превращается в бесконечный прогон."""
    fetch = fake_fetch({}, default=lambda anchor: one_day_grid_page(anchor))
    obj, broken = probes.run_recipe("smr_mesto_sily", GRID_RECIPE,
                                    date(2026, 10, 1), date(2027, 10, 1),
                                    fetch=fetch)
    assert broken is None
    assert len(fetch.urls) == litepms.GRID_PAGE_CAP
    assert obj["status"] == "partial"
    assert "страниц" in obj["reason"]


def test_third_grid_account_parses_with_its_own_placeholder():
    """mesto_schastya: другой шаблон URL ({widget_id}) и свои имена юнитов."""
    recipe = json.loads(json.dumps(GRID_RECIPE))
    recipe["request"]["url_template"] = (
        "https://litepms.ru/widget/calendar"
        "?id={property_id}&wid={widget_id}&mode=embed&d={day_anchor}")
    recipe["request"]["params"] = {"property_id": "12001",
                                   "widget_id": "2172"}
    fetch = fake_fetch({"01-10-2026": load("widget_grid_mesto_schastya.html")})
    obj, broken = probes.run_recipe("smr_mesto_schastya", recipe,
                                    date(2026, 10, 1), date(2026, 10, 15),
                                    fetch=fetch)
    assert broken is None
    assert set(obj["units"]) == {"Gokötta с чаном", "Ogonblick"}
    assert "wid=2172" in obj["source_urls"][0]
    assert obj["units"]["Gokötta с чаном"]["2026-10-03"]["state"] == "busy"


def test_disabled_widget_body_is_broken_with_a_hint_about_wid():
    """HTTP 200 и «модуль бронирования отключен» — это забытый wid.

    Не антибот и не смена вёрстки: параметра в рецепте не хватает.
    """
    fetch = fake_fetch({"01-10-2026": (200, "модуль бронирования отключен")})
    obj, broken = probes.run_recipe("smr_mesto_schastya", GRID_RECIPE,
                                    date(2026, 10, 1), date(2026, 10, 15),
                                    fetch=fetch)
    assert broken is not None and "wid" in broken
    assert obj["status"] == "insufficient_data"


def test_empty_body_is_broken_with_the_same_hint():
    fetch = fake_fetch({"01-10-2026": (200, "   ")})
    obj, broken = probes.run_recipe("smr_mesto_schastya", GRID_RECIPE,
                                    date(2026, 10, 1), date(2026, 10, 15),
                                    fetch=fetch)
    assert broken is not None and "wid" in broken


def test_grid_month_fully_booked_is_sales_not_open_like_the_old_layout():
    """Правило закрытых продаж считается по КАЛЕНДАРНОМУ месяцу, не по окну."""
    busy_pages = {}
    for first, last in (("01-11-2026", 15), ("16-11-2026", 15)):
        busy_pages[first] = busy_grid_page(first, last)
    fetch = fake_fetch(busy_pages)
    obj, broken = probes.run_recipe("smr_mesto_sily", GRID_RECIPE,
                                    date(2026, 11, 1), date(2026, 11, 30),
                                    fetch=fetch)
    assert broken is None
    cells = obj["units"]["Домик"]
    assert len(cells) == 30
    assert all(c["state"] == "sales_not_open" for c in cells.values())


# ---------------------------------------------------------------------------
# Старый макет не должен сдвинуться ни на клетку
# ---------------------------------------------------------------------------

def test_layout_is_decided_by_the_html_not_by_the_recipe():
    """Признак макета — разметка страницы, а не параметр рецепта.

    Аккаунт вправе сменить оформление в админке, и рецепт об этом не
    узнает.
    """
    assert litepms.detect_layout(
        load("widget_calendar_dachavsosnah.html")) == "vertical"
    assert litepms.detect_layout(
        load("widget_calendar_closed_months.html")) == "vertical"
    for name in ("widget_grid_mesto_sily.html", "widget_grid_pobeg_31d.html",
                 "widget_grid_mesto_schastya.html"):
        assert litepms.detect_layout(load(name)) == "grid"
    assert litepms.detect_layout("<html><body>пусто</body></html>") is None


def test_vertical_recipes_keep_their_numbers_cell_for_cell():
    """Рабочие рецепты (dachavsosnah, smolarelaks) читаются как раньше.

    Сверка поимённая, а не «статус ok»: вторая ветвь разбора не имеет права
    сдвинуть ни одну клетку живого вертикального макета.
    """
    fetch = fake_fetch({
        "01-08-2026": load("widget_calendar_dachavsosnah.html"),
        "01-11-2026": load("widget_calendar_closed_months.html")})
    obj, broken = probes.run_recipe("dachavsosnah", LITEPMS_RECIPE,
                                    date(2026, 8, 1), date(2026, 12, 31),
                                    fetch=fetch)
    assert broken is None
    census = {}
    for unit, cells in obj["units"].items():
        for night, cell in cells.items():
            census[(unit, night[:7], cell["state"])] = \
                census.get((unit, night[:7], cell["state"]), 0) + 1
    les, pole = "Дача ЛЕС", "Дача ПОЛЕ с проектором"
    assert census[(les, "2026-08", "busy")] == 14
    assert census[(les, "2026-08", "free")] == 17
    assert census[(les, "2026-09", "busy")] == 3
    assert (les, "2026-10", "busy") not in census
    assert census[(pole, "2026-08", "busy")] == 11
    assert census[(pole, "2026-10", "busy")] == 2
    # ноябрь-декабрь: окно продаж закрыто, ни одной busy-клетки
    for unit in (les, pole):
        for month in ("2026-11", "2026-12"):
            assert census[(unit, month, "sales_not_open")] == \
                (30 if month == "2026-11" else 31)


VILLY_ULEY_RECIPE = {
    "site": "https://villy-uley.ru",
    "engine": "litepms",
    "status": "ok",
    "request": {
        "url_template": ("https://litepms.ru/widget/calendar"
                         "?id={property_id}&wid={wid}&mode=embed"
                         "&d={month_anchor}"),
        "method": "GET",
        "params": {"property_id": "5099", "wid": "967"},
        "headers": {"Referer": "https://villy-uley.ru/"},
        "date_substitution": "month_anchor = 01-MM-YYYY",
    },
    "discovered_at": "2026-09-09T00:00:00+03:00",
    "notes": "",
    "source_urls": [],
}


def test_disabled_cell_is_a_closed_night_not_a_sold_one():
    """Класс disabled — ночь за краем окна продаж, а не занятая ночь.

    Живая страница villy_uley (id=5099&wid=967, якорь 01-12-2026, снято
    09.09.2026): декабрь идёт обычными freeday/booking, а январь 2027 — 30
    суток disabled плюс одна busy-ночь 1 января (хвост новогодней брони).
    Прежний разбор ронял на такой клетке ВЕСЬ рецепт («клетка без
    freeday/booking — схема сменилась»), то есть объект уходил в
    переразведку из-за неоткрытого месяца. Месячное правило closed_months
    его тоже не спасает: из-за busy-ночи 1 января месяц не «весь занят».
    """
    fetch = fake_fetch({"01-12-2026":
                        load("widget_calendar_disabled_season.html")})
    obj, broken = probes.run_recipe("villy_uley", VILLY_ULEY_RECIPE,
                                    date(2026, 12, 1), date(2027, 1, 31),
                                    fetch=fetch)
    assert broken is None
    assert obj["status"] == "ok"
    standard = obj["units"]["Модуль Вили Улей Волга «СТАНДАРТ»"]
    # декабрь считается как раньше — у клетки есть фонд
    assert standard["2026-12-01"] == {"state": "free", "units_total": 1,
                                      "units_free": 1}
    assert standard["2026-12-31"] == {"state": "busy", "units_total": 1,
                                      "units_free": 0}
    # январь: 1-е — хвост брони, остальные 30 ночей закрыты и без фонда
    assert standard["2027-01-01"]["state"] == "busy"
    assert standard["2027-01-15"] == {"state": "sales_not_open"}
    january = [standard[f"2027-01-{d:02d}"]["state"] for d in range(2, 32)]
    assert set(january) == {"sales_not_open"}


def test_disabled_month_does_not_reach_the_occupancy_denominator():
    """Закрытая ночь не считается ни занятой, ни свободной."""
    fetch = fake_fetch({"01-12-2026":
                        load("widget_calendar_disabled_season.html")})
    obj, _ = probes.run_recipe("villy_uley", VILLY_ULEY_RECIPE,
                               date(2026, 12, 1), date(2027, 1, 31),
                               fetch=fetch)
    import occupancy_core as core
    january = core.aggregate(obj["units"], ["2027-01"])[0]
    # в знаменателе января — только 1-е число у обоих юнитов, а не 62 ночи
    assert january["cuts"]["all"]["known"] == 2
    assert january["cuts"]["all"]["busy"] == 2


def test_probe_version_says_the_parsing_widened():
    """Смысл разбора расширился — версия пробника обязана это показать."""
    assert litepms.PROBE_VERSION == 4



def test_vertical_page_with_page_calendar_wrapper_is_still_vertical():
    """Обёртка page-calendar — не признак сетки (регресс приёмки 05.09).

    Живая вертикальная страница smr_smolarelaks несёт class="widget-wrapper
    page-calendar" так же, как сеточные; детектор по одной обёртке уводил её в
    parse_grid_page, тот падал SchemaChanged, и рецепт метился broken. Макет
    определяют строки tr#room<rid>, а их у вертикальной страницы нет.
    """
    from probes import litepms
    vertical = (FIXTURES / "widget_calendar_dachavsosnah.html").read_text(encoding="utf-8")
    assert litepms.detect_layout(vertical) == "vertical"
    # Фикстура обрезана до calendar-content, поэтому обёртку приставляем
    # снаружи — ровно так она и стоит на живой странице.
    wrapped = '<div class="widget-wrapper page-calendar">' + vertical + '</div>'
    assert 'page-calendar' in wrapped
    assert litepms.detect_layout(wrapped) == "vertical"
    grid = (FIXTURES / "widget_grid_mesto_sily.html").read_text(encoding="utf-8")
    assert litepms.detect_layout(grid) == "grid"
