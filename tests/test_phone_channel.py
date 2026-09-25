# -*- coding: utf-8 -*-
"""Телефонный канал (phone_channel.py): лист прозвона и разбор ответов.

Контракт: ответ человека ложится в ряд тем же объектом SCHEMA (б), что и
машинный снимок, но с engine "phone" и честным reason; «частично» и
«не дозвонились» клеток не выдумывают.
"""
import csv
import json
from datetime import date

import phone_channel as pc


def test_windows_skip_the_weekend_that_already_started():
    # понедельник 07.09.2026 -> ближайшая пятница 11.09, ночи 11 и 12, следующие 18 и 19, будни вт 15.09
    w = pc.windows_for(date(2026, 9, 7))
    assert [d.isoformat() for d in w["w1"]] == ["2026-09-11", "2026-09-12"]
    assert [d.isoformat() for d in w["w2"]] == ["2026-09-18", "2026-09-19"]
    assert w["w3"][0].isoformat() == "2026-09-15"
    # суббота 12.09: спрашивать про идущие выходные бессмысленно — следующая пятница
    w = pc.windows_for(date(2026, 9, 12))
    assert w["w1"][0].isoformat() == "2026-09-18"
    # пятница 11.09: ночь уже сегодня — тоже следующая пятница
    w = pc.windows_for(date(2026, 9, 11))
    assert w["w1"][0].isoformat() == "2026-09-18"


def test_slug_is_stable_and_names_unnamed_boats_by_owner():
    assert pc.slug("Manhattan (Манхэттен) — чёрный катамаран") == "hb_manhattan"
    assert pc.slug("без имени #2 (AQUASTORIES, FreeDom 75)") == "hb_aquastories_2"
    assert pc.slug("Хаусбот «Мечта»") == "hb_hausbot_mechta"
    assert pc.slug("«Хаусбот 40» (отель «Гора» / «Бухта»)") == "hb_hausbot_40"


def _targets(tmp_path):
    p = tmp_path / "targets.json"
    json.dump({"selected": [
        {"unit_name": "Хаусбот «Мечта»", "phones": ["+7 000 000-00-00"], "region": "Волгоград", "site": "https://x"},
        {"unit_name": "Одиссей — белый VIP-катамаран", "phones": [], "region": "Самара"},
    ]}, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    return p


def test_sheet_has_one_row_per_boat_with_three_windows(tmp_path):
    targets = pc.load_targets(_targets(tmp_path))
    csv_path, xlsx, n = pc.build_sheet(targets, tmp_path / "calls", date(2026, 9, 7))
    assert n == 2 and csv_path.exists()
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    assert rows[0]["Ключ"] == "hb_hausbot_mechta"
    assert rows[0]["Ближайшие выходные: ночи"] == "2026-09-11, 2026-09-12"
    assert rows[0]["Будняя ночь: дата"] == "2026-09-15"
    assert "телефона на витринах нет" in rows[1]["Телефоны (витрина)"]
    assert "Мечта" in rows[0]["Сценарий вопроса"]


def test_ingest_maps_answers_to_cells_honestly(tmp_path):
    targets = pc.load_targets(_targets(tmp_path))
    csv_path, _, _ = pc.build_sheet(targets, tmp_path / "calls", date(2026, 9, 7))
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    rows[0].update({"Ближайшие выходные: ответ": "занято", "Следующие выходные: ответ": "свободно",
                    "Будняя ночь: ответ": "частично", "Цена ночи, названная (₽)": "25 000",
                    "Комментарий": "только от двух суток", "Дата звонка": "2026-09-08", "Кто звонил": "оператор"})
    rows[1].update({"Ближайшие выходные: ответ": "не дозвонились"})
    objs = pc.fixtures_from_sheet(rows, targets, "2026-09-08")
    a, b = objs
    assert a["username"] == "hb_hausbot_mechta" and a["engine"] == "phone" and a["source_kind"] == "none"
    cells = a["units"]["__aggregate__"]
    assert cells["2026-09-11"] == {"state": "busy", "units_total": 1, "units_free": 0}
    assert cells["2026-09-18"] == {"state": "free", "units_total": 1, "units_free": 1, "price": 25000}
    # «частично» — неизвестно, без выдуманного фонда и цены
    assert cells["2026-09-15"] == {"state": "unknown"}
    assert a["status"] == "ok"
    assert "2026-09-08" in a["reason"] and "оператор" in a["reason"] and "только от двух суток" in a["reason"]
    # не дозвонились — объект без клеток и честный статус
    assert b["units"]["__aggregate__"] == {} and b["status"] == "insufficient_data"
    assert "не дозвонились" in b["reason"]


# --- ревью 14.09.2026: ручной ввод в Excel не портит ряд и не роняет снапшот ---

def test_price_takes_the_first_number_instead_of_gluing_all_digits():
    # Прежний разбор склеивал все цифры поля: «25 000 – 30 000» -> 2 500 030 000 ₽.
    assert pc._price("25 000 – 30 000") == 25000
    assert pc._price("4 500 ₽ за ночь, мин. 2 ночи") == 4500
    assert pc._price("25000.0") == 25000
    assert pc._price("25 000") == 25000
    assert pc._price("") is None
    assert pc._price("не сказали") is None


def test_nights_accept_excel_dates_and_russian_format_and_drop_garbage():
    from datetime import datetime
    assert pc._nights(datetime(2026, 9, 22)) == ["2026-09-22"]
    assert pc._nights(date(2026, 9, 22)) == ["2026-09-22"]
    assert pc._nights("22.09.2026, 2026-09-23") == ["2026-09-22", "2026-09-23"]
    assert pc._nights("в среду") == []
