# -*- coding: utf-8 -*-
"""Пробник Суточно.ру: календарь занятости ОБЪЯВЛЕНИЯ агрегатора -> сетка по юнитам.

Суточно.ру — не модуль бронирования объекта, а агрегатор: объект отдаёт ему
часть фонда (частник — как правило, всё жильё целиком, отель — квоту), и
занятость в этом канале равна загрузке объекта только когда объект продаётся
ТОЛЬКО здесь. Поэтому снимок всегда несёт source_kind "aggregator_quota" —
сводка никогда не смешивает его с модульными цифрами без пометки (SKILL.md
«Объект без модуля → агрегаторы»). Это первый агрегатор с пробником: у
Островка и Яндекса календарь живёт за SPA/капчей, а у Суточно есть JSON-API
с анонимным ключом приложения, который отдаёт календарь одним GET.

Метод (разведка хаусботов 21.08.2026: docs/research/2026-08-20-houseboat-market/
occupancy-raw/sutochno-mopup.json и aggr-2.json; перепроверен живьём 08.09.2026
на шести объявлениях — фикстуры tests/fixtures/sutochno/):

    GET https://sutochno.ru/api/json/orders/getOrdersByObject?object_id=<id>
        заголовки: api-version: 1.8, platform: js, token: <ключ приложения>
        -> {"success": true,
            "data": {"calendars": [
                {"date_begin": "2026-09-14 14:00:00",
                 "date_end": "2026-09-18 12:00:00",
                 "type": "cs-import", "id": 596689511, "object_id": 2224174},
                ...]},
            "errors": [], "actions": []}

Дат в запросе НЕТ: ответ — все БУДУЩИЕ блоки календаря объявления (блок,
чей конец прошёл, из выдачи исчезает сам), так что глубина бесплатна: один
GET на объявление на любой горизонт. Ночь d занята блоком, когда
date_begin.date <= d < date_end.date: заезд 14:00–15:00, выезд 12:00, и блок
«07.09 14:00 -> 08.09 12:00» — это ровно одна ночь 07.09.

ТОКЕН. Не секрет пользователя, а ключ КЛИЕНТСКОГО ПРИЛОЖЕНИЯ: лежит открытым
текстом в бандле сайта, один на всех анонимных посетителей, и 08.09.2026
он тот же, что 21.08. В витрине DEFAULT_TOKEN — плейсхолдер (см. комментарий
у константы). Где ключ лежит на сайте (оба места сняты 08.09):
- legacy-скрипт страниц объявлений https://sutochno.ru/doc/js/asset.index.js
  — `wp.settings={apiToken:"…"}`; это транспорт страниц
  <город>.sutochno.ru/<id>, и календарь там зовётся тем же getOrdersByObject
  (url_reserved_list в 11hw.js);
- Nuxt-бандл главной https://sutochno.ru/: <script type="module"
  src="https://cdn.sutochno.ru/static-pages/_nuxt/<entry>.js"> -> карта
  ключей по платформам `{…, defaultWhiteLabel:"…", default:"…"}`; axios там
  создаётся с headers {"api-version":"1.7", platform:"js", token: default}.
Чужой или протухший ключ API отвечает HTTP 403 с телом
{"success":false,"errors":["Application authentication failed"],
 "actions":{"need_update_token":true}} (снято 08.09 без токена и с мусорным)
— это НЕ заслон WAF, а просьба обновить ключ приложения. Пробник отличает её
по need_update_token: один раз за съём достаёт ключ из бандла (сначала
legacy-скрипт — один GET по постоянному адресу, потом цепочка главная ->
entry-чанк) и повторяет запрос ТОГО ЖЕ объекта с новым ключом — один раз.
Свежий ключ живёт в памяти процесса (остальные цели Суточно этого прогона
идут с ним без второй ходки в бандл) и пишется в объект снимка полем
token_refresh, а в reason — просьба обновить params.token. Рецепт пробник
НЕ правит: CLI сохраняет реестр по своим правилам (_account_recipe), а
смена request двигает recipe_hash ряда. Бандл без ключа — broken: схема
сменилась, нужна переразведка. 403 БЕЗ need_update_token — обычный отказ
хоста (refused, счётчик суток в CLI).

ТИПЫ БЛОКОВ и их прочтение — все значат «канал не даёт забронировать»:
- cs-book   — бронь через площадку;
- cs-appr   — подтверждённая/закрытая владельцем дата (Углич 21.08: и одна
              ночь 04.09, и блок 19.10.2026–30.04.2027 — оба этим типом);
- cs-import — импорт чужого календаря (канал-менеджер, другая площадка;
              Царевщина: все блоки такие);
- cs-navail — «недоступно», закрыто владельцем (три хаусбота Горы).
Бронь и закрытие изнутри ответа не различить — всё идёт в busy (оценка
СВЕРХУ, та же, что у модульных движков), а СОСТАВ по типам называется в
reason объекта и в клетке (поле block). Незнакомый тип — unknown, с
пометкой в reason: его смысл не доказывает занятость.

Одно исключение, названное вслух: блок длиной >= LONG_BLOCK_MIN_NIGHTS ночей
НЕ типа cs-book читается как закрытые продажи (sales_not_open), а не как
занятость. Живые примеры: Углич cs-appr 19.10.2026–30.04.2027 (193 ночи),
три хаусбота Горы cs-navail 30.12.2026–06.09.2027 (250 ночей). Это сезонные
закрытия, и 100% занятости по ним — выдуманный пик сезона (та же логика,
что у правила «стены» диспетчера: SALES_WALL_MIN_NIGHTS = 30). Правило
диспетчера ловит только ХВОСТ горизонта, а блок Углича стоит посреди него.
Порог переопределяется рецептом (params.long_block_nights; 0 — выключить).
cs-book любой длины остаётся бронью: это запись самой площадки о заказе.

ПУСТОЙ КАЛЕНДАРЬ — не факт «свободно везде»: на НЕСУЩЕСТВУЮЩИЙ object_id API
отвечает так же (08.09, id 999999999: HTTP 200, calendars []). Поэтому при
пустом календаре пробник один раз спрашивает страницу объявления
GET https://sutochno.ru/<id> (живое — 301 на <город>.sutochno.ru/…, снятое —
404 «Страница не найдена»; проверено 08.09 на 1746701, 2192982 и 999999999):
404 — клетки unknown и insufficient_data с причиной; живое — free с пометкой
«свободно везде ИЛИ владелец не ведёт календарь площадки» (Спароход: ноль
блоков 21.08 и 08.09 при живом объявлении — различить нельзя).

ЕДИНИЦА СЕТКИ — объявление (object_id) из params.objects; у отеля на Суточно
каждый тип номера — своё объявление (Гора: hotels/169448 -> хаусботы
2192982/2193366/2193368). Фонд: пара units_total/units_free пишется ТОЛЬКО
при rooms_count=1 в справочнике (частное объявление = одно жильё; у типа
отеля фонд называет разведчик); без rooms_count клетка идёт без пары и
объект считается по типам с пометкой — как у bronirui (ревью волны 4: фонд
не выдумывается). Цен API не отдаёт: соседний objects/getPricesAndAvailabilities
требует dateBegin/dateEnd/guests по каждому объекту (два пробных вызова 08.09
упёрлись в валидацию аргументов) и в v1 не зовётся — в клетке цены нет.

Правила ошибок — единый каскад (транспорт probes/_common.py + правила исхода
probes/_outcome.py): смена схемы, success=false с текстом ошибки и 4xx кроме
403/429 -> broken; 403/429/заслон -> refused (рецепт жив); сеть и 5xx ->
unknown-клетки, рецепт жив. Один запрос — одна попытка (единственный повтор —
тот же объект с ОБНОВЛЁННЫМ ключом приложения), пауза >= 1.2 с к хосту
(общий трекер _common), ничего не бронируется: эндпоинты заказа
(order/checkAvailability, orders/updateOrderDraft и прочие order*) не
зовутся никогда.
"""
from __future__ import annotations

import os
import re
import threading
from collections import namedtuple
from datetime import date, timedelta
from typing import Callable, Optional

from ._common import (AccessRefused, SchemaChanged,
                      _base_object as _common_base, _iso_dates, _now_iso,
                      deep_date_to, make_fetch as _make_fetch, one_line)
# Каскад и сборка объекта идут через правила ИСХОДА (probes/_outcome.py):
# 5xx рецепт не ломает никогда, а отказ хоста поверх уже снятой сетки не
# заводит счётчик суток. Транспорт про судьбу рецепта не знает.
from ._outcome import classify_response, finish as _finish

API_URL = ("https://sutochno.ru/api/json/orders/getOrdersByObject"
           "?object_id={object_id}")
# Версия API из живого метода 21.08 (Nuxt-бандл 08.09 шлёт 1.7 — обе живы).
API_VERSION = "1.8"
PLATFORM = "js"
# Анонимный ключ приложения: бандл sutochno.ru, 21.08 и 08.09.2026. Только
# умолчание для рецепта без params.token; протухнет — пробник возьмёт свежий
# из бандла сам (refresh_token).
# Витрина: значение ключа НЕ переносится. Это анонимный ключ клиентского
# приложения сайта (лежит открытым текстом в публичном бандле, см. докстринг),
# но в репозиторий кладём плейсхолдер той же формы: боевой ключ задаётся
# переменной окружения SUTOCHNO_APP_TOKEN или params.token рецепта, а при
# отказе need_update_token пробник сам достаёт свежий ключ из бандла.
DEFAULT_TOKEN = "SutochnoAppKeyPlaceholder0000000"
TOKEN_ENV = "SUTOCHNO_APP_TOKEN"
# Откуда пробник берёт свежий ключ (по порядку): постоянный адрес legacy-скрипта
# страниц объявлений — один GET; затем главная -> entry-чанк Nuxt (два GET).
TOKEN_SOURCE_LEGACY = "https://sutochno.ru/doc/js/asset.index.js"
TOKEN_SOURCE_NUXT = "https://sutochno.ru/"
# Страница объявления без города: 301 на <город>.sutochno.ru/… у живого,
# 404 у снятого — этим проверяется пустой календарь.
LISTING_URL = "https://sutochno.ru/{object_id}"

# Блок не короче стольких ночей и не типа cs-book — закрытые продажи, а не
# занятость (см. докстринг). Совпадает с SALES_WALL_MIN_NIGHTS диспетчера.
LONG_BLOCK_MIN_NIGHTS = 30
BOOKING_TYPE = "cs-book"
# Известные типы блоков (21.08 и 08.09) — для человеческой расшифровки.
BLOCK_TYPES = {
    "cs-book": "бронь площадки",
    "cs-appr": "подтверждено/закрыто владельцем",
    "cs-import": "импорт чужого календаря",
    "cs-navail": "закрыто владельцем (недоступно)",
}

_BROKEN_HINT = "похоже на смену API Суточно.ру или антибот"

# Версия РАЗБОРА движка (поле probe_version снапшота, тикет 10): поднимать,
# когда меняется смысл клетки (типы блоков, порог длинного блока, фонд).
PROBE_VERSION = 2

# Ключ приложения в бандле. Legacy: wp.settings={apiToken:"…"}. Nuxt: карта
# ключей по платформам, где ключ сайта стоит сразу после defaultWhiteLabel —
# голое `default:"…"` встречается в чанке сотни раз, якорь нужен.
_RE_TOKEN_LEGACY = re.compile(
    r'apiToken\s*:\s*["\']([A-Za-z0-9+/=_\-]{16,})["\']')
_RE_TOKEN_NUXT = re.compile(
    r'defaultWhiteLabel\s*:\s*["\'][^"\']*["\']\s*,\s*default\s*:\s*'
    r'["\']([A-Za-z0-9+/=_\-]{16,})["\']')
_RE_NUXT_ENTRY = re.compile(
    r'<script[^>]+src=["\'](https://cdn\.sutochno\.ru/static-pages/_nuxt/'
    r'[^"\']+\.js)["\']', re.IGNORECASE)
_TEXT_HEADERS = {"Accept": "*/*"}

# Свежий ключ, добытый из бандла в ЭТОМ процессе: {"token", "source"}. Общий
# на все цели Суточно прогона (они идут одной хост-очередью, но замок всё
# равно стоит — цена нулевая, а гонка при смене конфигурации воркеров дорога).
_token_cache: dict = {}
_token_lock = threading.Lock()

# Итог ходки за ключом: token — найденный ключ (None — не найден), urls —
# что смотрели (evidence), problem — почему не нашли, network — сбой сети/
# отказ хоста (рецепт жив), а не «в бандле ключа нет» (broken).
TokenRefresh = namedtuple("TokenRefresh", "token source urls problem network")


SOURCE_KIND = "aggregator_quota"


def _base_object(username: str, recipe: dict) -> dict:
    obj = _common_base(username, recipe, "per_unit", default_engine="sutochno")
    # Агрегатор: цифры — квота канала, не полная загрузка (SKILL.md).
    obj["source_kind"] = SOURCE_KIND
    return obj


def _body_text(fetch) -> str:
    """Тело последнего ответа fetch — им опознаётся страница-заслон.

    Импорт ленивый: пакет probes грузит этот модуль сам, и импорт на уровне
    модуля замкнул бы круг. Реализация одна на все движки — probes.body_text_of.
    """
    from . import body_text_of
    return body_text_of(fetch)


def api_headers(token: str, extra: Optional[dict] = None,
                api_version: str = API_VERSION) -> dict:
    """Заголовки вызова API: рецептные (Referer) плюс три обязательных.

    Ключ приложения берётся из params.token/кэша, а не из headers рецепта:
    место у него одно, иначе протухший ключ в headers молча перекрывал бы
    свежий.
    """
    headers = dict(extra or {})
    headers.update({"api-version": str(api_version), "platform": PLATFORM,
                    "token": token})
    return headers


def object_url(template: str, object_id: str) -> str:
    """URL календаря объявления по url_template рецепта."""
    template = template or API_URL
    if "{object_id}" in template:
        return template.replace("{object_id}", str(object_id))
    sep = "&" if "?" in template else "?"
    return f"{template}{sep}object_id={object_id}"


def directory(objects: dict, params: Optional[dict] = None) -> tuple:
    """params.objects -> ({object_id: имя}, {object_id: фонд}, [id без фонда]).

    Две формы записи, как у bronirui (там же — почему фонд не выдумывается):
      "2224174": "Плавдом на озере"                       — фонд неизвестен;
      "2224174": {"name": ..., "rooms_count": 1}          — фонд назван.
    Отдельная карта params.rooms_count тоже читается. Фондом клетки становится
    ТОЛЬКО единица: объявление с одним жильём отвечает бинарно, и это и есть
    его остаток; фонд больше единицы уходит в пометку, не в клетку.
    Дубли имён различаются object_id — ключ сетки обязан быть уникальным.
    """
    extra = (params or {}).get("rooms_count") or {}
    names: dict[str, str] = {}
    capacity: dict[str, int] = {}
    unknown: list[str] = []
    for object_id, value in objects.items():
        key = str(object_id)
        if isinstance(value, dict):
            names[key] = str(value.get("name") or value.get("title")
                             or f"Объявление {key}")
            rooms = value.get("rooms_count")
        else:
            names[key] = str(value)
            rooms = None
        if rooms is None:
            rooms = extra.get(key, extra.get(object_id))
        if isinstance(rooms, int) and not isinstance(rooms, bool) and rooms >= 1:
            capacity[key] = rooms
        else:
            unknown.append(key)
    counts: dict[str, int] = {}
    for name in names.values():
        counts[name] = counts.get(name, 0) + 1
    unique = {key: (name if counts[name] == 1 else f"{name} [{key}]")
              for key, name in names.items()}
    return unique, capacity, unknown


# ---------------------------------------------------------------------------
# Ключ приложения: из бандла, один раз за процесс
# ---------------------------------------------------------------------------

def extract_token(text: str) -> Optional[str]:
    """Ключ приложения из текста бандла (legacy или Nuxt) или None."""
    if not isinstance(text, str) or not text:
        return None
    for pattern in (_RE_TOKEN_LEGACY, _RE_TOKEN_NUXT):
        m = pattern.search(text)
        if m:
            return m.group(1)
    return None


def stale_token(status: int, data) -> bool:
    """Ответ «ключ приложения не принят» — а не заслон и не смена схемы.

    Признак машинный и живой (08.09): HTTP 403 + actions.need_update_token
    (или текст Application authentication failed в errors).
    """
    if status != 403 or not isinstance(data, dict):
        return False
    actions = data.get("actions")
    if isinstance(actions, dict) and actions.get("need_update_token"):
        return True
    errors = data.get("errors")
    return isinstance(errors, list) and any(
        "authentication failed" in str(e).lower() for e in errors)


def _errors_text(data) -> str:
    """Текст errors из ответа API — в detail вердикта."""
    if isinstance(data, dict) and isinstance(data.get("errors"), list) \
            and data["errors"]:
        return one_line("; ".join(str(e) for e in data["errors"]), 200)
    return ""


def refresh_token(text_fetch: Optional[Callable]) -> TokenRefresh:
    """Свежий ключ приложения из бандла sutochno.ru. Без ретраев.

    Порядок: legacy-скрипт (постоянный адрес, один GET); не дал — главная
    страница -> entry-чанк Nuxt. Каждый адрес спрашивается один раз. Сеть/
    отказ хоста -> network=True (рецепт жив, нужен повтор прогона); бандл
    снят, а ключа в нём нет -> network=False (схема сменилась).
    """
    if text_fetch is None:
        return TokenRefresh(None, None, [],
                            "нет текстового транспорта для бандла", True)
    urls: list[str] = []
    problems: list[str] = []
    network = False

    def get(url: str, what: str):
        nonlocal network
        urls.append(url)
        try:
            status, body = text_fetch(url, _TEXT_HEADERS)
        except AccessRefused as e:
            network = True
            problems.append(f"{what}: нас не пустили ({e.reason})")
            return None
        except OSError as e:
            network = True
            problems.append(f"{what}: сетевой сбой ({e})")
            return None
        if status != 200 or not isinstance(body, str) or not body:
            problems.append(f"{what}: HTTP {status}")
            return None
        return body

    body = get(TOKEN_SOURCE_LEGACY, "legacy-скрипт asset.index.js")
    token = extract_token(body) if body else None
    if token:
        return TokenRefresh(token, TOKEN_SOURCE_LEGACY, urls, None, False)
    if body:
        problems.append("legacy-скрипт снят, apiToken в нём не найден")
    page = get(TOKEN_SOURCE_NUXT, "главная sutochno.ru")
    if page:
        m = _RE_NUXT_ENTRY.search(page)
        if m is None:
            problems.append("на главной нет тега entry-чанка Nuxt")
        else:
            chunk = get(m.group(1), "entry-чанк Nuxt")
            token = extract_token(chunk) if chunk else None
            if token:
                return TokenRefresh(token, m.group(1), urls, None, False)
            if chunk:
                problems.append("entry-чанк снят, карты ключей в нём нет")
    return TokenRefresh(None, None, urls, "; ".join(problems), network)


def _current_token(params: dict) -> tuple[str, str]:
    """(ключ, откуда): кэш процесса > params.token рецепта > env > умолчание."""
    with _token_lock:
        cached = _token_cache.get("token")
    if cached:
        return cached, "cache"
    recipe_token = params.get("token")
    if isinstance(recipe_token, str) and recipe_token.strip():
        return recipe_token.strip(), "recipe"
    env_token = os.environ.get(TOKEN_ENV, "").strip()
    if env_token:
        return env_token, "env"
    return DEFAULT_TOKEN, "default"


def _refreshed_token(text_fetch: Optional[Callable],
                     stale: str) -> TokenRefresh:
    """Ключ на замену протухшему: из кэша процесса, иначе из бандла."""
    with _token_lock:
        cached = _token_cache.get("token")
        source = _token_cache.get("source")
    if cached and cached != stale:
        return TokenRefresh(cached, source, [], None, False)
    fresh = refresh_token(text_fetch)
    if fresh.token:
        with _token_lock:
            _token_cache["token"] = fresh.token
            _token_cache["source"] = fresh.source
    return fresh


# ---------------------------------------------------------------------------
# Разбор календаря
# ---------------------------------------------------------------------------

def _day(value) -> Optional[date]:
    """'YYYY-MM-DD HH:MM:SS' (или 'YYYY-MM-DD') -> date; иначе None."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def parse_blocks(payload: object) -> list[dict]:
    """Ответ getOrdersByObject -> [{"begin", "end", "type", "nights"}].

    Не та форма -> SchemaChanged. success=false при HTTP 200 — тоже: API
    объяснил отказ словами (валидация, смена контракта), это работа
    разведчика, а не сети.
    """
    if not isinstance(payload, dict):
        raise SchemaChanged(
            "ответ не объект JSON — схема getOrdersByObject сменилась")
    if payload.get("success") is False:
        raise SchemaChanged(
            "API ответил success=false"
            + (f": {_errors_text(payload)}" if _errors_text(payload) else ""))
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("calendars"), list):
        raise SchemaChanged(
            "в ответе нет списка data.calendars — схема сменилась")
    blocks: list[dict] = []
    for item in data["calendars"]:
        if not isinstance(item, dict):
            raise SchemaChanged("в data.calendars не-объект — схема сменилась")
        begin, end = _day(item.get("date_begin")), _day(item.get("date_end"))
        if begin is None or end is None:
            raise SchemaChanged(
                "блок календаря без date_begin/date_end вида "
                "'YYYY-MM-DD HH:MM:SS' — схема сменилась")
        blocks.append({"begin": begin, "end": end,
                       "type": str(item.get("type") or "?"),
                       "nights": (end - begin).days})
    return blocks


def build_cells(blocks: list[dict], dates: list[str],
                rooms_count: Optional[int] = None,
                long_min: int = LONG_BLOCK_MIN_NIGHTS) -> tuple[dict, dict]:
    """Блоки -> клетки {дата: {...}} плюс статистика для reason.

    Ночь под коротким блоком или под cs-book любой длины -> busy (поле block
    несёт тип; несколько типов — через «+»). Ночь только под длинным блоком
    не-cs-book -> sales_not_open с rule="long_block". Свободная -> free.
    Пара units_total/units_free — только при rooms_count=1 (см. directory).
    """
    long_min = long_min if isinstance(long_min, int) and long_min > 0 else 0
    tagged = [dict(b, long=bool(long_min) and b["nights"] >= long_min
                   and b["type"] in BLOCK_TYPES and b["type"] != BOOKING_TYPE)
              for b in blocks if b["nights"] > 0]
    one_home = rooms_count == 1
    cells: dict[str, dict] = {}
    busy_by_type: dict[str, int] = {}
    closed = 0
    for d in dates:
        day = date.fromisoformat(d)
        covering = [b for b in tagged if b["begin"] <= day < b["end"]]
        hard = [b for b in covering if not b["long"] and b["type"] in BLOCK_TYPES]
        unknown = [b for b in covering if b["type"] not in BLOCK_TYPES]
        if hard:
            types = sorted({b["type"] for b in hard})
            cell = {"state": "busy", "block": "+".join(types)}
            if one_home:
                cell.update(units_total=1, units_free=0)
            for t in types:
                busy_by_type[t] = busy_by_type.get(t, 0) + 1
        elif unknown:
            cell = {"state": "unknown", "block": "+".join(sorted({b["type"] for b in unknown}))}
        elif covering:
            types = sorted({b["type"] for b in covering})
            cell = {"state": "sales_not_open", "block": "+".join(types),
                    "rule": "long_block"}
            if one_home:
                cell.update(units_total=1, units_free=0)
            closed += 1
        else:
            cell = {"state": "free"}
            if one_home:
                cell.update(units_total=1, units_free=1)
        cells[d] = cell
    stats = {"busy_by_type": busy_by_type,
             "long_blocks": [b for b in tagged if b["long"]],
             "closed_nights": closed}
    return cells, stats


# ---------------------------------------------------------------------------
# Пустой календарь: живо ли объявление
# ---------------------------------------------------------------------------

def listing_alive(text_fetch: Optional[Callable],
                  object_id: str) -> tuple[Optional[bool], str, str]:
    """-> (живо | None, url, пометка). None — не проверилось (сеть/нет транспорта).

    Живое объявление: 301 -> страница с «объявление <id>» в теле (транспорт
    идёт по редиректу и отдаёт 200); снятое — 404. Id в теле проверяется,
    чтобы 200 чужой страницы (редирект на город) не сошёл за живое.
    """
    url = LISTING_URL.format(object_id=object_id)
    if text_fetch is None:
        return None, url, "существование объявления не проверялось (нет " \
                          "текстового транспорта)"
    try:
        status, body = text_fetch(url, _TEXT_HEADERS)
    except AccessRefused as e:
        return None, url, f"страница объявления: нас не пустили ({e.reason})"
    except OSError as e:
        return None, url, f"страница объявления: сетевой сбой ({e})"
    if status == 404:
        return False, url, "HTTP 404"
    if status == 200 and isinstance(body, str) and str(object_id) in body:
        return True, url, ""
    return None, url, f"страница объявления: HTTP {status}" + (
        "" if status != 200 else " без номера объявления в теле")


# ---------------------------------------------------------------------------
# Вход пробника
# ---------------------------------------------------------------------------

def _unknown(dates: list[str]) -> dict:
    return {d: {"state": "unknown"} for d in dates}


def probe(username: str, recipe: dict, date_from: date, date_to: date,
          fetch: Optional[Callable] = None, inventory: bool = True,
          inventory_date_to: Optional[date] = None,
          text_fetch: Optional[Callable] = None):
    """Снять календари объявлений по рецепту. -> (obj, broken_reason | None).

    inventory и inventory_date_to приняты для единообразия диспетчера и
    запросов не добавляют: фонд объявления приходит справочником рецепта, а
    не запросом на ночь, поэтому известен на всю сетку (inventory_until = её
    конец). text_fetch — текстовый транспорт для бандла (ключ) и страницы
    объявления (пустой календарь); в живом режиме создаётся сам, в тестах
    передаётся явно.
    """
    if fetch is None:
        fetch = _make_fetch("get_json")
        if text_fetch is None:
            text_fetch = _make_fetch("get_text")
    request = recipe.get("request", {})
    params = request.get("params", {}) or {}
    headers = request.get("headers", {}) or {}
    template = request.get("url_template", "") or API_URL
    api_version = str(params.get("api_version") or API_VERSION)
    long_min = params.get("long_block_nights", LONG_BLOCK_MIN_NIGHTS)
    objects = params.get("objects") or {}
    if not objects:
        obj = _base_object(username, recipe)
        obj["status"] = "insufficient_data"
        obj["reason"] = ("в рецепте нет params.objects — календарь Суточно "
                         "снимается по object_id объявления, нужна разведка")
        return obj, None

    names, capacity, without_capacity = directory(objects, params)
    token, token_origin = _current_token(params)
    deep_to = deep_date_to(date_to)
    dates = _iso_dates(date_from, deep_to)
    obj = _base_object(username, recipe)
    obj["inventory_until"] = deep_to.isoformat()
    obj["grid_until"] = deep_to.isoformat()
    failures: list[str] = []
    broken: Optional[str] = None
    refused: Optional[str] = None
    refusal_status: Optional[int] = None
    refreshed_here = False
    composition: list[str] = []
    long_notes: list[str] = []
    empty_notes: list[str] = []
    strange_types: set[str] = set()

    for object_id, unit in names.items():
        url = object_url(template, object_id)
        obj["source_urls"].append(url)
        try:
            status, data = fetch(url, api_headers(token, headers, api_version))
            if stale_token(status, data) and not refreshed_here:
                # Ключ приложения не принят — не заслон и не смена схемы.
                # Один раз за съём берём свежий из бандла и повторяем ТОТ ЖЕ
                # объект один раз. Второе протухание за съём — уже отказ.
                refreshed_here = True
                fresh = _refreshed_token(text_fetch, token)
                obj["source_urls"].extend(fresh.urls)
                if fresh.token and fresh.token != token:
                    obj["token_refresh"] = {
                        "stale_from": token_origin, "source": fresh.source,
                        "token": fresh.token, "at": _now_iso()}
                    token, token_origin = fresh.token, "bundle"
                    status, data = fetch(url, api_headers(token, headers,
                                                          api_version))
                elif fresh.token is None and fresh.network:
                    failures.append(
                        f"{unit}: ключ приложения протух, а бандл за свежим "
                        f"не снялся ({fresh.problem}) — нужен повтор прогона")
                    obj["units"][unit] = _unknown(dates)
                    continue
                elif fresh.token is None:
                    broken = (f"ключ приложения протух (403 need_update_token), "
                              f"а в бандле sutochno.ru ключа нет "
                              f"({fresh.problem}) — схема сменилась, нужна "
                              f"переразведка")
                    break
                # fresh.token == token: бандл отдаёт тот же ключ, дело не в
                # нём — 403 идёт по общему каскаду (refused).
        except AccessRefused as e:
            refused, refusal_status = e.reason, e.status
            obj["units"][unit] = _unknown(dates)
            break
        except OSError as e:
            failures.append(f"{unit}: сетевой сбой ({e})")
            obj["units"][unit] = _unknown(dates)
            continue
        verdict = classify_response(status, data is not None, unit,
                                    detail=_errors_text(data),
                                    hint=_BROKEN_HINT,
                                    body_text=_body_text(fetch))
        if verdict.kind == "refused":
            refused, refusal_status = verdict.reason, status
            obj["units"][unit] = _unknown(dates)
            break
        if verdict.kind == "broken":
            broken = verdict.reason
            break
        if verdict.kind == "network":
            failures.append(verdict.reason)
            obj["units"][unit] = _unknown(dates)
            continue
        try:
            blocks = parse_blocks(data)
        except SchemaChanged as e:
            broken = f"{unit}: {e}"
            break
        if not blocks:
            # Пусто и у снятого объявления — проверяем, живо ли оно.
            alive, page, note = listing_alive(text_fetch, object_id)
            obj["source_urls"].append(page)
            if alive is False:
                failures.append(
                    f"{unit}: объявление не найдено ({note} на {page}) — "
                    f"снято с публикации или удалено; календарь пуст потому, "
                    f"что объекта нет")
                obj["units"][unit] = _unknown(dates)
                continue
            empty_notes.append(
                f"{unit}: календарь пуст"
                + (" (объявление живо)" if alive else f" ({note})")
                + " — свободно везде или владелец не ведёт календарь "
                  "площадки, различить нельзя")
        cells, stats = build_cells(blocks, dates, capacity.get(object_id),
                                   long_min)
        obj["units"][unit] = cells
        if stats["busy_by_type"]:
            composition.append(
                f"{unit}: " + ", ".join(
                    f"{t}×{n}" for t, n in sorted(stats["busy_by_type"].items())))
        strange_types.update(b["type"] for b in blocks if b["type"] not in BLOCK_TYPES)
        for b in stats["long_blocks"]:
            long_notes.append(
                f"{unit} {b['type']} {b['begin'].isoformat()}.."
                f"{(b['end'] - timedelta(days=1)).isoformat()}"
                f" ({b['nights']} ноч.)")

    result = _finish(obj, failures, broken, refused=refused,
                     refusal_status=refusal_status)
    if broken:
        return result
    notes: list[str] = []
    if composition:
        notes.append("блоки Суточно (бронь, закрытие владельцем и импорт "
                     "чужого календаря неразличимы — все busy, оценка "
                     "сверху): " + "; ".join(composition))
    if strange_types:
        notes.append("незнакомый тип блока " + ", ".join(sorted(strange_types))
                     + " — unknown, смысл блока не подтверждён")
    if long_notes:
        notes.append(f"закрытые продажи (блок ≥{long_min} ноч. не cs-book "
                     f"размечен sales_not_open, не занятость): "
                     + "; ".join(long_notes))
    notes.extend(empty_notes)
    if obj.get("token_refresh"):
        notes.append(f"ключ приложения обновлён из бандла "
                     f"({obj['token_refresh']['source']}) — обновите "
                     f"params.token рецепта")
    for note in (_note_without_fund(without_capacity),
                 _note_multi_home(capacity, names)):
        if note:
            notes.append(note)
    if notes:
        result[0]["reason"] = "; ".join(
            x for x in [result[0].get("reason")] + notes if x)
    return result


def _shortlist(keys) -> str:
    keys = sorted(keys)
    head = ", ".join(keys[:5])
    return head + (f" и ещё {len(keys) - 5}" if len(keys) > 5 else "")


def _note_without_fund(without_capacity: list) -> str:
    """Объявления, про фонд которых справочник не сказал ничего."""
    if not without_capacity:
        return ""
    return (f"считано по объявлениям без фонда (rooms_count не задан в "
            f"рецепте): {_shortlist(without_capacity)}")


def _note_multi_home(capacity: dict, names: dict) -> str:
    """Объявления с несколькими домиками: поштучного остатка API не отдаёт."""
    multi = {key: fund for key, fund in capacity.items() if fund > 1}
    if not multi:
        return ""
    listed = ", ".join(f"{names.get(key, key)} [{key}] ×{multi[key]}"
                       for key in sorted(multi)[:5])
    tail = f" и ещё {len(multi) - 5}" if len(multi) > 5 else ""
    return (f"поштучного остатка Суточно не отдаёт: под объявлениями {listed}"
            f"{tail} несколько домиков, а календарь отвечает только «можно ли "
            f"забронировать» — эти клетки считаны по типу, занятость занижена")
