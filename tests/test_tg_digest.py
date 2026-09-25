# -*- coding: utf-8 -*-
"""Сводка объекта в телеграм: кого уведомлять, что писать, о чём молчать.

Просьба оператора 16.08 — «нужно уведомлять в тг о Pine River». Проверяем ровно
то, что ломается тихо: что молчание не наступает при пропаже данных, что
режим "changes" не глушит неполный снимок, и что человеку в чат не уезжает
машинный формат чисел.
"""
from datetime import date

import occupancy_core as core
import tg_digest

MONTHS = ["2026-08", "2026-09"]
TODAY = date(2026, 8, 18)


def cells(spec):
    """{дата: (state, всего, свободно)} -> клетки юнита."""
    out = {}
    for day, value in spec.items():
        if isinstance(value, str):
            out[day] = {"state": value}
        else:
            state, total, free = value
            out[day] = {"state": state, "units_total": total,
                        "units_free": free}
    return out


def obj(units, status="ok", reason=""):
    return {"username": "pineriver_hotel", "units": units, "status": status,
            "reason": reason}


TARGET = {"username": "pineriver_hotel", "title": "Pine River", "notify": True}


# ---------------------------------------------------------------------------
# Кого уведомлять
# ---------------------------------------------------------------------------

def test_notify_modes():
    assert tg_digest.wants_notify({"notify": True}) == "daily"
    assert tg_digest.wants_notify({"notify": "daily"}) == "daily"
    assert tg_digest.wants_notify({"notify": "changes"}) == "changes"
    assert tg_digest.wants_notify({}) == ""
    assert tg_digest.wants_notify({"notify": False}) == ""
    assert tg_digest.wants_notify(None) == ""


def test_title_falls_back_to_username():
    assert tg_digest.title({"title": "Pine River"}, {}) == "Pine River"
    assert tg_digest.title({}, {"username": "art.glamp"}) == "art.glamp"


def test_target_without_notify_gets_no_message():
    units = {"Дом": cells({"2026-08-18": ("busy", 1, 0)})}
    assert tg_digest.digest({"username": "x"}, obj(units), None, MONTHS,
                            TODAY) == ""


# ---------------------------------------------------------------------------
# Что в сообщении
# ---------------------------------------------------------------------------

def test_daily_message_carries_months_split_and_fund():
    units = {"Баррель": cells({"2026-08-21": ("busy", 30, 0),    # пятница
                               "2026-08-18": ("free", 30, 15)})}  # вторник
    text = tg_digest.digest(TARGET, obj(units), None, MONTHS, TODAY)
    assert text.startswith("Pine River — снимок 18.08")
    assert "Занято: авг" in text
    assert "Выходные августа" in text and "будни" in text
    assert "Фонд: 30 домиков в 1 категориях" in text
    assert "первый снимок" in text


def test_fund_is_called_a_lower_bound_when_partially_known():
    """У объекта, где фонд снят не везде, «180 домиков» — это «не меньше»."""
    units = {"С фондом": cells({"2026-08-18": ("free", 5, 2)}),
             "Без фонда": cells({"2026-08-18": "busy"})}
    text = tg_digest.digest(TARGET, obj(units), None, MONTHS, TODAY)
    assert "не меньше" in text


def test_change_line_counts_movement():
    prev = {"Дом": cells({"2026-08-18": ("free", 10, 5)})}
    cur = {"Дом": cells({"2026-08-18": ("free", 10, 3)})}
    text = tg_digest.digest(TARGET, obj(cur), obj(prev), MONTHS, TODAY,
                            gap_days=1)
    assert "За сутки" in text
    assert "допродали домиков: 1" in text


def test_newly_busy_nights_are_counted():
    prev = {"Дом": cells({"2026-08-18": ("free", 1, 1)})}
    cur = {"Дом": cells({"2026-08-18": ("busy", 1, 0)})}
    text = tg_digest.digest(TARGET, obj(cur), obj(prev), MONTHS, TODAY)
    assert "Занятых ночей стало больше на 1" in text


def test_decimal_separator_is_a_comma():
    """Человеку в чат, а не в лог: «+1,2 пп», а не «+1.2 пп»."""
    prev = {"Дом": cells({f"2026-08-{d:02d}": ("free", 100, 100)
                          for d in range(1, 11)})}
    cur = {"Дом": cells({f"2026-08-{d:02d}": ("free", 100, 99)
                         for d in range(1, 11)})}
    text = tg_digest.digest(TARGET, obj(cur), obj(prev), MONTHS, TODAY)
    assert " пп" in text
    assert ".0 пп" not in text and ".5 пп" not in text


def test_quiet_day_is_said_out_loud():
    """«Ничего не двигалось» — тоже новость: молчание её не передаёт."""
    units = {"Дом": cells({"2026-08-18": ("free", 10, 5)})}
    text = tg_digest.digest(TARGET, obj(units), obj(units), MONTHS, TODAY)
    assert "не двигался" in text


# ---------------------------------------------------------------------------
# О чём молчать нельзя
# ---------------------------------------------------------------------------

def test_lost_data_always_notifies():
    """Пропавший объект — единственная новость, требующая человека."""
    text = tg_digest.digest(TARGET, obj({}, status="insufficient_data",
                                        reason="рецепт помечен broken"),
                            None, MONTHS, TODAY)
    assert "Данные снять не удалось" in text
    assert "broken" in text


def test_lost_data_notifies_even_in_changes_mode():
    target = dict(TARGET, notify="changes")
    text = tg_digest.digest(target, obj({}, status="insufficient_data",
                                        reason="хост не ответил"),
                            None, MONTHS, TODAY)
    assert "Данные снять не удалось" in text


def test_partial_snapshot_says_why():
    units = {"Дом": cells({"2026-08-18": ("free", 10, 5)})}
    text = tg_digest.digest(TARGET, obj(units, status="partial",
                                        reason="ночь 20.08: HTTP 503"),
                            obj(units), MONTHS, TODAY)
    assert "Снимок неполный: ночь 20.08: HTTP 503" in text


def test_changes_mode_stays_quiet_when_nothing_moved():
    target = dict(TARGET, notify="changes")
    units = {"Дом": cells({"2026-08-18": ("free", 10, 5)})}
    assert tg_digest.digest(target, obj(units), obj(units), MONTHS,
                            TODAY) == ""


def test_changes_mode_speaks_when_something_moved():
    target = dict(TARGET, notify="changes")
    prev = {"Дом": cells({"2026-08-18": ("free", 10, 5)})}
    cur = {"Дом": cells({"2026-08-18": ("busy", 10, 0)})}
    assert tg_digest.digest(target, obj(cur), obj(prev), MONTHS, TODAY) != ""


def test_changes_mode_speaks_when_snapshot_is_partial():
    """Неполный снимок — не «тишина», даже если проценты совпали."""
    target = dict(TARGET, notify="changes")
    units = {"Дом": cells({"2026-08-18": ("free", 10, 5)})}
    text = tg_digest.digest(target, obj(units, status="partial",
                                        reason="ночь 20.08: HTTP 503"),
                            obj(units), MONTHS, TODAY)
    assert "Снимок неполный" in text


def test_moved_ignores_rounding_noise():
    assert tg_digest.moved({"months": {"2026-08": {"delta_pp": 0.0}},
                            "newly_busy": [], "newly_sold": []}) is False
    assert tg_digest.moved({"months": {"2026-08": {"delta_pp": 0.4}},
                            "newly_busy": [], "newly_sold": []}) is True


def test_message_fits_telegram_limit():
    """Сообщение не должно упираться в предел Bot API (4096 знаков)."""
    units = {f"Категория {i}": cells({"2026-08-18": ("free", 10, 5)})
             for i in range(40)}
    text = tg_digest.digest(TARGET, obj(units), None, MONTHS, TODAY)
    assert 0 < len(text) < 1000


# ---------------------------------------------------------------------------
# Итог прогона одним сообщением (тикет 16)
# ---------------------------------------------------------------------------

def report(total=5, ok=3, partial=1, empty=1, empty_rows=(("wood_glamp",
                                                           "рецепт помечен broken"),),
           skipped=(), skipped_groups=None, late=(), share=0.2,
           threshold=0.2, alert=""):
    return {"total": total, "ok": ok, "partial": partial, "empty": empty,
            "empty_rows": list(empty_rows), "skipped": list(skipped),
            "skipped_groups": dict(skipped_groups or {}),
            "late": list(late), "empty_share": share,
            "threshold": threshold, "alert": alert}


def test_run_summary_counts_targets():
    text = tg_digest.run_summary(report(), TODAY)
    assert "прогон 18.08" in text
    assert "Снято 5 целей" in text
    assert "полностью 3" in text and "частично 1" in text
    assert "без данных 1 (20%)" in text


def test_run_summary_names_targets_without_data():
    """Пропавший объект — единственная новость прогона, требующая человека."""
    text = tg_digest.run_summary(report(), TODAY)
    assert "wood_glamp — рецепт помечен broken" in text


def test_run_summary_mentions_skipped_and_late():
    text = tg_digest.run_summary(
        report(skipped=[("a", "рецепта нет в реестре", "scout"),
                        ("b", "агрегатор", "aggregator")],
               skipped_groups={"scout": 1, "aggregator": 1, "no_module": 0},
               late=["c"]), TODAY)
    assert "Пропущено до пробника: 2" in text
    assert "Не дошла очередь до дедлайна: 1" in text


def test_run_summary_splits_skipped_by_who_has_to_act():
    """Одно число на всех звало человека туда, где работы нет.

    «Без онлайн-канала» — уже сделанный и записанный вывод, а не долг агента;
    смешивать его с «ждут разведки» значит каждое утро показывать владельцу
    выдуманную работу (09.09.2026: 18 таких из 38).
    """
    text = tg_digest.run_summary(
        report(skipped=[("a", "нет рецепта", "scout")] * 3
               + [("b", "агрегатор", "aggregator")] * 2
               + [("c", "канала нет", "no_module")] * 18,
               skipped_groups={"scout": 3, "aggregator": 2, "no_module": 18}),
        TODAY)
    assert ("Пропущено до пробника: 23 — ждут разведки 3, "
            "агрегаторы (ветка агента) 2, без онлайн-канала 18." in text)


def test_run_summary_shows_zero_scouting_debt():
    """«Ждут разведки: 0» печатается ВСЕГДА — ради этой строки всё и делалось."""
    text = tg_digest.run_summary(
        report(skipped=[("c", "канала нет", "no_module")] * 4,
               skipped_groups={"scout": 0, "aggregator": 0, "no_module": 4}),
        TODAY)
    assert "ждут разведки 0" in text
    assert "без онлайн-канала 4" in text


def test_skip_labels_match_the_run():
    """Имена групп в чате и в прогоне — одни и те же слова."""
    import run_scheduled
    assert tg_digest.SKIP_LABELS == run_scheduled.SKIP_LABELS
    assert tg_digest.SKIP_ORDER == run_scheduled.SKIP_ORDER


def test_run_summary_carries_the_alert_text():
    text = tg_digest.run_summary(report(alert="без данных 40% при пороге 20%"),
                                 TODAY)
    assert "Внимание: без данных 40% при пороге 20%" in text


def test_run_summary_is_quiet_about_what_did_not_happen():
    """Ноль пропущенных и ноль опоздавших — строк о них в сообщении нет."""
    text = tg_digest.run_summary(report(empty=0, empty_rows=(), share=0.0),
                                 TODAY)
    assert "Пропущено" not in text and "дедлайна" not in text
    assert "Внимание" not in text
    assert "Без данных:" not in text


def test_run_summary_survives_fifty_three_targets():
    """53 цели без данных — сообщение обязано влезть в предел Bot API."""
    rows = [(f"объект_{i}", "движок ответил 403 — нас не пускают")
            for i in range(53)]
    text = tg_digest.run_summary(
        report(total=53, ok=0, partial=0, empty=53, empty_rows=rows,
               share=1.0, alert="без данных 100% целей при пороге 20%"), TODAY)
    assert 0 < len(text) < 4096
    assert "и ещё" in text


def test_run_summary_without_snapshot_says_so():
    """Снапшот не прочитан — молчать нельзя: это тоже новость для человека."""
    text = tg_digest.run_summary(report(total=0, ok=0, partial=0, empty=0,
                                        empty_rows=(), share=0.0), TODAY)
    assert "снапшот" in text.lower()


def test_run_summary_agrees_with_the_numeral():
    """Сообщение читает человек: «снято 4 целей» его спотыкает."""
    assert "Снято 4 цели:" in tg_digest.run_summary(report(total=4), TODAY)
    assert "Снято 1 цель:" in tg_digest.run_summary(report(total=1), TODAY)
    assert "Снято 11 целей:" in tg_digest.run_summary(report(total=11), TODAY)


def test_alert_stands_before_the_long_tail():
    """Обрезка по пределу Bot API режет ХВОСТ, поэтому строка, требующая
    человека, обязана стоять в начале: при длинных причинах именно она и
    пропадала, а сообщение считалось доставленным."""
    rows = [(f"объект_{i}", "движок ответил 403 — " + "подробности " * 30)
            for i in range(12)]
    text = tg_digest.run_summary(
        report(total=12, ok=0, partial=0, empty=12, empty_rows=rows,
               share=1.0, alert="без данных 100% целей при пороге 20%"), TODAY)
    assert text.index("Внимание:") < text.index("Без данных:")
    try:
        import cf.notify as notify_mod      # тот же транспорт, что и в бою
        fit = notify_mod.fit
    except ImportError:                     # витрина: пакета cf нет — предел Bot API 4096
        def fit(s, limit=4096):
            return s if len(s) <= limit else s[:limit]
    assert "Внимание:" in fit(text)


def test_long_reason_is_cut_in_a_known_place():
    """Причина собирается из групп отказов и доходит до двух килобайт: одна
    такая строка съедала бы половину сообщения."""
    text = tg_digest.run_summary(
        report(empty_rows=[("wood_glamp", "хост молчит — " + "ещё " * 200)]),
        TODAY)
    row = [ln for ln in text.splitlines() if ln.startswith("· wood_glamp")][0]
    assert len(row) < tg_digest.REASON_LIMIT + 40 and row.endswith("…")


def test_empty_snapshot_says_it_once():
    """Пустой снапшот — одна новость, а не две одинаковые строки подряд."""
    text = tg_digest.run_summary(
        report(total=0, ok=0, partial=0, empty=0, empty_rows=(), share=0.0,
               alert="снапшот прогона не прочитан — снятых целей в нём нет"),
        TODAY)
    assert text.lower().count("снапшот") == 1


# ---------------------------------------------------------------------------
# Волна 4: дата снимка и период сравнения не выдумываются (major ревью в3)
# ---------------------------------------------------------------------------
#
# Цель, которую таймер пропускает (агрегатор или ещё не разведанный рецепт),
# сегодняшнего файла не получает — и её трёхнедельная цифра уходила владельцу
# под сегодняшней датой со словами «за сутки календарь не двигался». Сводка о
# возрасте данных говорит честно (колонка «Снят»), сообщение в чат — нет.

def test_head_carries_the_day_of_the_snapshot(tmp_path):
    units = {"Дом": cells({"2026-08-18": ("free", 10, 5)})}
    text = tg_digest.digest(TARGET, obj(units), None, MONTHS, TODAY,
                            taken=date(2026, 8, 14))
    assert "снимок 14.08" in text
    assert "сегодня не снимался" in text


def test_todays_snapshot_is_not_marked_stale():
    units = {"Дом": cells({"2026-08-18": ("free", 10, 5)})}
    text = tg_digest.digest(TARGET, obj(units), None, MONTHS, TODAY,
                            taken=TODAY)
    assert "снимок 18.08" in text and "не снимался" not in text


def test_change_line_names_the_real_period():
    """«За сутки» у снимков недельной давности — выдумка о свежести."""
    prev = {"Дом": cells({"2026-08-18": ("free", 10, 5)})}
    cur = {"Дом": cells({"2026-08-18": ("free", 10, 3)})}
    text = tg_digest.digest(TARGET, obj(cur), obj(prev), MONTHS, TODAY,
                            taken=date(2026, 8, 14), gap_days=7)
    assert "За 7 дней" in text and "За сутки" not in text


def test_quiet_period_says_how_long_it_was_quiet():
    units = {"Дом": cells({"2026-08-18": ("free", 10, 5)})}
    text = tg_digest.digest(TARGET, obj(units), obj(units), MONTHS, TODAY,
                            gap_days=3)
    assert "За 3 дня календарь не двигался" in text


def test_unknown_period_is_said_honestly():
    units = {"Дом": cells({"2026-08-18": ("free", 10, 5)})}
    text = tg_digest.digest(TARGET, obj(units), obj(units), MONTHS, TODAY,
                            gap_days=None, taken=date(2026, 8, 14))
    assert "С прошлого снимка календарь не двигался" in text


def test_lost_data_is_dated_by_its_snapshot_too():
    text = tg_digest.digest(TARGET, obj({}, status="insufficient_data",
                                        reason="403"), None, MONTHS, TODAY,
                            taken=date(2026, 8, 14))
    assert "снимок 14.08" in text and "403" in text


def test_day_word_agrees_with_the_numeral():
    units = {"Дом": cells({"2026-08-18": ("free", 10, 5)})}
    for gap, word in ((1, "За сутки"), (2, "За 2 дня"), (5, "За 5 дней"),
                      (21, "За 21 день")):
        text = tg_digest.digest(TARGET, obj(units), obj(units), MONTHS, TODAY,
                                gap_days=gap)
        assert word in text, gap
