# -*- coding: utf-8 -*-
"""Пробник Bnovo: та же платформа, что вариант (1) reservationsteps в travelline.py.

Разведка 14.08.2026 (тикет 03, чистый HTTP без браузера): «модуль Bnovo» и
«вариант reservationsteps» — ОДИН API. Сайт Bnovo-объекта грузит
widget.reservationsteps.ru/iframe/library/dist/booking_iframe.js (конструктор
BookingIframe с uid объекта), iframe открывает
reservationsteps.ru/rooms/index/{uid}, страница и её конфигуратор
брендированы Bnovo (bnovo.js, ссылки на bnovo.ru), а данные виджет берёт с
public-api.reservationsteps.ru/v1/api/min_prices — ровно тот эндпоинт,
который волна 2 встречала у «travelline»-объектов yck_kuzminskoe и ok_reka.
То есть reservationsteps.ru — это движок Bnovo, а не TravelLine;
классификация волны 2 у той пары объектов просто не различала платформы.
Существующие рецепты не переименовываются: ключ реестра — username,
поле engine лишь выбирает пробник, и оба имени ведут в один код.

Поэтому кода съёма здесь НЕТ: probe — это travelline.probe, который по
url_template (reservationsteps) сам уходит в вариант (1). Семантика клеток
(null = занята/закрыта, оценка сверху; пустой min_prices = sales_not_open),
паузы >= 1.2 c, таймауты, правила broken — там же, в travelline.py.

Разведчику (тикет 05) для новых Bnovo-объектов:
- uid — в HTML сайта: конструктор BookingIframe({uid: "..."}), обычно
  на странице /booking;
- room_type_id категорий — GET reservationsteps.ru/rooms/index/{uid}
  ?lang=ru&dfrom=DD-MM-YYYY&dto=DD-MM-YYYY на заведомо СВОБОДНУЮ дату:
  в HTML фильтра карточек лежат пары data-category-id / data-name.
  Сервер рендерит только доступные на запрошенные даты категории, поэтому
  надёжен союз двух-трёх дат в разных месяцах (проверка 14.08: списки
  a_ureki на 22-23.09, 20-21.10 и 08-09.12 сошлись в один и тот же набор);
  нежилые категории («Подарочный сертификат») в рецепт не включать;
- то же самое отдаёт /v1/api/rooms?account_id=...&dfrom=...&dto=..., но ему
  нужен ещё account_id (hidden input формы листа ожидания на rooms/index —
  есть не у всех объектов), а min_prices рецепта живёт одним uid;
- глубина: API отдаёт весь запрошенный диапазон дат, но за границей окна
  продаж все ночи null — неотличимо от «занято». Горизонт сводки держать
  в окне продаж (14.08: живые тарифы a_ureki до 29.12.2026, chekhovapi до
  29.11.2026 — глубже «текущий+следующий месяц», который виджет показывал
  волне 2 в браузере).
"""
from __future__ import annotations

from . import travelline

# Тонкий алиас: один API — один пробник (см. докстринг).
probe = travelline.probe
# И версия разбора та же: снимок bnovo снят кодом travelline, и подписывать
# его отдельной версией значит соврать о происхождении (тикет 10).
PROBE_VERSION = travelline.PROBE_VERSION
