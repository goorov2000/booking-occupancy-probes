# -*- coding: utf-8 -*-
"""Сборка сводки (тикет 06): история по объекту, модель строк, рендер md/html.

Тестируется чистая логика build_summary.py на fixture-снапшотах во временном
каталоге; публикация артефакта (harness-инструмент) не тестируется.
"""
from datetime import date, timedelta
from pathlib import Path

import build_summary as bs
import occupancy_core as core

MONTHS = ["2026-08", "2026-09", "2026-10"]

# «Сегодня» для фикстур: их снимки датированы 14–16 августа, а с пометкой
# возраста (тикет 08) строка зависит от текущей даты. Пин делает тесты
# воспроизводимыми: 16.08 — день последнего фикстурного прогона.
FIXTURE_TODAY = date(2026, 8, 16)


def _table(md: str) -> str:
    """Только строки markdown-таблицы: сноска «как читать» объясняет те же
    пометки словами, и проверять их наличие по всему тексту бессмысленно."""
    return "\n".join(line for line in md.splitlines()
                     if line.startswith("|"))


def _build_rows(targets, recipes, history, months, today_=FIXTURE_TODAY):
    """build_rows с пришпиленным «сегодня» — иначе тесты зависят от даты."""
    return bs.build_rows(targets, recipes, history, months, today_=today_)


def _units(states: dict) -> dict:
    return {"Дом": {d: {"state": s} for d, s in states.items()}}


def _obj(username, units, *, status="ok", reason="", source_kind="module",
         granularity="per_unit", engine="travelline", checked_at):
    return {
        "username": username, "site": f"https://{username}.example",
        "engine": engine, "source_kind": source_kind,
        "granularity": granularity, "units": units,
        "checked_at": checked_at, "status": status, "reason": reason,
        "source_urls": [f"https://{username}.example/booking"],
    }


TARGETS = [
    {"username": "alpha", "site": "https://alpha.example", "priority": 100},
    {"username": "bravo", "site": "https://bravo.example", "priority": 90},
    {"username": "charlie", "site": "", "priority": 80},
    {"username": "delta", "site": "", "priority": 70},
]
RECIPES = {
    "alpha": {"engine": "travelline", "status": "ok"},
    "bravo": {"engine": "bronirui", "status": "broken",
              "broken_reason": "модуль выключен отелем (401)"},
    "delta": {"engine": "aggregator-ostrovok", "status": "aggregator"},
}


def _write_history(root):
    """Два прогона: alpha в обоих (авг 50% -> 100%), bravo и delta во втором."""
    run1 = [
        _obj("alpha", _units({"2026-08-07": "busy", "2026-08-08": "free"}),
             checked_at="2026-08-14T10:00:00+03:00"),
    ]
    core.write_snapshot(root, "2026-08-14-1000",
                        {"started_at": "2026-08-14T10:00:00+03:00",
                         "targets": ["alpha"]}, run1)
    run2 = [
        _obj("alpha", _units({"2026-08-07": "busy", "2026-08-08": "busy"}),
             checked_at="2026-08-14T12:00:00+03:00"),
        _obj("bravo", {}, status="insufficient_data",
             reason="рецепт помечен broken — нужна переразведка",
             granularity="aggregate", engine="bronirui",
             checked_at="2026-08-14T12:00:00+03:00"),
        _obj("delta", _units({"2026-09-22": "busy", "2026-09-23": "free"}),
             source_kind="aggregator_quota", engine="aggregator-ostrovok",
             checked_at="2026-08-14T12:00:00+03:00"),
    ]
    core.write_snapshot(root, "2026-08-14-1200",
                        {"started_at": "2026-08-14T12:00:00+03:00",
                         "targets": ["alpha", "bravo", "delta"]}, run2)


def _rows(tmp_path):
    root = tmp_path / "snapshots"
    _write_history(root)
    history = bs.load_history(root)
    return _build_rows(TARGETS, RECIPES, history, MONTHS)


def test_every_supported_engine_has_a_human_label():
    """Движок без имени печатался в сводке своим ключом.

    Четыре пробника, подключённые 08.09 (bookonline24, frontdesk24,
    rc-bookings, sutochno), в карте имён не значились, и владелец читал бы в
    колонке «Источник» строку вида «rc-bookings». Тест держит карту и
    диспетчер в согласии.
    """
    import probes

    missing = [e for e in probes.ENGINES if e not in bs.ENGINE_LABELS]
    assert missing == [], f"движки без человеческого имени: {missing}"


def test_rows_follow_targets_order(tmp_path):
    rows = _rows(tmp_path)
    assert [r["username"] for r in rows] == ["alpha", "bravo", "charlie", "delta"]


def test_latest_snapshot_and_dynamics_against_previous(tmp_path):
    alpha = _rows(tmp_path)[0]
    assert alpha["status"] == "ok"
    assert alpha["run_id"] == "2026-08-14-1200"
    dyn = alpha["dynamics"]
    assert dyn["prev_run_id"] == "2026-08-14-1000"
    # авг: 1/2=50% -> 2/2=100%, +50 пп; 08-08 стала занятой
    assert dyn["months"]["2026-08"]["delta_pp"] == 50.0
    assert len(dyn["newly_busy"]) == 1
    assert dyn["hours"] == 2.0


def test_first_snapshot_has_no_dynamics(tmp_path):
    delta = _rows(tmp_path)[3]
    assert delta["dynamics"] is None
    assert delta["quota"] is True


def test_insufficient_row_keeps_reason(tmp_path):
    bravo = _rows(tmp_path)[1]
    assert bravo["status"] == "insufficient_data"
    assert "переразведка" in bravo["source_label"]


def test_target_without_recipe_is_honest_row(tmp_path):
    charlie = _rows(tmp_path)[2]
    assert charlie["status"] == "missing"
    assert "разведк" in charlie["source_label"]  # «нужна разведка»


def test_markdown_essentials(tmp_path):
    rows = _rows(tmp_path)
    md = bs.render_markdown(rows, MONTHS, _meta())
    assert "оценка сверху" in md          # шапка «как читать»
    assert "авг" in md and "сен" in md and "окт" in md
    assert "квота агрегатора" in md       # строка delta не смешивается с модульными
    assert "первый снимок" in md          # у delta нет прошлого прогона
    assert "+50.0 пп" in md               # динамика alpha
    assert "### alpha" in md              # досье по объекту
    assert "2026-08-14-1200" in md        # дата/имя снимка в конце
    assert "snapshots" in md              # ссылка на сырьё


def test_markdown_zero_change_reads_as_no_change(tmp_path):
    root = tmp_path / "snapshots"
    same = _units({"2026-08-07": "busy", "2026-08-08": "free"})
    for run_id, hour in (("2026-08-14-1000", "10"), ("2026-08-14-1300", "13")):
        core.write_snapshot(
            root, run_id, {"started_at": f"2026-08-14T{hour}:00:00+03:00",
                           "targets": ["alpha"]},
            [_obj("alpha", same, checked_at=f"2026-08-14T{hour}:00:00+03:00")])
    rows = _build_rows(TARGETS[:1], RECIPES, bs.load_history(root), MONTHS)
    md = bs.render_markdown(rows, MONTHS, _meta(run_id="2026-08-14-1300"))
    assert "без изменений" in md


def test_sales_not_open_shown_instead_of_percent(tmp_path):
    root = tmp_path / "snapshots"
    units = {"Дом": {"2026-09-01": {"state": "sales_not_open"},
                     "2026-09-02": {"state": "sales_not_open"}}}
    core.write_snapshot(
        root, "2026-08-14-1000",
        {"started_at": "2026-08-14T10:00:00+03:00", "targets": ["alpha"]},
        [_obj("alpha", units, checked_at="2026-08-14T10:00:00+03:00")])
    rows = _build_rows(TARGETS[:1], RECIPES, bs.load_history(root), MONTHS)
    md = bs.render_markdown(rows, MONTHS, _meta(run_id="2026-08-14-1000"))
    assert "продажи не открыты" in md


def test_html_essentials(tmp_path):
    rows = _rows(tmp_path)
    html = bs.render_html(rows, MONTHS, _meta())
    assert "<title>Загрузка глэмпингов</title>" in html
    assert "overflow-x" in html           # таблица скроллится в контейнере
    assert "квота агрегатора" in html
    assert "оценка сверху" in html
    assert "prefers-color-scheme" in html  # темы по правилам артефактов


def _meta(run_id="2026-08-14-1200"):
    return {"run_id": run_id, "generated_at": "2026-08-14T21:00:00+03:00",
            "snapshot_root": "agent-runtime/research/glamping/occupancy/snapshots"}


def _single_row(tmp_path, units, **obj_kwargs):
    """Один объект alpha в одном прогоне -> rows."""
    root = tmp_path / "snapshots"
    obj_kwargs.setdefault("checked_at", "2026-08-14T10:00:00+03:00")
    core.write_snapshot(
        root, "2026-08-14-1000",
        {"started_at": "2026-08-14T10:00:00+03:00", "targets": ["alpha"]},
        [_obj("alpha", units, **obj_kwargs)])
    return _build_rows(TARGETS[:1], RECIPES, bs.load_history(root), MONTHS)


# ---------------------------------------------------------------------------
# Агрегатные строки — оценка СНИЗУ (тикет 02, ревью 14.08)
# ---------------------------------------------------------------------------

def test_aggregate_row_with_data_is_marked_lower_bound(tmp_path):
    units = {core.AGGREGATE_UNIT: {"2026-09-22": {"state": "busy"},
                                   "2026-09-23": {"state": "free"}}}
    rows = _single_row(tmp_path, units, granularity="aggregate",
                       engine="bnovo")
    assert "агрегат: оценка снизу" in rows[0]["source_label"]
    md = bs.render_markdown(rows, MONTHS, _meta(run_id="2026-08-14-1000"))
    assert "агрегат: оценка снизу" in md
    # шапка объясняет обе рамки
    assert "Поюнитные проценты — оценка сверху" in md
    assert "оценка снизу" in bs.HOW_TO_READ
    # досье говорит то же человеческим текстом
    assert "оценка снизу" in bs.dossier(rows[0], MONTHS)
    html = bs.render_html(rows, MONTHS, _meta(run_id="2026-08-14-1000"))
    assert "агрегат: оценка снизу" in html


def test_aggregate_all_sales_not_open_is_not_lower_bound(tmp_path):
    """ok_reka: модуль подключён, продаж нет ни на одну дату — сравнивать
    нечего, пометка «оценка снизу» не ставится."""
    units = {core.AGGREGATE_UNIT: {
        "2026-09-22": {"state": "sales_not_open"},
        "2026-09-23": {"state": "sales_not_open"}}}
    rows = _single_row(tmp_path, units, granularity="aggregate",
                       engine="bnovo")
    assert "оценка снизу" not in rows[0]["source_label"]
    assert "оценка снизу" not in bs.dossier(rows[0], MONTHS)


# ---------------------------------------------------------------------------
# Размер выборки: «(по N ночам)» у неполных сеток (ревью 14.08)
# ---------------------------------------------------------------------------

def test_sparse_quota_month_carries_sample_note(tmp_path):
    """Находка ревью: «сен 0%» glamping_pod_nebom был считан по 3 ночам
    квоты агрегатора — без оговорки ноль читается как пустой месяц."""
    units = {"Купол": {"2026-09-10": {"state": "free"},
                       "2026-09-11": {"state": "free"},
                       "2026-09-12": {"state": "free"}}}
    rows = _single_row(tmp_path, units, source_kind="aggregator_quota",
                       engine="aggregator-ostrovok")
    md = bs.render_markdown(rows, MONTHS, _meta(run_id="2026-08-14-1000"))
    assert "по 3 ночам" in md
    assert "по 3 ночам" in bs.dossier(rows[0], MONTHS)
    html = bs.render_html(rows, MONTHS, _meta(run_id="2026-08-14-1000"))
    assert "по 3 ночам" in html


def test_fully_checked_month_has_no_sample_note(tmp_path):
    units = {"Дом": {f"2026-09-{d:02d}": {"state": "free"}
                     for d in range(1, 31)}}
    rows = _single_row(tmp_path, units)
    md = bs.render_markdown(rows, MONTHS, _meta(run_id="2026-08-14-1000"))
    assert "по 30 ночам" not in md


def test_partial_per_unit_grid_carries_sample_note(tmp_path):
    """Частичная сетка любого источника: проверено 2 даты сентября из 30."""
    units = {"Дом": {"2026-09-05": {"state": "busy"},
                     "2026-09-06": {"state": "free"},
                     "2026-09-07": {"state": "unknown"}}}
    rows = _single_row(tmp_path, units)
    md = bs.render_markdown(rows, MONTHS, _meta(run_id="2026-08-14-1000"))
    assert "по 2 ночам" in md


def test_nights_word_declension():
    assert bs._nights_word(1) == "ночи"
    assert bs._nights_word(11) == "ночам"
    assert bs._nights_word(21) == "ночи"
    assert bs._nights_word(3) == "ночам"


# ---------------------------------------------------------------------------
# Методика и проверки честности (фидбек оператора 14.08): оба раздела
# обязаны попадать и в md-сводку, и в html артефакта
# ---------------------------------------------------------------------------

def test_markdown_has_method_and_trust_sections(tmp_path):
    md = bs.render_markdown(_rows(tmp_path), MONTHS, _meta())
    assert "## Как это работает" in md
    assert "те же данные, что видит гость" in md
    assert "не бронирует" in md            # границы инструмента названы
    assert "## Почему цифрам можно доверять" in md
    assert "Проверки от 14.08.2026" in md  # подпись с датой проверок


def test_markdown_trust_checks_carry_exact_numbers(tmp_path):
    md = bs.render_markdown(_rows(tmp_path), MONTHS, _meta())
    assert "48% против 47%" in md          # сверка с осмотром 11.08
    assert "26,7% против ~27%" in md       # объект B дата-в-дату
    assert "копейка в копейку" in md       # цены Островка сошлись
    assert "46%" in md                     # объект I против 0% агрегата
    # все проверки пронумерованы и стоят в тексте
    for i in range(1, len(bs.TRUST_CHECKS) + 1):
        assert f"\n{i}. " in md


def test_html_has_method_and_trust_sections(tmp_path):
    html = bs.render_html(_rows(tmp_path), MONTHS, _meta())
    assert "<h2>Как это работает</h2>" in html
    assert "те же данные, что видит гость" in html
    assert "<h2>Почему цифрам можно доверять</h2>" in html
    assert "Проверки от 14.08.2026" in html
    assert "48% против 47%" in html
    assert html.count("<li>") == len(bs.TRUST_CHECKS)


# ---------------------------------------------------------------------------
# Тексты досье честны от фактических месяцев (ревью 14.08)
# ---------------------------------------------------------------------------

def test_dossier_autumn_wording_only_for_autumn_tail(tmp_path):
    units = {"Дом": {"2026-09-22": {"state": "busy"},
                     "2026-09-23": {"state": "free"}}}
    rows = _single_row(tmp_path, units)
    text = bs.dossier(rows[0], MONTHS)  # хвост сен+окт — это осень
    assert "Дальше по осени" in text


# ---------------------------------------------------------------------------
# Единственная графика html-артефакта (редакция 14.08, фидбек «передушил с
# графикой»): календарная лента и одна легенда. Плитки-агрегаты, месячные
# столбики и дельта-чипы сняты — тесты ниже держат их снятыми.
# md-сводка графики не несёт и правкой не тронута.
# ---------------------------------------------------------------------------

def test_html_has_no_tiles_bars_or_chips(tmp_path):
    """Один факт — один способ показа: три дубля процентов на объект сняты."""
    html = bs.render_html(_rows(tmp_path), MONTHS, _meta())
    for gone in ('class="tiles"', 'class="tile"', 'class="tvalue"',
                 'class="bars"', 'class="bargroup"', 'class="barrow"',
                 'class="track"', 'class="chip', "▲", "▼",
                 "Объектов с данными", "юнито-ночи"):
        assert gone not in html
    assert not hasattr(bs, "_tiles_html")
    assert not hasattr(bs, "_month_bars_html")
    assert not hasattr(bs, "_delta_chip")


def test_html_header_carries_coverage_and_upper_bound(tmp_path):
    """Покрытие — одной строкой шапки вместо плитки; рамка «оценка сверху»
    стоит до цифр, а не только в методике."""
    html = bs.render_html(_rows(tmp_path), MONTHS, _meta())
    lede = html.split('<p class="lede">', 1)[1].split("</p>", 1)[0]
    assert "оценка сверху" in lede
    assert "Снято 3 из 4 целей списка" in lede    # alpha+bravo+delta из 4
    assert "нет данных" in lede


def test_html_method_sections_come_before_data(tmp_path):
    """Порядок страницы (указание оператора 14.08, вечер): шапка -> методика ->
    доверие -> таблица -> ленты -> досье. Читатель сначала понимает механизм и
    основания доверять инструменту и только потом смотрит цифры."""
    html = bs.render_html(_rows(tmp_path), MONTHS, _meta())
    order = ['<p class="lede">', "<h2>Как это работает</h2>",
             "<h2>Почему цифрам можно доверять</h2>", 'ol class="checks"',
             "<h2>Загрузка по объектам</h2>", '<div class="table-wrap"',
             '<p class="note tablenote">', "<h2>Календарь занятости",
             "<h2>Досье по объектам</h2>", "<footer>"]
    found = [html.index(chunk) for chunk in order]
    assert found == sorted(found)
    # разбор клетки — сноска ПОД таблицей, а не абзац перед цифрами
    assert html.index("Клетка месяца:") > html.index("</table>")


def test_html_calendar_strips_and_tooltips(tmp_path):
    html = bs.render_html(_rows(tmp_path), MONTHS, _meta())
    # ленты только у объектов с данными: alpha и delta
    assert html.count('<svg class="strip"') == 2
    assert "viewBox=" in html
    assert 'class="strip-wrap"' in html          # лента скроллится в контейнере
    assert "07.08 — занято 1 из 1" in html       # тултип занятой клетки alpha
    assert "23.09 — свободно (занято 0 из 1)" in html  # свободная клетка delta
    assert 'class="c-free"' in html              # свободные ночи — контуром
    assert 'class="c-nd"' in html                # непроверенные дни отличимы
    assert 'class="wkband"' in html              # подложка выходных пт-вс
    assert "сентябрь" in html                    # подпись месяца в ленте


def test_html_sales_not_open_cells_use_hatch(tmp_path):
    units = {"Дом": {"2026-09-01": {"state": "sales_not_open"},
                     "2026-09-02": {"state": "sales_not_open"}}}
    html = bs.render_html(_single_row(tmp_path, units), MONTHS,
                          _meta(run_id="2026-08-14-1000"))
    assert 'id="hatch-sno"' in html              # штриховка 45°, не только цвет
    assert 'class="c-sno"' in html
    assert "01.09 — продажи не открыты" in html


def test_html_percent_cuts_live_only_in_the_table(tmp_path):
    """Будни/выходные показываются один раз — колонкой таблицы. Столбики
    под лентой их дублировали (фидбек 14.08) и сняты."""
    html = bs.render_html(_rows(tmp_path), MONTHS, _meta())
    table = html.split('<div class="table-wrap"', 1)[1].split("</table>", 1)[0]
    assert "будни" in table and "вых" in table
    after_table = html.split("</table>", 1)[1]
    assert "будни 100%" not in after_table
    assert 'style="width:' not in html           # треков-заливок больше нет


def test_html_dynamics_is_plain_text_in_table(tmp_path):
    html = bs.render_html(_rows(tmp_path), MONTHS, _meta())
    assert '<td class="dyn">авг +50.0 пп' in html  # текстом, без пилюли
    assert 'class="chip' not in html


def test_html_single_compact_legend(tmp_path):
    """Легенда одна на страницу и объясняет ровно то, что есть в ленте:
    состояния клетки и выходные. Строки про чипы и рамки-оговорки ушли
    вместе с самой графикой."""
    html = bs.render_html(_rows(tmp_path), MONTHS, _meta())
    assert html.count('class="legend"') == 1
    legend = html.split('<div class="legend">', 1)[1].split("</div>\n", 1)[0]
    for label in ("занято", "свободно", "продажи не открыты", "нет данных",
                  "выходные"):
        assert label in legend
    assert "пунктирная рамка" not in html
    assert "квота агрегатора" in html            # пометка честности жива


def test_html_quota_and_aggregate_marked_next_to_strip(tmp_path):
    """Оговорка источника осталась пометкой у ленты (пилюля + причина);
    декоративная пунктирная рамка карточки снята."""
    html = bs.render_html(_rows(tmp_path), MONTHS, _meta())
    assert "objcard" not in html
    calendar = html.split("<h2>Календарь занятости", 1)[1]
    assert '<span class="pill quota">квота агрегатора' in calendar
    units = {core.AGGREGATE_UNIT: {"2026-09-22": {"state": "busy"},
                                   "2026-09-23": {"state": "free"}}}
    agg = bs.render_html(
        _single_row(tmp_path / "agg", units, granularity="aggregate",
                    engine="bnovo"),
        MONTHS, _meta(run_id="2026-08-14-1000"))
    assert '<span class="srcnote">агрегат: оценка снизу</span>' in agg


def test_html_rows_without_data_have_no_strip(tmp_path):
    units = {"Дом": {"2026-09-22": {"state": "busy"}}}
    rows = _single_row(tmp_path, units)
    # bravo (insufficient) и charlie (missing) лент не получают
    html = bs.render_html(rows + _rows(tmp_path)[1:3], MONTHS,
                          _meta(run_id="2026-08-14-1000"))
    assert html.count('<section class="calobj">') == 1
    assert html.count('<svg class="strip"') == 1


# ---------------------------------------------------------------------------
# Досье: на странице — коротко (что не выводится из таблицы), в md — целиком
# ---------------------------------------------------------------------------

def test_html_dossier_is_short_and_does_not_repeat_percents(tmp_path):
    rows = _rows(tmp_path)
    for row in rows:
        text = bs.dossier_short(row, MONTHS)
        assert "%" not in text                   # проценты стоят в строке
        assert 1 <= text.count(". ") + 1 <= 3    # одно-два плюс оговорка
        assert len(text) < len(bs.dossier(row, MONTHS))
    html = bs.render_html(rows, MONTHS, _meta())
    assert "свободных ночей" in html             # где дыра
    assert "оффер" in html                       # что это значит


def test_markdown_dossier_stays_full(tmp_path):
    """Правка тронула только html-страницу: в md-сводке досье прежнее."""
    md = bs.render_markdown(_rows(tmp_path), MONTHS, _meta())
    assert "Дальше по осени" in md
    assert "Август занят на 100%" in md


# ---------------------------------------------------------------------------
# Визуальный аудит артефакта (14.08): найденные глазами дефекты вёрстки.
# Каждый тест держит одну починку — чтобы она не отъехала на следующей правке.
# ---------------------------------------------------------------------------

def test_html_long_reason_lives_outside_pill(tmp_path):
    """Причина не помещается В пилюлю: круглые торцы резали многострочный
    текст и раздували строку таблицы (карточка pod_nebom, ревью 14.08)."""
    rows = _rows(tmp_path)
    bravo = next(r for r in rows if r["username"] == "bravo")
    html = bs.render_html([bravo], MONTHS, _meta())
    assert ('<span class="pill none">нет данных: рецепт помечен broken</span>'
            in html)
    assert '<span class="srcnote">нужна переразведка</span>' in html
    # длинный «хвост» не остался внутри пилюли
    assert "broken — нужна переразведка</span>" not in html


def test_html_month_separator_is_drawn_over_cells(tmp_path):
    """Разделитель месяцев рисуется ПОСЛЕ клеток: иначе они его закрашивают
    и стык месяцев в ленте не виден."""
    html = bs.render_html(_rows(tmp_path), MONTHS, _meta())
    svg = html.split('<svg class="strip"', 1)[1].split("</svg>", 1)[0]
    assert 'class="msep"' in svg
    assert svg.rindex('class="c-') < svg.index('class="msep"')


def test_html_scroll_containers_are_keyboard_reachable(tmp_path):
    """Прокручиваемые области должны доставаться с клавиатуры (WCAG 2.1.1)."""
    html = bs.render_html(_rows(tmp_path), MONTHS, _meta())
    assert '<div class="strip-wrap" tabindex="0" role="group"' in html
    assert '<div class="table-wrap" tabindex="0" role="region"' in html
    assert ":focus-visible" in html               # фокус видно


def test_html_negative_delta_uses_typographic_minus(tmp_path):
    """Падение в колонке «Динамика» — типографским минусом, не дефисом
    (находка визуального аудита пережила снятие цветных чипов)."""
    root = tmp_path / "snapshots"
    core.write_snapshot(
        root, "2026-08-14-1000",
        {"started_at": "2026-08-14T10:00:00+03:00", "targets": ["alpha"]},
        [_obj("alpha", _units({"2026-08-07": "busy", "2026-08-08": "busy"}),
              checked_at="2026-08-14T10:00:00+03:00")])
    core.write_snapshot(
        root, "2026-08-14-1200",
        {"started_at": "2026-08-14T12:00:00+03:00", "targets": ["alpha"]},
        [_obj("alpha", _units({"2026-08-07": "busy", "2026-08-08": "free"}),
              checked_at="2026-08-14T12:00:00+03:00")])
    rows = _build_rows(TARGETS[:1], RECIPES, bs.load_history(root), MONTHS)
    html = bs.render_html(rows, MONTHS, _meta())
    assert "авг −50.0 пп" in html
    assert "-50.0 пп" not in html


def test_html_month_cell_breaks_only_between_parts(tmp_path):
    """«продажи не открыты» и «будни 0%» не рвутся по слову в узкой колонке."""
    html = bs.render_html(_rows(tmp_path), MONTHS, _meta())
    assert '<span class="np">будни ' in html
    units = {"Дом": {"2026-09-01": {"state": "sales_not_open"}}}
    sno = bs.render_html(_single_row(tmp_path / "sno", units), MONTHS,
                         _meta(run_id="2026-08-14-1000"))
    assert '<span class="cut np">продажи не открыты</span>' in sno


def test_html_keeps_meta_charset(tmp_path):
    """Без meta charset страница по file:// и с сервера без charset читается
    как windows-1252 — кириллица превращается в кракозябры (проверено)."""
    html = bs.render_html(_rows(tmp_path), MONTHS, _meta())
    assert html.startswith('<meta charset="utf-8">')


def test_html_print_rules_do_not_clip_graphics(tmp_path):
    """На бумаге прокрутить нельзя: лента ужимается по ширине страницы."""
    html = bs.render_html(_rows(tmp_path), MONTHS, _meta())
    assert "@media print" in html
    printed = html.split("@media print", 1)[1]
    assert "overflow: visible" in printed
    assert ".strip { width: 100%; height: auto; }" in printed


def test_html_footer_date_is_human_readable(tmp_path):
    html = bs.render_html(_rows(tmp_path), MONTHS, _meta())
    assert "14.08.2026, 21:00" in html
    assert "2026-08-14T21:00:00+03:00" not in html


def test_dossier_generic_wording_for_non_autumn_months(tmp_path):
    months = ["2026-11", "2026-12", "2027-01"]
    units = {"Дом": {"2026-11-06": {"state": "busy"},
                     "2026-12-04": {"state": "busy"},
                     "2026-12-05": {"state": "free"}}}
    root = tmp_path / "snapshots"
    core.write_snapshot(
        root, "2026-08-14-1000",
        {"started_at": "2026-08-14T10:00:00+03:00", "targets": ["alpha"]},
        [_obj("alpha", units, checked_at="2026-08-14T10:00:00+03:00")])
    rows = _build_rows(TARGETS[:1], RECIPES, bs.load_history(root), months)
    text = bs.dossier(rows[0], months)
    assert "осен" not in text.lower()  # словами про осень тут врать нельзя
    assert "по горизонту" in text


# ---------------------------------------------------------------------------
# Основа расчёта в сводке (правка 15.08): читатель должен видеть, считан
# объект по домикам или по типам — второе занижает загрузку
# ---------------------------------------------------------------------------

def _capacity_units(cells: dict) -> dict:
    """{дата: (state, всего домиков, свободно)} -> сетка одного типа."""
    return {"А-фрейм": {d: {"state": s, "units_total": t, "units_free": f}
                        for d, (s, t, f) in cells.items()}}


def _write_capacity_history(root, units, username="alpha"):
    core.write_snapshot(root, "2026-08-15-1000",
                        {"started_at": "2026-08-15T10:00:00+03:00",
                         "targets": [username]},
                        [_obj(username, units,
                              checked_at="2026-08-15T10:00:00+03:00")])


def test_source_label_says_counted_by_homes(tmp_path):
    root = tmp_path / "snapshots"
    _write_capacity_history(root, _capacity_units({
        "2026-08-07": ("free", 3, 1), "2026-08-08": ("busy", 3, 0)}))
    rows = _build_rows(TARGETS, RECIPES, bs.load_history(root), MONTHS)
    alpha = rows[0]
    assert "по домикам: 3 в 1 типах" in alpha["source_label"]
    # 2 проданы 7-го + 3 восьмого = 5 домико-ночей из 6
    assert alpha["metrics"][0]["cuts"]["all"]["pct"] == 83.3


def test_source_label_warns_when_counted_by_types(tmp_path):
    root = tmp_path / "snapshots"
    _write_capacity_history(root, _units({"2026-08-07": "busy",
                                          "2026-08-08": "free"}))
    rows = _build_rows(TARGETS, RECIPES, bs.load_history(root), MONTHS)
    assert "по типам (фонд не снят)" in rows[0]["source_label"]
    assert "занижение" in rows[0]["source_label"]


def test_source_label_marks_partial_capacity(tmp_path):
    root = tmp_path / "snapshots"
    units = dict(_capacity_units({"2026-08-07": ("free", 3, 1)}))
    units["Купол"] = {"2026-08-07": {"state": "free"}}
    _write_capacity_history(root, units)
    rows = _build_rows(TARGETS, RECIPES, bs.load_history(root), MONTHS)
    assert "по домикам частично: фонд снят у 1 из 2 типов" in rows[0]["source_label"]


def test_dynamics_shows_sales_inside_a_type(tmp_path):
    root = tmp_path / "snapshots"
    core.write_snapshot(root, "2026-08-15-1000",
                        {"started_at": "2026-08-15T10:00:00+03:00",
                         "targets": ["alpha"]},
                        [_obj("alpha", _capacity_units(
                            {"2026-08-07": ("free", 3, 3)}),
                            checked_at="2026-08-15T10:00:00+03:00")])
    core.write_snapshot(root, "2026-08-15-1200",
                        {"started_at": "2026-08-15T12:00:00+03:00",
                         "targets": ["alpha"]},
                        [_obj("alpha", _capacity_units(
                            {"2026-08-07": ("free", 3, 1)}),
                            checked_at="2026-08-15T12:00:00+03:00")])
    rows = _build_rows(TARGETS, RECIPES, bs.load_history(root), MONTHS)
    assert len(rows[0]["dynamics"]["newly_sold"]) == 1
    assert "допродано домиков в типах: 1" in bs._dynamics_cell(rows[0])


def test_method_text_explains_multiple_identical_homes(tmp_path):
    root = tmp_path / "snapshots"
    _write_capacity_history(root, _capacity_units({"2026-08-07": ("free", 3, 1)}))
    md = bs.render_markdown(
        _build_rows(TARGETS, RECIPES, bs.load_history(root), MONTHS),
        MONTHS, _meta(run_id="2026-08-15-1000"))
    assert "несколько одинаковых домиков" in md
    assert "по типам" in md
    assert "24%" in md and "30,4%" in md      # замер объекта H 16-29.08


# ---------------------------------------------------------------------------
# Объект-референс: снимается наравне, но кандидатом не считается
# (решение владельца 16.08 — Pine River на постоянной слежке как ориентир)
# ---------------------------------------------------------------------------

REF_TARGET = {"username": "echo", "site": "https://echo.example",
              "reference": True}


def _ref_rows(tmp_path, targets=None):
    """История с alpha и референсом echo, снятыми одинаково."""
    root = tmp_path / "snapshots"
    _write_history(root)
    core.write_snapshot(root, "2026-08-16-1000",
                        {"started_at": "2026-08-16T10:00:00+03:00",
                         "targets": ["alpha", "echo"]},
                        [_obj("alpha", _units({"2026-08-07": "busy",
                                               "2026-09-22": "busy"}),
                              checked_at="2026-08-16T10:00:00+03:00"),
                         _obj("echo", _units({"2026-08-07": "busy",
                                              "2026-09-22": "free"}),
                              checked_at="2026-08-16T10:00:00+03:00")])
    return _build_rows(targets or (TARGETS + [REF_TARGET]), RECIPES,
                         bs.load_history(root), MONTHS)


def test_reference_row_goes_last_and_candidates_keep_their_order():
    """Порядок кандидатов = рейтинг волны 2: референс не должен его сдвигать —
    даже если его строку положили в середину targets.json."""
    rows = _build_rows([TARGETS[0], REF_TARGET] + TARGETS[1:], {}, {}, MONTHS)
    assert [r["username"] for r in rows] == ["alpha", "bravo", "charlie",
                                             "delta", "echo"]


def test_reference_flag_is_set_even_without_snapshots():
    """Флаг живёт в базовом словаре строки: до первого снимка референс тоже
    не должен выглядеть кандидатом."""
    rows = _build_rows([REF_TARGET], {}, {}, MONTHS)
    assert rows[0]["status"] == "missing"
    assert rows[0]["reference"] is True


def test_candidates_are_not_marked_reference():
    rows = _build_rows(TARGETS, {}, {}, MONTHS)
    assert all(r["reference"] is False for r in rows)


def test_markdown_marks_reference_in_table_and_dossier(tmp_path):
    md = bs.render_markdown(_ref_rows(tmp_path), MONTHS,
                            _meta(run_id="2026-08-16-1000"))
    assert "| echo — референс |" in md
    assert "### echo — референс" in md
    assert "не кандидат в клиенты" in md          # объяснение под таблицей


def test_reference_dossier_never_calls_it_a_candidate(tmp_path):
    """Прямое самопротиворечие, ради которого всё и затевалось: строка
    помечена «не кандидат» и тут же названа кандидатом под оффер."""
    rows = _ref_rows(tmp_path)
    ref = [r for r in rows if r["username"] == "echo"][0]
    full = bs.dossier(ref, MONTHS)
    short = bs.dossier_short(ref, MONTHS)
    assert "кандидат под оффер" not in full and "кандидат под оффер" not in short
    assert "Это референс, а не кандидат" in full
    assert "Это референс, а не кандидат" in short


def test_candidate_dossier_keeps_its_offer_verdict(tmp_path):
    """Референс не должен ничего отнимать у кандидатов."""
    rows = _ref_rows(tmp_path)
    alpha = [r for r in rows if r["username"] == "alpha"][0]
    assert "Это референс" not in bs.dossier(alpha, MONTHS)


def test_html_marks_reference_with_neutral_pill(tmp_path):
    html = bs.render_html(_ref_rows(tmp_path), MONTHS,
                          _meta(run_id="2026-08-16-1000"))
    assert '<span class="pill ref">референс</span>' in html
    assert ".pill.ref {" in html
    assert 'class="chip' not in html          # запрет редакции 14.08 в силе


def test_coverage_counts_candidates_only(tmp_path):
    """Знаменатель шапки — цели ПРОДАЖ. Референс завышал бы масштаб работы."""
    rows = _ref_rows(tmp_path)
    line = bs._coverage_line(rows)
    assert "из 4 целей списка" in line
    assert "из 5 целей списка" not in line


def test_uhotels_and_homereserve_have_human_labels():
    """Иначе в пилюле источника у референса стоит сырой слаг движка."""
    assert bs._engine_label("uhotels") == "UHotels"
    assert bs._engine_label("homereserve") == "HomeReserve"


# ---------------------------------------------------------------------------
# Тикет 08: сегодняшний провал не затирает вчерашние цифры
#
# Живой повод (04.09): у цели с рецептом знакомого движка split_queue защиты
# не даёт — она идёт в прогон всегда, и её insufficient_data становился
# ПОСЛЕДНИМ снимком, из которого сводка брала цифры (entries[-1]).
# ---------------------------------------------------------------------------

def _run(root, run_id, objs, targets=("alpha",)):
    core.write_snapshot(root, run_id,
                        {"started_at": objs[0]["checked_at"],
                         "targets": list(targets)}, objs)


def _good_then_failed(tmp_path, good_checked="2026-09-03T06:45:00+03:00"):
    """Вчера снялось (авг 50%), сегодня рецепт сломан и снимок пустой."""
    root = tmp_path / "snapshots"
    _run(root, "2026-09-03-0637",
         [_obj("alpha", _units({"2026-08-07": "busy", "2026-08-08": "free"}),
               checked_at=good_checked)])
    _run(root, "2026-09-04-0631",
         [_obj("alpha", {}, status="insufficient_data",
               reason="рецепт помечен broken — нужна переразведка",
               granularity="aggregate",
               checked_at="2026-09-04T06:39:00+03:00")])
    return bs.load_history(root)


def test_failed_run_keeps_yesterdays_numbers(tmp_path):
    """Приёмка тикета 08: цифры вчерашние, провал — отдельной строкой."""
    history = _good_then_failed(tmp_path)
    row = bs.build_rows(TARGETS[:1], RECIPES, history, MONTHS,
                        today_=date(2026, 9, 4))[0]
    assert row["status"] == "ok"
    assert row["run_id"] == "2026-09-03-0637"
    assert row["metrics"][0]["cuts"]["all"]["pct"] == 50.0
    assert row["checked_at"] == "2026-09-03T06:45:00+03:00"
    assert row["last_failure"]["run_id"] == "2026-09-04-0631"
    assert ("сегодня не снялось: рецепт помечен broken"
            in row["source_label"])


def test_failed_run_visible_in_markdown_and_html(tmp_path):
    history = _good_then_failed(tmp_path)
    rows = bs.build_rows(TARGETS[:1], RECIPES, history, MONTHS,
                         today_=date(2026, 9, 4))
    md = bs.render_markdown(rows, MONTHS, _meta(run_id="2026-09-04-0631"))
    assert "сегодня не снялось: рецепт помечен broken" in md
    assert "50%" in md                       # цифры на месте, а не прочерк
    assert "03.09" in md                     # возраст данных в колонке «Снят»
    html = bs.render_html(rows, MONTHS, _meta(run_id="2026-09-04-0631"))
    assert "сегодня не снялось" in html


def test_failure_from_an_earlier_day_is_dated_not_called_today(tmp_path):
    """Прогона сегодня не было вовсе: врать «сегодня не снялось» нельзя."""
    history = _good_then_failed(tmp_path)
    row = bs.build_rows(TARGETS[:1], RECIPES, history, MONTHS,
                        today_=date(2026, 9, 6))[0]
    assert "не снялось 04.09: рецепт помечен broken" in row["source_label"]
    assert "сегодня не снялось" not in row["source_label"]


def test_all_snapshots_empty_stay_honest_no_data(tmp_path):
    """Вторая приёмка тикета 08: живых цифр нет вовсе — прежнее «нет данных».

    Ровно случай shale_aframe на 04.09: все 24 его снимка insufficient_data,
    показывать нечего, и придумывать нельзя (правило №1).

    Здесь же закрыт третий пункт приёмки тикета 08 («shale_aframe в сводке
    снова показывает свои последние живые цифры с датой»): он НЕВЫПОЛНИМ, и
    это не дефект кода. Перебор всех 24 снимков цели на диске 04.09: у 24 из
    24 status=insufficient_data и ноль известных клеток, причина одна и та
    же — «рецепт помечен broken — нужна переразведка». Посылка тикета
    («история по нему есть») неверна: истории С ЦИФРАМИ у него нет ни одной.
    Показывать в сводке нечего, пока цель не переразведают, и строка
    «нет данных» — правильный ответ, а не незакрытая приёмка.
    """
    root = tmp_path / "snapshots"
    for run_id, day in (("2026-09-03-0637", "03"), ("2026-09-04-0631", "04")):
        _run(root, run_id,
             [_obj("alpha", {}, status="insufficient_data",
                   reason="рецепт помечен broken — нужна переразведка",
                   granularity="aggregate",
                   checked_at=f"2026-09-{day}T06:40:00+03:00")])
    row = bs.build_rows(TARGETS[:1], RECIPES, bs.load_history(root), MONTHS,
                        today_=date(2026, 9, 4))[0]
    assert row["status"] == "insufficient_data"
    assert row["source_label"].startswith("нет данных: рецепт помечен broken")
    assert "сегодня не снялось" not in row["source_label"]
    assert row["last_failure"] is None
    md = bs.render_markdown([row], MONTHS, _meta(run_id="2026-09-04-0631"))
    assert "| — | — | — |" in md            # прочерки вместо выдуманных цифр


def test_empty_snapshot_is_not_thrown_away(tmp_path):
    """Правило №1: пустой снимок с диска остаётся в истории как evidence."""
    history = _good_then_failed(tmp_path)
    assert [run_id for run_id, _ in history["alpha"]] == [
        "2026-09-03-0637", "2026-09-04-0631"]


def test_dynamics_compares_two_snapshots_with_data(tmp_path):
    """Динамика считается между удачными снимками: диф к пустой сетке рисовал
    бы все занятые ночи «новыми занятыми» разом."""
    root = tmp_path / "snapshots"
    _run(root, "2026-09-02-0643",
         [_obj("alpha", _units({"2026-08-07": "busy", "2026-08-08": "busy"}),
               checked_at="2026-09-02T06:51:00+03:00")])
    _run(root, "2026-09-03-0637",
         [_obj("alpha", _units({"2026-08-07": "busy", "2026-08-08": "free"}),
               checked_at="2026-09-03T06:45:00+03:00")])
    _run(root, "2026-09-04-0631",
         [_obj("alpha", {}, status="insufficient_data", reason="403 от хоста",
               granularity="aggregate",
               checked_at="2026-09-04T06:39:00+03:00")])
    row = bs.build_rows(TARGETS[:1], RECIPES, bs.load_history(root), MONTHS,
                        today_=date(2026, 9, 4))[0]
    assert row["dynamics"]["prev_run_id"] == "2026-09-02-0643"
    assert row["dynamics"]["months"]["2026-08"]["delta_pp"] == -50.0
    assert row["dynamics"]["newly_busy"] == []


def test_stale_data_is_marked(tmp_path):
    """Третья приёмка тикета 08: данные старше порога помечены явно.

    Живой повод: glamping_pod_nebom снят 14.08 (квота Островка снималась
    агентом браузером), таймер его пропускает — на 04.09 цифрам 21 сутки.
    """
    history = _good_then_failed(tmp_path, good_checked="2026-08-14T20:42:00+03:00")
    row = bs.build_rows(TARGETS[:1], RECIPES, history, MONTHS,
                        today_=date(2026, 9, 4))[0]
    assert row["stale_days"] == 21
    assert "данные устарели: последний снимок 21 день назад" in row["source_label"]
    md = bs.render_markdown([row], MONTHS, _meta(run_id="2026-09-04-0631"))
    assert "данные устарели" in md


def test_fresh_data_has_no_stale_mark(tmp_path):
    history = _good_then_failed(tmp_path)
    row = bs.build_rows(TARGETS[:1], RECIPES, history, MONTHS,
                        today_=date(2026, 9, 4))[0]
    assert row["stale_days"] == 1
    assert "устарели" not in row["source_label"]


def test_stale_threshold_is_seven_days(tmp_path):
    assert bs.STALE_AFTER_DAYS == 7
    for age, marked in ((7, False), (8, True)):
        history = _good_then_failed(
            tmp_path / f"a{age}",
            good_checked=(date(2026, 9, 4) - timedelta(days=age)).isoformat()
            + "T06:45:00+03:00")
        row = bs.build_rows(TARGETS[:1], RECIPES, history, MONTHS,
                            today_=date(2026, 9, 4))[0]
        assert ("устарели" in row["source_label"]) is marked


def test_days_word_declension():
    assert bs._days_word(1) == "день"
    assert bs._days_word(2) == "дня"
    assert bs._days_word(5) == "дней"
    assert bs._days_word(11) == "дней"
    assert bs._days_word(21) == "день"
    assert bs._days_word(22) == "дня"


def test_dossier_tells_about_failed_run_and_stale_data(tmp_path):
    history = _good_then_failed(tmp_path, good_checked="2026-08-14T20:42:00+03:00")
    row = bs.build_rows(TARGETS[:1], RECIPES, history, MONTHS,
                        today_=date(2026, 9, 4))[0]
    full = bs.dossier(row, MONTHS)
    short = bs.dossier_short(row, MONTHS)
    assert "Сегодня не снялось" in full
    assert "рецепт помечен broken" in full
    assert "14.08" in full                   # от какой даты цифры в таблице
    assert "устарели" in full and "устарели" in short


def test_row_without_today_argument_uses_system_date(tmp_path, monkeypatch):
    """Обратная совместимость: старый вызов без today_ по-прежнему работает."""
    monkeypatch.setattr(core, "today", lambda: date(2026, 9, 4))
    row = bs.build_rows(TARGETS[:1], RECIPES, _good_then_failed(tmp_path),
                        MONTHS)[0]
    assert row["status"] == "ok" and row["stale_days"] == 1


# ---------------------------------------------------------------------------
# Тикет 09: сводка перестаёт читать в память всю историю
# ---------------------------------------------------------------------------

def _long_history(root, runs=40, targets=("alpha", "bravo", "charlie")):
    """runs прогонов подряд, в каждом — все цели со снимком ok."""
    for i in range(runs):
        day = date(2026, 8, 1) + timedelta(days=i)
        checked = f"{day.isoformat()}T06:40:00+03:00"
        core.write_snapshot(
            root, f"{day.isoformat()}-0640",
            {"started_at": checked, "targets": list(targets)},
            [_obj(u, _units({"2026-08-07": "busy"}), checked_at=checked)
             for u in targets])


def _count_reads(monkeypatch, root):
    """Считает РАЗБОР json-файлов снапшотов: именно он стоил 120 МБ RSS."""
    read = []
    original = core.load_json

    def spy(path, error_cls=core.SnapshotError):
        read.append(str(path))
        return original(path, error_cls)

    monkeypatch.setattr(core, "load_json", spy)
    return read


def test_load_history_depth_reads_only_the_last_runs(tmp_path, monkeypatch):
    """Приёмка тикета 09: 40 каталогов, depth=2 — читаются два последних."""
    root = tmp_path / "snapshots"
    _long_history(root)
    read = _count_reads(monkeypatch, root)
    history = bs.load_history(root, depth=2)
    dirs = {Path(p).parent.name for p in read if p.endswith(".json")}
    assert len(dirs) <= 3                    # запас на run.json приграничного
    assert len(read) <= 3 * 2 + 2            # 3 цели × 2 снимка
    assert all(len(v) == 2 for v in history.values())


def test_depth_limited_history_gives_the_same_summary(tmp_path):
    """Побайтная сверка: на тех же данных урезанная история даёт ту же сводку."""
    root = tmp_path / "snapshots"
    _long_history(root)
    targets = [{"username": u} for u in ("alpha", "bravo", "charlie")]
    full = bs.build_rows(targets, RECIPES, bs.load_history(root), MONTHS,
                         today_=date(2026, 9, 9))
    thin = bs.build_rows(targets, RECIPES, bs.load_history(root, depth=2),
                         MONTHS, today_=date(2026, 9, 9))
    meta = _meta(run_id="2026-09-09-0640")
    assert bs.render_markdown(full, MONTHS, meta) == bs.render_markdown(
        thin, MONTHS, meta)
    assert bs.render_html(full, MONTHS, meta) == bs.render_html(
        thin, MONTHS, meta)


def test_depth_keeps_the_last_good_snapshot_however_old(tmp_path):
    """Цель снялась один раз и с тех пор падает: живой снимок обязан
    доехать до сводки, иначе экономия памяти сотрёт единственные цифры."""
    root = tmp_path / "snapshots"
    _run(root, "2026-08-01-0640",
         [_obj("alpha", _units({"2026-08-07": "busy"}),
               checked_at="2026-08-01T06:40:00+03:00")])
    for i in range(1, 30):
        day = date(2026, 8, 1) + timedelta(days=i)
        _run(root, f"{day.isoformat()}-0640",
             [_obj("alpha", {}, status="insufficient_data",
                   reason="403 от хоста", granularity="aggregate",
                   checked_at=f"{day.isoformat()}T06:40:00+03:00")])
    history = bs.load_history(root, depth=2)
    assert history["alpha"][0][0] == "2026-08-01-0640"
    assert history["alpha"][0][1]["status"] == "ok"
    row = bs.build_rows(TARGETS[:1], RECIPES, history, MONTHS,
                        today_=date(2026, 8, 30))[0]
    assert row["status"] == "ok"
    assert row["run_id"] == "2026-08-01-0640"


def test_load_history_without_depth_reads_everything(tmp_path):
    """Обратная совместимость: прежний вызов отдаёт всю историю целиком."""
    root = tmp_path / "snapshots"
    _long_history(root, runs=5)
    history = bs.load_history(root)
    assert len(history["alpha"]) == 5


def test_summary_reads_history_with_a_bound(tmp_path, monkeypatch):
    """main не читает историю целиком: иначе экономия остаётся на бумаге."""
    seen = {}

    def spy(root, depth=None):
        seen["depth"] = depth
        return {}

    monkeypatch.setattr(bs, "load_history", spy)
    _write_targets_and_recipes(tmp_path)
    root = tmp_path / "snapshots"
    _long_history(root, runs=2)
    bs.main(["--snapshot-root", str(root),
             "--targets", str(tmp_path / "targets.json"),
             "--recipes", str(tmp_path / "recipes.json"),
             "--out", str(tmp_path / "occupancy.md"),
             "--out-html", str(tmp_path / "occupancy.html")])
    assert seen["depth"] == bs.HISTORY_DEPTH


# ---------------------------------------------------------------------------
# Дата-бомба (блокер ревью волны 1): месяцы сводки — скользящее окно
# ---------------------------------------------------------------------------

def _write_targets_and_recipes(tmp_path):
    core.save_targets(tmp_path / "targets.json",
                      [{"username": "alpha", "site": "https://alpha.example"}])
    core.save_recipes(tmp_path / "recipes.json",
                      {"alpha": {"engine": "travelline", "status": "ok"}})


def test_summary_months_are_rolling_not_calendar(tmp_path, monkeypatch):
    """05.11.2026 — день, после которого прежняя сводка показывала «нет
    данных» по ВСЕМ объектам: месяцы были зашиты как авг-окт 2026."""
    monkeypatch.setattr(core, "today", lambda: date(2026, 11, 5))
    root = tmp_path / "snapshots"
    checked = "2026-11-05T06:40:00+03:00"
    _run(root, "2026-11-05-0640",
         [_obj("alpha", _units({"2026-11-07": "busy", "2026-12-05": "busy"}),
               checked_at=checked)])
    _write_targets_and_recipes(tmp_path)
    out = tmp_path / "occupancy.md"
    assert bs.main(["--snapshot-root", str(root),
                    "--targets", str(tmp_path / "targets.json"),
                    "--recipes", str(tmp_path / "recipes.json"),
                    "--out", str(out),
                    "--out-html", str(tmp_path / "occupancy.html")]) == 0
    md = out.read_text(encoding="utf-8")
    assert "ноя 2026" in md and "дек 2026" in md and "янв 2027" in md
    assert "авг 2026" not in md
    assert "100%" in md                      # ночи снимаются и видны


def test_build_summary_has_no_calendar_constant():
    """Приёмка тикета 01: ни одного календарного литерала года в горизонте."""
    source = Path(bs.__file__).read_text(encoding="utf-8")
    assert "DEFAULT_MONTHS" not in source


# ---------------------------------------------------------------------------
# Пометка честности для отчётов (тикет 06): месяц за горизонтом ФОНДА
# ---------------------------------------------------------------------------

def test_month_beyond_inventory_horizon_is_marked(tmp_path):
    """«Фонд не снимался» — это не «движок не отдал фонд»: первое про
    границу вопроса (45 ближних ночей), второе про отказ движка."""
    root = tmp_path / "snapshots"
    units = {"А-фрейм": {"2026-08-07": {"state": "free", "units_total": 3,
                                        "units_free": 1},
                         "2026-10-07": {"state": "busy"}}}
    _run(root, "2026-08-14-1000",
         [dict(_obj("alpha", units, checked_at="2026-08-14T10:00:00+03:00"),
               inventory_until="2026-09-28")])
    rows = bs.build_rows(TARGETS[:1], RECIPES, bs.load_history(root), MONTHS,
                         today_=date(2026, 8, 14))
    assert rows[0]["inventory_until"] == "2026-09-28"
    assert rows[0]["inventory_gap"] == {"2026-08": False, "2026-09": False,
                                        "2026-10": True}
    md = bs.render_markdown(rows, MONTHS, _meta(run_id="2026-08-14-1000"))
    # пометка стоит в КЛЕТКЕ октября, а не только в сноске «как читать»
    assert "фонд не снимался" in _table(md)
    assert "по 1 ночи, фонд не снимался)" in _table(md)
    text = bs.dossier(rows[0], MONTHS)
    assert "Фонд типов дальше 28.09 не снимался" in text
    assert "за октябрь проценты занижены" in text
    html = bs.render_html(rows, MONTHS, _meta(run_id="2026-08-14-1000"))
    assert "фонд не снимался" in html.split("</table>", 1)[0]


def test_without_inventory_until_nothing_is_marked(tmp_path):
    """Поля ещё нет в снапшотах — ведём себя ровно как раньше."""
    rows = _rows(tmp_path)
    assert all(r.get("inventory_until") is None for r in rows)
    md = bs.render_markdown(rows, MONTHS, _meta())
    assert "фонд не снимался" not in _table(md)   # в сноске текст остаётся
    assert all("Фонд типов" not in bs.dossier(r, MONTHS) for r in rows)


# ---------------------------------------------------------------------------
# Ревью волны 2 (summary + блокер 2 probes): снимок «со статусом, но без
# клеток», половинчатый горизонт фонда, экранирование чужого текста,
# ограничитель истории и оговорка «по N ночам» после разметки стены
# ---------------------------------------------------------------------------

def _all_unknown(dates):
    """Сетка, где все ночи в состоянии unknown: ровно то, что оставляет за
    собой ветка «ВЕСЬ ГОРИЗОНТ» правила окна продаж (probes/__init__.py)."""
    return {"Дом": {d: {"state": "unknown"} for d in dates}}


def _good_then_blank(tmp_path, blank_units, *, status="partial", reason=""):
    """Вчера снялось (авг 50%), сегодня снимок «со статусом, но без клеток»."""
    root = tmp_path / "snapshots"
    _run(root, "2026-09-03-0637",
         [_obj("alpha", _units({"2026-08-07": "busy", "2026-08-08": "free"}),
               checked_at="2026-09-03T06:45:00+03:00")])
    _run(root, "2026-09-04-0631",
         [_obj("alpha", blank_units, status=status, reason=reason,
               checked_at="2026-09-04T06:39:00+03:00")])
    return bs.load_history(root)


def test_partial_without_known_cells_does_not_erase_yesterday(tmp_path):
    """Ровно дефект, ради которого писался тикет 08, по второму каналу:
    правило окна продаж переводит ВСЕ клетки в unknown и понижает ok до
    partial, а фильтр по одному лишь статусу считал такой снимок данными."""
    history = _good_then_blank(
        tmp_path, _all_unknown(["2026-08-07", "2026-08-08"]),
        reason="окно продаж закрыто: весь горизонт занят")
    row = bs.build_rows(TARGETS[:1], RECIPES, history, MONTHS,
                        today_=date(2026, 9, 4))[0]
    assert row["run_id"] == "2026-09-03-0637"
    assert row["metrics"][0]["cuts"]["all"]["pct"] == 50.0
    assert row["last_failure"]["run_id"] == "2026-09-04-0631"
    assert "сегодня не снялось: окно продаж закрыто" in row["source_label"]
    md = bs.render_markdown([row], MONTHS, _meta(run_id="2026-09-04-0631"))
    assert "50%" in md


def test_blank_snapshot_without_reason_names_the_cause_itself(tmp_path):
    """У снимка со статусом ok причины в поле reason обычно нет: «причина не
    записана» тут врёт — причина видна по самой сетке."""
    units = {"Дом": {"2026-08-07": {"state": "sales_not_open"},
                     "2026-08-08": {"state": "sales_not_open"}}}
    history = _good_then_blank(tmp_path, units, status="ok")
    row = bs.build_rows(TARGETS[:1], RECIPES, history, MONTHS,
                        today_=date(2026, 9, 4))[0]
    assert "продажи не открыты" in row["last_failure"]["reason"]
    assert "причина не записана" not in row["source_label"]


def test_snapshot_with_only_sales_not_open_stays_as_it_was(tmp_path):
    """Регресс-щит для ok_reka и wood_glamp: у них ВСЯ история — сетка без
    единой проверенной ночи (21 снимок из 21 на 04.09), и строка обязана
    остаться прежней «продажи не открыты», а не превратиться в провал."""
    units = {"Дом": {"2026-09-01": {"state": "sales_not_open"},
                     "2026-09-02": {"state": "sales_not_open"}}}
    rows = _single_row(tmp_path, units)
    assert rows[0]["status"] == "ok"
    assert rows[0]["last_failure"] is None
    assert "продажи не открыты" in bs.render_markdown(
        rows, MONTHS, _meta(run_id="2026-08-14-1000"))


def test_history_quota_counts_snapshots_with_cells(tmp_path):
    """Ограничитель истории обязан считать «с данными» так же, как их считает
    сводка: иначе два пустых снимка съедают квоту и динамика теряется."""
    root = tmp_path / "snapshots"
    _run(root, "2026-09-01-0640",
         [_obj("alpha", _units({"2026-08-07": "busy", "2026-08-08": "busy"}),
               checked_at="2026-09-01T06:40:00+03:00")])
    _run(root, "2026-09-02-0640",
         [_obj("alpha", _units({"2026-08-07": "busy", "2026-08-08": "free"}),
               checked_at="2026-09-02T06:40:00+03:00")])
    for run_id, day in (("2026-09-03-0640", "03"), ("2026-09-04-0640", "04")):
        _run(root, run_id,
             [_obj("alpha", _all_unknown(["2026-08-07", "2026-08-08"]),
                   status="partial", reason="окно продаж закрыто",
                   checked_at=f"2026-09-{day}T06:40:00+03:00")])
    row = bs.build_rows(TARGETS[:1], RECIPES,
                        bs.load_history(root, depth=bs.HISTORY_DEPTH), MONTHS,
                        today_=date(2026, 9, 4))[0]
    assert row["run_id"] == "2026-09-02-0640"
    assert row["dynamics"]["prev_run_id"] == "2026-09-01-0640"
    assert row["dynamics"]["months"]["2026-08"]["delta_pp"] == -50.0


def test_history_stops_scanning_a_target_that_never_had_data(tmp_path,
                                                             monkeypatch):
    """shale_aframe: 24 снимка из 24 пустые (проверено перебором 04.09).
    Условие «набрать depth снимков С ДАННЫМИ» на такой цели не выполняется
    никогда, и разбор рос вместе с историей."""
    root = tmp_path / "snapshots"
    runs = bs.NO_DATA_SCAN_LIMIT + 20
    for i in range(runs):
        day = date(2026, 8, 1) + timedelta(days=i)
        _run(root, f"{day.isoformat()}-0640",
             [_obj("alpha", {}, status="insufficient_data",
                   reason="рецепт помечен broken — нужна переразведка",
                   granularity="aggregate",
                   checked_at=f"{day.isoformat()}T06:40:00+03:00")])
    read = _count_reads(monkeypatch, root)
    history = bs.load_history(root, depth=bs.HISTORY_DEPTH)
    assert len(read) <= bs.NO_DATA_SCAN_LIMIT + 1   # потолок, а не вся история
    assert len(read) < runs
    row = bs.build_rows(TARGETS[:1], RECIPES, history, MONTHS,
                        today_=date(2026, 8, 1) + timedelta(days=runs))[0]
    assert row["status"] == "insufficient_data"   # строка та же, что и была


def test_scan_limit_keeps_a_recent_good_snapshot(tmp_path):
    """Ограничитель не имеет права стереть живые цифры прошлой недели:
    считаются СВОИ снимки цели, а их у редкой цели немного."""
    root = tmp_path / "snapshots"
    start = date(2026, 8, 25)
    _run(root, f"{start.isoformat()}-0640",
         [_obj("alpha", _units({"2026-08-07": "busy"}),
               checked_at=f"{start.isoformat()}T06:40:00+03:00")])
    for i in range(1, bs.NO_DATA_SCAN_LIMIT):
        day = start + timedelta(days=i)
        _run(root, f"{day.isoformat()}-0640",
             [_obj("alpha", {}, status="insufficient_data",
                   reason="403 от хоста", granularity="aggregate",
                   checked_at=f"{day.isoformat()}T06:40:00+03:00")])
    history = bs.load_history(root, depth=bs.HISTORY_DEPTH)
    row = bs.build_rows(TARGETS[:1], RECIPES, history, MONTHS,
                        today_=start + timedelta(days=bs.NO_DATA_SCAN_LIMIT))[0]
    assert row["status"] == "ok"
    assert row["run_id"] == f"{start.isoformat()}-0640"


def _october_grid(fund_until_day=31, sold=()):
    """Октябрь целиком у типа из трёх домиков: 1–20 заняты, 21–31 свободны
    (продан один домик из трёх). Фонд снят только по ночь fund_until_day —
    дальше клетка без units_total, и ночь весит один домик, а не три:
    ровно так режет горизонт фонда (тикет 06).

    sold — ночи, целиком выкупленные к этому снимку (живая динамика).
    """
    cells = {}
    for day in range(1, 32):
        iso = f"2026-10-{day:02d}"
        busy = day <= 20 or iso in sold
        cell = {"state": "busy" if busy else "free"}
        if day <= fund_until_day:
            cell["units_total"] = 3
            cell["units_free"] = 0 if busy else 1
        cells[iso] = cell
    return {"А-фрейм": cells}


def test_month_half_covered_by_inventory_is_marked(tmp_path):
    """Половина октября снята с фондом, половина — без: до правки пометку
    получал только месяц ЦЕЛИКОМ за границей, и завтрашний прогон (фонд до
    20.10) отдал бы октябрь как обычную цифру."""
    root = tmp_path / "snapshots"
    _run(root, "2026-09-05-0643",
         [dict(_obj("alpha", _october_grid(fund_until_day=20),
                    checked_at="2026-09-05T06:43:00+03:00"),
               inventory_until="2026-10-20")])
    rows = bs.build_rows(TARGETS[:1], RECIPES, bs.load_history(root), MONTHS,
                         today_=date(2026, 9, 5))
    assert rows[0]["inventory_gap"]["2026-10"] is True
    assert "фонд не снимался" in _table(bs.render_markdown(
        rows, MONTHS, _meta(run_id="2026-09-05-0643")))
    assert "Фонд типов дальше 20.10 не снимался" in bs.dossier(rows[0], MONTHS)


def test_month_fully_inside_inventory_is_not_marked(tmp_path):
    """Обратная сторона: пометка не должна расползтись на месяцы, все ночи
    которых сняты с фондом."""
    root = tmp_path / "snapshots"
    _run(root, "2026-09-05-0643",
         [dict(_obj("alpha", _october_grid(),
                    checked_at="2026-09-05T06:43:00+03:00"),
               inventory_until="2026-11-30")])
    rows = bs.build_rows(TARGETS[:1], RECIPES, bs.load_history(root), MONTHS,
                         today_=date(2026, 9, 5))
    assert rows[0]["inventory_gap"] == {"2026-08": False, "2026-09": False,
                                        "2026-10": False}
    assert "фонд не снимался" not in _table(bs.render_markdown(
        rows, MONTHS, _meta(run_id="2026-09-05-0643")))


def test_dynamics_says_when_the_inventory_border_moved(tmp_path):
    """Приёмка тикета 06: сдвиг рычага не читается как отток броней.
    TL-объект на живых данных 04.09: окт 42.9% -> 39.5% только оттого,
    что фонд перестали спрашивать после 20.10."""
    root = tmp_path / "snapshots"
    _run(root, "2026-09-04-0631",
         [dict(_obj("alpha", _october_grid(),
                    checked_at="2026-09-04T06:31:00+03:00"),
               inventory_until="2026-10-31")])
    _run(root, "2026-09-05-0643",
         [dict(_obj("alpha", _october_grid(fund_until_day=20),
                    checked_at="2026-09-05T06:43:00+03:00"),
               inventory_until="2026-10-20")])
    rows = bs.build_rows(TARGETS[:1], RECIPES, bs.load_history(root), MONTHS,
                         today_=date(2026, 9, 5))
    assert rows[0]["dynamics"]["gap_changed"] == ["2026-10"]
    cell = bs._dynamics_cell(rows[0])
    assert "граница фонда" in cell
    assert "граница фонда" in bs.render_markdown(
        rows, MONTHS, _meta(run_id="2026-09-05-0643"))


def test_dynamics_is_silent_when_the_border_did_not_move(tmp_path):
    root = tmp_path / "snapshots"
    _run(root, "2026-09-04-0631",
         [dict(_obj("alpha", _october_grid(fund_until_day=20),
                    checked_at="2026-09-04T06:40:00+03:00"),
               inventory_until="2026-10-20")])
    _run(root, "2026-09-05-0643",
         [dict(_obj("alpha", _october_grid(fund_until_day=20,
                                           sold=("2026-10-25",)),
                    checked_at="2026-09-05T06:40:00+03:00"),
               inventory_until="2026-10-20")])
    rows = bs.build_rows(TARGETS[:1], RECIPES, bs.load_history(root), MONTHS,
                         today_=date(2026, 9, 5))
    assert rows[0]["dynamics"]["gap_changed"] == []
    cell = bs._dynamics_cell(rows[0])
    assert "окт" in cell and "пп" in cell      # брони двигались, и это видно
    assert "граница фонда" not in cell


def test_percent_by_one_night_after_the_wall_is_qualified(tmp_path):
    """Блокер 2 ревью пробников: детектор стены штампует sales_not_open на
    30+ ночей, процент считается по одной оставшейся ночи, а оговорка «по N
    ночам» молчала — checked считал ночи «продажи не открыты» проверенными."""
    cells = {f"2026-09-{d:02d}": {"state": "sales_not_open"}
             for d in range(2, 31)}
    cells["2026-09-01"] = {"state": "busy"}
    rows = _single_row(tmp_path, {"Дом": cells})
    md = bs.render_markdown(rows, MONTHS, _meta(run_id="2026-08-14-1000"))
    assert "по 1 ночи" in _table(md)
    assert "по 1 ночи" in bs.dossier(rows[0], MONTHS)
    assert "по 1 ночи" in bs.render_html(rows, MONTHS,
                                         _meta(run_id="2026-08-14-1000"))


def test_broken_reason_does_not_break_the_markdown_table(tmp_path):
    """Текст в рецепт кладёт чужой движок (bronirui отдаёт message как есть):
    вертикальная черта и перевод строки в нём разъезжают всю таблицу."""
    recipes = dict(RECIPES)
    recipes["bravo"] = {"engine": "bronirui", "status": "broken",
                        "broken_reason": "HTTP 400 | module off\nповторите"}
    rows = _build_rows(TARGETS[1:2], recipes, {}, MONTHS)
    md = bs.render_markdown(rows, MONTHS, _meta())
    body = [line for line in md.splitlines() if line.startswith("| bravo")]
    assert len(body) == 1
    header = [line for line in md.splitlines()
              if line.startswith("| Объект")][0]
    assert body[0].replace("\\|", "").count("|") == header.count("|")
    assert "\\|" in body[0]
    assert "module off повторите" in body[0]
