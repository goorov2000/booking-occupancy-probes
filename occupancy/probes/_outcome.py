# -*- coding: utf-8 -*-
"""Исход пробника: что ответ хоста делает с РЕЦЕПТОМ, а не с транспортом.

Транспорт (probes/_common.py) знает, как сходить на хост и во что превратить
код ответа. Здесь живёт следующий вопрос — судьба рецепта, — и два правила,
которые он обязан соблюдать. Оба стоят живых рецептов, поэтому вынесены в
одно место, а не повторены в шести модулях движков.

1. АВАРИЯ ЧУЖОГО ХОСТЕРА РЕЦЕПТ НЕ ЛОМАЕТ. 502/503 от nginx или балансировщика
   почти никогда не приходят JSON-ом: это HTML-страница, то есть «тело не той
   формы». Общий каскад читает негодное тело как смену схемы раньше, чем
   доходит до кода 5xx, — и пятиминутная авария хостера помечает broken все
   цели этого хоста разом (у нас 8 живых целей на ru-ibe.tlintegration.ru и 6
   на public-api.reservationsteps.ru). Рецепт, помеченный broken, в следующие
   прогоны не идёт НИКОГДА, пока агент не переразведает объект руками, —
   поэтому 5xx с любым телом здесь становится сетевым сбоем: рецепт жив,
   нужен повтор прогона.

2. ЧАСТИЧНЫЙ СЪЁМ С ДАННЫМИ — ЭТО УСПЕХ, А НЕ ОТКАЗ. Поле refusal объекта
   заводит счётчик суток подряд (occupancy_core.note_refusal), и три отказа
   подряд ломают рецепт. Но 403/429 на ХВОСТЕ объекта, у которого сетка уже
   снята, — обычная ситуация при 49 запросах на цель: съём состоялся, просто
   не до конца. Тикет 03 говорит дословно «успешный съём обнуляет счётчик»,
   поэтому при снятых клетках отказ уходит в причину объекта (status partial)
   и НЕ становится полем refusal.

3. ПРОСЬБА ПОДОЖДАТЬ — НЕ ОТКАЗ ЦЕЛИ, К КОТОРОЙ МЫ НЕ ХОДИЛИ. 429 с длинным
   Retry-After транспорт запоминает на ХОСТ (probes/_common._host_not_before),
   и следующая цель того же хоста получает AccessRefused ещё до отправки
   первого запроса. На public-api.reservationsteps.ru после разведки 04.09
   висят 18 самарских целей плюс 4 подмосковных: один 429 заводил счётчик
   суток (occupancy_core.note_refusal) сразу 22 живым рецептам, а три таких
   дня подряд — это broken и ручная переразведка каждого. Признак машинный:
   у отказа НЕТ кода HTTP. Ответ хоста без кода не приходит никогда (403,
   429 и страница-заслон дают refusal_status), поэтому refusal_status=None
   значит ровно одно — по ЭТОЙ цели ответа не было, про её рецепт мы не
   узнали ничего. Такой снимок честно insufficient_data с причиной в reason,
   но поля refusal у него нет: счётчик суток не заводится и не обнуляется.

Все три поправки идемпотентны: если общий каскад начнёт решать так же сам,
обёртки станут пустыми, а не задвоят решение.
"""
from __future__ import annotations

from typing import Optional

from ._common import ResponseVerdict, _finish as _common_finish
from ._common import classify_response as _common_classify
from ._common import one_line

# Состояния клетки, по которым объект считается ЧТО-ТО снявшим. Копия
# предиката из _common._finish осознанная и минимальная: правило «успешный
# съём обнуляет счётчик» решается ДО того, как объект получит статус.
KNOWN_STATES = ("free", "busy", "sales_not_open")


def has_known_cells(obj: dict) -> bool:
    """Есть ли в объекте хоть одна клетка, про которую движок сказал правду."""
    return any(cell.get("state") in KNOWN_STATES
               for cells in (obj.get("units") or {}).values()
               for cell in cells.values())


def classify_response(status: int, body_ok: bool, what: str,
                      **kwargs) -> ResponseVerdict:
    """Каскад транспорта плюс правило 1: 5xx рецепт не ломает никогда.

    Всё остальное — дословно общий каскад: 200 с годным телом -> ok,
    403/429/заслон -> refused, прочие 4xx и негодное тело -> broken.
    """
    verdict = _common_classify(status, body_ok, what, **kwargs)
    if verdict.kind == "broken" and status >= 500:
        return ResponseVerdict("network", verdict.reason, verdict.retry_after)
    return verdict


def finish(obj: dict, failures: list, broken: Optional[str], *,
           refused: Optional[str] = None,
           refusal_status: Optional[int] = None):
    """Сборка объекта плюс правила 2 и 3: не всякий отказ — отказ рецепта.

    Правило 2 — отказ поверх снятой сетки; правило 3 — отказ без кода HTTP
    (хост попросил подождать, и запрос по этой цели не ушёл вовсе). В обоих
    случаях причина не теряется: она уходит в reason объекта пометкой, и
    человек видит её в колонке «Источник» сводки, — но поля refusal нет, и
    счётчик суток по рецепту не двигается.
    """
    if refused and has_known_cells(obj):
        note = f"снято не до конца: {one_line(refused)}"
        return _common_finish(obj, list(failures) + [note], broken)
    if refused and refusal_status is None:
        note = (f"цель пропущена, запрос не отправлен: {one_line(refused)}")
        return _common_finish(obj, list(failures) + [note], broken)
    return _common_finish(obj, failures, broken, refused=refused,
                          refusal_status=refusal_status)
