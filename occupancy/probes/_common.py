# -*- coding: utf-8 -*-
"""Общий транспорт и хелперы пробников: паузы по хосту, каскад ошибок, база.

Железные правила (SKILL.md «Запреты»): один запрос — одна попытка (никаких
ретраев), щадящий таймаут, пауза >= 1.2 с между запросами к ОДНОМУ хосту.
Трекер последнего запроса по хосту живёт на уровне МОДУЛЯ, а не замыкания
fetch: до ревью 14.08 каждый make_fetch держал свой таймер, и два
TravelLine-объекта подряд били ibe.tlintegration.ru без паузы между собой —
пауза работала только внутри одного объекта.

Каскад ошибок (единый для всех пробников, см. докстринг probes/__init__):
4xx или негодное тело -> broken с broken_reason (рецепт на переразведку);
сетевой сбой и 5xx -> unknown-клетки/insufficient_data, рецепт жив.

Что изменилось 04.09.2026 (тикет 03 спеки поюнитной сезонности, транспорт):
- «нас не пустили» (403/429/челлендж) — ОТДЕЛЬНЫЙ исход, а не смена схемы:
  один 403 помечал рецепт broken навсегда, и при 53 целях ежедневного
  прогона временный WAF чужого хостера выбивал бы рецепты пачками. Класс
  AccessRefused и classify_response() дают каскаду третий исход; счётчик
  суток и решение «пора ломать» живут в occupancy_core.note_refusal.
- пауза получила джиттер: ровно 1.2 с — узнаваемый машинный ритм.
- Retry-After у 429 уважается: короткую просьбу подождать выдерживаем перед
  СЛЕДУЮЩИМ запросом к хосту (ретрая по-прежнему нет), длинную не ждём —
  честно пропускаем объект.
- тело ответа читается с потолком MAX_BODY_BYTES: до этого любой ответ
  вычитывался в память целиком, каким бы он ни пришёл.
- чужой текст ошибки экранируется (one_line): вертикальная черта из тела
  чужого ответа доезжала до markdown-таблицы сводки и разъезжала строку.
Волна 2 (тикет 05, 04.09.2026): цели разных хостов снимаются ПАРАЛЛЕЛЬНО,
поэтому очередь к хосту держит замок (host_turn), а не голый dict: два
потока читали «последний запрос был давно» одновременно и били чужой хост
залпом. Внутри одного хоста порядок и пауза остались прежними.
AccessRefused и ResponseTooLarge — подклассы OSError НАРОЧНО: пробники,
которые ещё не знают о новом каскаде, ловят OSError и деградируют в
partial/insufficient_data, а не роняют весь прогон.
Волна 3 (ревью wave2-probes, 04.09.2026):
- fetch помнит тело НЕудачного ответа (last_body_text). Без него ветка
  is_challenge каскада была недостижима у пяти движков из шести: заслон с
  кодом 200 и HTML-телом читался как смена схемы и слал живой рецепт на
  переразведку.
- отметка времени запроса вернулась в его КОНЕЦ: с потоковым чтением она
  уехала на заголовки, и пауза к хосту укоротилась на время чтения тела.
Волна 4 (ревью wave3-core, 04.09.2026):
- бюджет времени на объект живёт ЗДЕСЬ (request_budget): резать можно только
  там, где отправляется запрос. В cli он лишь мерил время постфактум, а
  начатая цель шла до конца — 117 запросов pineriver_hotel, начатые на 89-й
  минуте при дедлайне 90 мин, съедали весь хвост прогона.
- Retry-After последнего ответа доезжает до каскада без заголовков
  (last_retry_after): параметр headers у classify_response не передавал ни
  один из десяти вызовов в пробниках, и приписка «хост просит подождать N с»
  не появлялась никогда.
"""
from __future__ import annotations

import contextlib
import email.utils
import random
import re
import threading
import time
import urllib.parse
from collections import namedtuple
from datetime import date, datetime, timedelta
from typing import Callable, Optional

import occupancy_core as _core

TIMEOUT_SEC = 25
PAUSE_SEC = 1.2
# Джиттер к паузе: 0..0.6 с сверху. Меньше 1.2 с пауза не становится никогда.
PAUSE_JITTER_SEC = 0.6
# Дольше этого чужую просьбу подождать не ждём: прогон ограничен окном
# таймера, и полчаса сна одному хосту — это потерянный день для всех целей.
MAX_HOST_WAIT_SEC = 60
# Потолок тела ответа: 8 МБ. Календарь на год — сотни килобайт; всё, что
# больше, это не календарь, и вычитывать его в память незачем.
MAX_BODY_BYTES = 8 * 1024 * 1024
BODY_CHUNK_BYTES = 64 * 1024
# Сколько знаков тела НЕудачного ответа транспорт держит для опознания
# заслона. Ровно столько же читает is_challenge: держать больше незачем, а
# держать тело удачного ответа незачем вовсе — календарь на год это сотни
# килобайт на каждый живой fetch прогона.
BODY_SNIPPET_CHARS = 4096

# DEPRECATED (04.09.2026): календарная константа глубины. Осталась как нижняя
# граница совместимости; глубину считает deep_date_to() от СЕГОДНЯ — иначе с
# 01.11.2026 «глубокий» горизонт оказывался в прошлом.
DEEP_DATE_TO = date(2027, 3, 31)

DEFAULT_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
                   "Gecko/20100101 Firefox/128.0"),
    "Accept": "application/json, text/plain, */*",
}

# Версия транспорта и каскада. Пишется в снапшот (probe_version, тикет 10):
# когда через месяц окажется, что движок сменил смысл поля, «снято старым
# кодом» надо отличать от «снято новым». Модуль движка может объявить свою
# PROBE_VERSION — тогда в снапшот идёт она.
PROBE_VERSION = 1

# Маркеры страницы-заслона (Cloudflare, DDoS-Guard, «проверка браузера»).
# Их МЫ НЕ ОБХОДИМ: маркер нужен, чтобы честно сказать «нас не пустили»,
# а не чтобы притвориться браузером.
CHALLENGE_MARKERS = (
    "just a moment", "cf-chl", "cf-browser-verification", "attention required",
    "ddos-guard", "checking your browser", "проверка браузера", "captcha",
    "капча",
)


class SchemaChanged(Exception):
    """Ответ движка не той формы, что зафиксирована рецептом -> broken."""


class AccessRefused(OSError):
    """Нас не пустили: 403/429/страница-заслон. Рецепт НЕ ломается.

    Отдельно от SchemaChanged нарочно: смена схемы — это работа разведчика,
    а отказ хоста лечится временем. Подкласс OSError, чтобы старые пробники
    поймали его как сетевой сбой (объект partial, рецепт жив).
    """

    def __init__(self, reason: str, *, status: Optional[int] = None,
                 retry_after: Optional[float] = None):
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.retry_after = retry_after


class ResponseTooLarge(OSError):
    """Тело ответа больше MAX_BODY_BYTES — целиком не читаем, объект partial."""


class BudgetExceeded(OSError):
    """Бюджет времени на ОБЪЕКТ исчерпан — новых запросов не отправляем.

    Подкласс OSError (как AccessRefused и ResponseTooLarge), но НЕ подкласс
    AccessRefused нарочно: исчерпанный бюджет — наше решение, а не отказ
    хоста. Считай мы его отказом, счётчик суток (core.note_refusal) через
    трое суток сломал бы живой рецепт за нашу же медлительность.
    """


# (kind: "ok"|"refused"|"broken"|"network", reason, retry_after)
ResponseVerdict = namedtuple("ResponseVerdict", "kind reason retry_after")


def probe_version_of(engine: str, module=None) -> str:
    """Версия пробника для поля probe_version снапшота: '<движок>@<версия>'.

    Модуль движка объявляет свою PROBE_VERSION и поднимает её, когда меняется
    РАЗБОР ответа (а не оформление кода); без своей версии берётся общая
    версия транспорта. Ряд сезонности живёт годами, и «снято старым кодом»
    надо уметь отличить от «снято новым» — иначе очередная смена смысла поля
    у движка (так было с limited_inventory_count и с min_stay) разъедется по
    ряду молча.
    """
    version = getattr(module, "PROBE_VERSION", None) or PROBE_VERSION
    return f"{engine or '?'}@{version}"


def deep_date_to(date_to: date, today: Optional[date] = None) -> date:
    """Глубина запроса там, где углубление БЕСПЛАТНО по числу запросов.

    Правило ревью 14.08 сохранено дословно: спрашиваем настолько глубоко,
    насколько это не стоит лишних запросов, и сохраняем всё, что движок
    отдал; сводка по-прежнему показывает только свои месяцы. Изменилась
    только точка отсчёта: не константа 2027-03-31, а сегодня + год
    (core.grid_horizon) — константа протухала 01.11.2026.
    """
    return max(date_to, _core.grid_horizon(today))


# ---------------------------------------------------------------------------
# Пауза по хосту (модульный трекер — общий для всех fetch процесса)
# ---------------------------------------------------------------------------

_last_request_by_host: dict[str, float] = {}
# Момент, раньше которого хост просил не приходить (Retry-After у 429).
_host_not_before: dict[str, float] = {}

# Замок на КАЖДЫЙ хост (тикет 05, 04.09.2026). С этого дня цели разных хостов
# снимаются параллельно, и трекер выше — обычный dict — паузу бы не удержал:
# два потока одновременно читают «последний запрос был давно» и бьют чужой
# хост залпом. Замок держится всю дорогу запроса, а не только на время
# расчёта паузы: пауза меряется от КОНЦА предыдущего запроса, и отпусти мы
# замок раньше — сосед вклинился бы в середину.
_host_locks: dict[str, threading.Lock] = {}
_host_locks_guard = threading.Lock()

# Память ПОТОКА: бюджет времени на текущий объект и просьба подождать из
# последнего ответа. Оба принадлежат одному съёму, а съёмов в процессе
# столько же, сколько хостов в прогоне (по потоку на хост-очередь), — общий
# на процесс dict тут врал бы соседям.
_local = threading.local()


def host_of(url: str) -> str:
    """Хост URL: ключ паузы и ключ группировки целей в прогоне (тикет 05)."""
    return urllib.parse.urlsplit(url).netloc


# Прежнее внутреннее имя: его зовут функции этого модуля.
_host = host_of


def punycode_url(url: str) -> str:
    """URL с кириллическим хостом -> тот же URL с хостом в punycode (xn--…).

    Зачем в транспорте, а не только в разведчике: http.client кодирует строку
    запроса и заголовки latin-1, и кириллица в них роняет запрос ещё до сети
    UnicodeEncodeError'ом. Живой прогон 04.09 упал так на смоларелакс.рф
    (Referer), проверка 05.09 00:00 — на smr_mesto_schastya. Разведчик с 04.09
    пишет заголовки чистыми (scout.ascii_site), но реестр правят и руками:
    транспорт — последний рубеж, после которого ни один рецепт не уронит
    пробник. Порт, путь и запрос не трогаются; путь с кириллицей requests
    кодирует процентами сам. Не разобрали хост — вернули как было.
    """
    if not url or url.isascii():
        return url
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if not host or host.isascii():
        return url
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return url
    netloc = parts.netloc
    start = netloc.lower().find(host)  # hostname уже приведён к нижнему регистру
    if start < 0:
        return url
    netloc = netloc[:start] + ascii_host + netloc[start + len(host):]
    return urllib.parse.urlunsplit(parts._replace(netloc=netloc))


def _latin1_ok(value: str) -> bool:
    try:
        value.encode("latin-1")
    except UnicodeEncodeError:
        return False
    return True


def wire_headers(headers: Optional[dict]) -> dict:
    """Заголовки рецепта в виде, который можно положить на провод.

    Правило одно: значение обязано кодироваться latin-1 (так делает
    http.client). Значение-URL (Referer, Origin) чинится: хост -> punycode,
    остаток -> процентное кодирование — выбросить его нельзя, Referer/Origin
    ждёт WAF (SKILL.md:84). Значение, которое URL не является и не кодируется
    («User-Agent»: «браузерный» — слово-заглушка из рецепта smr_mesto_schastya,
    из-за которой объект 05.09 получил insufficient_data), выбрасывается: под
    ним лежит умолчание DEFAULT_HEADERS, и запрос уходит, а не падает.
    Заголовок молча не переписывается ни во что иное — только чинится хост
    или убирается всё значение.
    """
    clean = {}
    for name, value in (headers or {}).items():
        if not isinstance(value, str) or _latin1_ok(value):
            clean[name] = value
            continue
        if "://" in value:
            fixed = punycode_url(value)
            if not _latin1_ok(fixed):
                # процентное кодирование только не-ASCII: разделители URL
                # остаются как есть, уже закодированные %XX не портятся
                fixed = urllib.parse.quote(fixed, safe=":/?#[]@!$&'()*+,;=%")
            if _latin1_ok(fixed):
                clean[name] = fixed
        # иначе — выбрасываем: непередаваемое значение вне URL
    return clean


def _lock_for(host: str) -> threading.Lock:
    with _host_locks_guard:
        return _host_locks.setdefault(host, threading.Lock())


@contextlib.contextmanager
def host_turn(url: str, pause_sec: float = PAUSE_SEC):
    """Занять очередь к хосту url на время одного запроса.

    Кто ходит к хосту НЕ этим fetch (чужой транспорт, разведчик), обязан
    брать очередь этим контекстом: очередь к одному хосту одна на процесс,
    чем бы запрос ни отправлялся. Голого _wait_host_turn для этого мало —
    он выдерживает паузу, но соседа в неё не пускает замок, а не расчёт.
    Внутри тела контекста запрос надо отметить (_note_request), иначе
    следующий в очереди отмерит паузу от чужого запроса.
    """
    with _lock_for(host_of(url)):
        _wait_host_turn(url, pause_sec)
        yield


@contextlib.contextmanager
def request_budget(seconds: Optional[float]):
    """Ограничить время съёма ОДНОГО объекта: дальше новых запросов не шлём.

    Зачем в транспорте, а не в cli (ревью wave3-core): дедлайн прогона
    останавливает выдачу НОВЫХ целей, а начатая цель шла до конца — у ветки
    tl_api это три обязательных запроса плюс POST на каждую ночь фонда, у
    pineriver_hotel замер дал 117 запросов. Цель, начатая на 89-й минуте при
    дедлайне 90 и TimeoutStartSec=120min, съедала весь хвост прогона
    (сводка, слой сезонности, телеграм, строка в Run Log). Резать можно
    только там, где запрос отправляется, — здесь.

    Промах бюджета стоит не больше одного незавершённого запроса: идущий
    запрос не прерывается, его держит таймаут TIMEOUT_SEC.

    seconds=None или <= 0 — бюджета нет (разведчик, тесты, ручной вызов).
    """
    previous = getattr(_local, "budget_deadline", None)
    _local.budget_deadline = (time.monotonic() + seconds
                              if seconds and seconds > 0 else None)
    try:
        yield
    finally:
        _local.budget_deadline = previous


def budget_left() -> Optional[float]:
    """Сколько секунд бюджета осталось у съёма (None — бюджета нет)."""
    deadline = getattr(_local, "budget_deadline", None)
    return None if deadline is None else deadline - time.monotonic()


def _check_budget(url: str) -> None:
    left = budget_left()
    if left is not None and left <= 0:
        raise BudgetExceeded(
            f"бюджет времени на объект исчерпан — запрос к {host_of(url)} "
            f"не отправлен")


def _jitter() -> float:
    """Случайная добавка к паузе: ровный ритм в 1.2 с слишком машинный."""
    return random.uniform(0.0, PAUSE_JITTER_SEC)


def _wait_host_turn(url: str, pause_sec: float) -> None:
    """Выдержать паузу от ПОСЛЕДНЕГО запроса к хосту url — чьим бы он ни был.

    Две причины подождать: наша собственная пауза приличия (pause_sec плюс
    джиттер) и просьба самого хоста (Retry-After у 429). Берём позднейшую.
    Просьбу дольше MAX_HOST_WAIT_SEC не высиживаем: честнее пропустить
    объект, чем отдать окно таймера одному хосту.
    """
    host = _host(url)
    now = time.monotonic()
    ready = []
    last = _last_request_by_host.get(host)
    if last is not None:
        ready.append(last + pause_sec + _jitter())
    not_before = _host_not_before.get(host)
    if not_before is not None:
        if not_before - now > MAX_HOST_WAIT_SEC:
            raise AccessRefused(
                f"{host} просит подождать {round(not_before - now)} с "
                f"(Retry-After) — объект пропущен, ждать дольше "
                f"{MAX_HOST_WAIT_SEC} с прогон не может")
        ready.append(not_before)
    if not ready:
        return
    wait = max(ready) - now
    if wait > 0:
        time.sleep(wait)


def _note_request(url: str) -> None:
    """Запомнить момент запроса к хосту url (зовётся и при сбое запроса)."""
    _last_request_by_host[_host(url)] = time.monotonic()


def parse_retry_after(value) -> Optional[float]:
    """Заголовок Retry-After -> секунды ожидания. Не разобрали -> None.

    Формата два: число секунд и HTTP-дата. Отрицательное («дата в прошлом»)
    приравнивается к нулю — ждать нечего.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0.0, float(int(text)))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    now = datetime.now(when.tzinfo) if when.tzinfo else datetime.now()
    return max(0.0, (when - now).total_seconds())


def _note_retry_after(url: str, resp) -> None:
    """Запомнить просьбу хоста подождать (Retry-After) — для паузы и вердикта.

    Просьбу выдерживает сам транспорт (_host_not_before), и только у 429 —
    так было и раньше. Отдельно значение кладётся в память ПОТОКА: оттуда
    его берёт classify_response, которому ни один пробник заголовки не
    передавал (все десять вызовов зовут её без headers), из-за чего
    retry_after в вердикте был всегда None и приписка «хост просит подождать
    N с» не появлялась никогда.
    """
    headers = getattr(resp, "headers", None) or {}
    delay = parse_retry_after(headers.get("Retry-After"))
    # Пишем ВСЕГДА, в том числе None: молчание нового ответа обязано стирать
    # просьбу прежнего, иначе она прилипнет к чужому вердикту.
    _local.retry_after = delay
    if delay and getattr(resp, "status_code", None) == 429:
        _host_not_before[_host(url)] = time.monotonic() + delay


def last_retry_after() -> Optional[float]:
    """Retry-After ПОСЛЕДНЕГО ответа в этом потоке (None — хост не просил).

    Значение живёт от ответа до ответа. Если запрос не дошёл до ответа
    (сетевой сбой), оно остаётся от предыдущего — но на этом пути каскад
    вердиктов и не зовётся: пробник ловит OSError раньше.
    """
    return getattr(_local, "retry_after", None)


# ---------------------------------------------------------------------------
# Чтение тела с потолком
# ---------------------------------------------------------------------------

def _capture_body(resp) -> bytes:
    """Прочитать тело потоком, не длиннее MAX_BODY_BYTES, и вернуть его в resp.

    Тело кладётся обратно в объект ответа (_content), чтобы .text/.json()
    декодировали его штатной механикой requests: среди снимаемых сайтов есть
    страницы в windows-1251, и своя декодировка сломала бы их разбор.
    Соединение закрывается всегда — оборванное чтение не должно держать сокет.
    """
    body = bytearray()
    try:
        for chunk in resp.iter_content(BODY_CHUNK_BYTES):
            if not chunk:
                continue
            body += chunk
            if len(body) > MAX_BODY_BYTES:
                raise ResponseTooLarge(
                    f"тело ответа больше {MAX_BODY_BYTES // (1024 * 1024)} МБ "
                    f"— не читаем целиком")
    finally:
        close = getattr(resp, "close", None)
        if callable(close):
            close()
    data = bytes(body)
    resp._content = data
    resp._content_consumed = True
    return data


# ---------------------------------------------------------------------------
# Тело последнего НЕудачного ответа: без него заслон неотличим от смены схемы
# ---------------------------------------------------------------------------

def last_body_text(fetch=None) -> str:
    """Текст тела последнего НЕудачного ответа этого fetch (или пусто).

    Зачем: Cloudflare и DDoS-Guard отдают страницу-заслон кодом HTTP 200 с
    HTML-телом. У JSON-пробника это (200, None) — ровно то же, что видно при
    смене схемы, и каскад слал живой рецепт на переразведку. Ветка
    is_challenge в classify_response была с тикета 03, но передать ей тело
    было нечем: fetch отдавал только (статус, JSON) и тело выбрасывал.
    Хост ru-ibe.tlintegration.ru прикрыт WAF, то есть заслон — штатное
    поведение, а не экзотика: одна такая ночь выбивала до восьми рецептов.

    Тело помнит САМ fetch (атрибут last_body_text), а не модуль: цели разных
    хостов идут параллельно в разных потоках, у каждой свой fetch, и общий
    на модуль тайник отдавал бы соседское тело.
    """
    value = getattr(fetch, "last_body_text", "")
    return value if isinstance(value, str) else ""


def _body_snippet(resp) -> str:
    """Начало тела ответа, не длиннее BODY_SNIPPET_CHARS."""
    try:
        return resp.text[:BODY_SNIPPET_CHARS]
    except Exception:      # noqa: BLE001 — тело необязательное удобство
        return ""


# ---------------------------------------------------------------------------
# Живой fetch: три варианта транспорта на одной механике
# ---------------------------------------------------------------------------

def make_fetch(mode: str = "get_json", pause_sec: float = PAUSE_SEC) -> Callable:
    """Живой fetch: requests-сессия, пауза по хосту, таймаут 25 с, БЕЗ ретраев.

    mode:
      - "get_json":  fetch(url, headers[, payload]) -> (status, JSON | None,
                     когда тело не JSON). С payload уходит POST — так шаг
                     фонда типов (hotel_availability) живёт на том же fetch,
                     что и календарь, не заводя второго транспорта и второго
                     трекера пауз;
      - "post_json": fetch(url, headers, payload) -> (status, JSON | None)
      - "get_text":  fetch(url, headers) -> (status, текст тела)
    Сетевые сбои пробрасываются как OSError (requests.RequestException —
    его подкласс). Форма ответа не менялась с 14.08 — пробники распаковывают
    её парой; тело НЕудачного ответа fetch помнит отдельно, атрибутом
    last_body_text (см. last_body_text() выше), а не третьим элементом:
    третий элемент пришлось бы разобрать всем шести движкам сразу.
    """
    import requests

    session = requests.Session()

    def _request(url: str, headers: dict, payload=None):
        # Хост в punycode ДО бюджета и очереди: ключ паузы — хост, и
        # кириллическое с punycode-написанием одного сайта обязаны попасть в
        # одну очередь, а не в две без паузы между ними.
        url = punycode_url(url)
        headers = wire_headers(headers)
        # Бюджет объекта проверяется ДО очереди к хосту: стоять в очереди за
        # запросом, который отправлять уже нельзя, значит держать хост-замок
        # и тормозить соседние цели той же группы.
        _check_budget(url)
        # Очередь к хосту берётся на ВЕСЬ запрос, включая чтение тела:
        # соседний поток той же группы обязан ждать конца, а не начала.
        with host_turn(url, pause_sec):
            try:
                merged = {**DEFAULT_HEADERS, **headers}
                if payload is None:
                    resp = session.get(url, headers=merged,
                                       timeout=TIMEOUT_SEC, stream=True)
                else:
                    resp = session.post(url, json=payload, headers=merged,
                                        timeout=TIMEOUT_SEC, stream=True)
                _capture_body(resp)
            finally:
                # Отметка ставится в КОНЦЕ запроса, вместе с чтением тела.
                # До stream=True сессия возвращалась с уже вычитанным телом,
                # и отметка приходилась на конец сама собой; с потоковым
                # чтением она уехала на заголовки, и пауза к хосту
                # укоротилась на время чтения — у объектов с крупными
                # календарями (a_ureki 195 КБ) заметно. Правило «>= 1.2 с к
                # одному хосту» меряется от конца ответа, а не от его начала.
                _note_request(url)
            _note_retry_after(url, resp)
            return resp

    def _json(resp):
        try:
            return resp.json()
        except ValueError:
            return None

    def _ask(url: str, headers: dict, payload=None, *, as_text: bool = False):
        """Общее тело всех трёх режимов: запрос, разбор, память о теле."""
        fetch.last_body_text = ""
        resp = _request(url, headers, payload)
        if as_text:
            data = resp.text
            ok = resp.status_code == 200
        else:
            data = _json(resp)
            ok = resp.status_code == 200 and data is not None
        if not ok:
            # Тело держим ТОЛЬКО у неудачного ответа: по нему пробник
            # отличает страницу-заслон от смены схемы (last_body_text).
            fetch.last_body_text = _body_snippet(resp)
        return resp.status_code, data

    if mode == "get_json":
        def fetch(url: str, headers: dict, payload: Optional[dict] = None):
            return _ask(url, headers, payload)
    elif mode == "post_json":
        def fetch(url: str, headers: dict, payload: dict):
            return _ask(url, headers, payload)
    elif mode == "get_text":
        def fetch(url: str, headers: dict):
            return _ask(url, headers, as_text=True)
    else:
        raise ValueError(f"неизвестный режим make_fetch: {mode!r}")
    fetch.last_body_text = ""
    return fetch


# ---------------------------------------------------------------------------
# Каскад ошибок HTTP-ответа
# ---------------------------------------------------------------------------

def is_challenge(body_text: str) -> bool:
    """Похоже ли тело на страницу-заслон (Cloudflare/DDoS-Guard/капча).

    Нужно ровно для одного: честно сказать «нас не пустили» вместо «схема
    сменилась». Обходить заслон запрещено (SKILL.md «Запреты»).

    Google reCAPTCHA из решения ИСКЛЮЧЕНА: её подключают к СВОЕЙ форме
    обратной связи, а не к заслону — российские антиботы ставят свои
    (Cloudflare cf-chl, DDoS-Guard, Yandex SmartCaptcha). Живая цена ошибки,
    09.09.2026: домен adygea-otdyh.ru припаркован в Timeweb, парковочная
    страница подключает recaptcha/api.js — и разведка отчиталась «антибот»
    вместо «домена больше нет», то есть объект ушёл в ветку «нужен браузер»
    вместо честного вывода. Тот же ложный след раньше ловили на Tilda
    (см. докстринг scout.py, ревью волны 3). SmartCaptcha Яндекса остаётся
    маркером: её ставят именно заслоном.
    """
    if not body_text:
        return False
    head = body_text[:4096].lower().replace("recaptcha", "")
    return any(marker in head for marker in CHALLENGE_MARKERS)


def classify_response(status: int, body_ok: bool, what: str, *,
                      detail: str = "", hint: str = "", headers=None,
                      body_text: str = "") -> ResponseVerdict:
    """Каскад с ЧЕТЫРЬМЯ исходами: ok / refused / broken / network.

    Отличие от classify_http (та осталась ради старых пробников): 403, 429 и
    страница-заслон дают "refused" — «нас не пустили». Такой исход рецепт не
    ломает: счётчик суток ведёт occupancy_core.note_refusal, и broken
    ставится, только когда отказ повторился подряд несколько суток. Всё
    остальное как раньше: прочие 4xx и негодное тело -> broken, 5xx и
    странные коды -> network (рецепт жив, нужен повтор прогона).
    """
    if status == 200 and body_ok:
        return ResponseVerdict("ok", "", None)
    reason = f"{what}: HTTP {status}" + ("" if body_ok else ", тело не JSON")
    if detail:
        reason += f" — {detail}"
    # Заголовки переданы — верим им; не переданы — берём просьбу последнего
    # ответа этого потока (пробники зовут каскад сразу после своего fetch).
    retry_after = (parse_retry_after((headers or {}).get("Retry-After"))
                   if headers else last_retry_after())
    if status in (403, 429) or is_challenge(body_text):
        note = "нас не пустили (антибот/лимит), рецепт не трогаем"
        if retry_after:
            note += f", хост просит подождать {round(retry_after)} с"
        return ResponseVerdict("refused", f"{reason} — {note}", retry_after)
    if 400 <= status < 500 or not body_ok:
        if hint:
            reason += f" — {hint}"
        return ResponseVerdict("broken", reason, None)
    return ResponseVerdict("network", reason, None)


def classify_http(status: int, body_ok: bool, what: str, *, detail: str = "",
                  hint: str = "") -> tuple[Optional[str], Optional[str]]:
    """(broken_reason | None, network_reason | None) по статусу и телу.

    СТАРЫЙ каскад с двумя исходами: его зовут пробники, ещё не переведённые
    на classify_response, поэтому поведение сохранено дословно — 403 и 429
    здесь по-прежнему ломают рецепт.

    200 с годным телом -> (None, None). 4xx или негодное тело -> broken
    (переразведка), с необязательными detail (текст движка) и hint
    («похоже на смену API…»). Остальное (5xx, странные коды) -> сетевой
    сбой: рецепт жив, нужен повтор прогона.
    """
    if status == 200 and body_ok:
        return None, None
    reason = f"{what}: HTTP {status}" + ("" if body_ok else ", тело не JSON")
    if detail:
        reason += f" — {detail}"
    if 400 <= status < 500 or not body_ok:
        if hint:
            reason += f" — {hint}"
        return reason, None
    return None, reason


# ---------------------------------------------------------------------------
# Сворачивание однотипных сбоев
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"\d{4}-\d{2}(-\d{2})?")
_NUM_RE = re.compile(r"\d+")
_SPACE_RE = re.compile(r"\s+")
# Голая вертикальная черта (уже экранированную не трогаем — иначе повторный
# проход через one_line превратил бы \| в \\|).
_BARE_PIPE_RE = re.compile(r"(?<!\\)\|")


def one_line(text: str, limit: int = 300) -> str:
    """Чужой текст -> одна строка не длиннее limit, безопасная для таблицы.

    Причина хранится в снапшоте и вклеивается в колонку «Источник»
    markdown-таблицы сводки. Перевод строки её рвал, а вертикальная черта
    (хватает одного 4xx с пайпом в теле чужого движка) добавляла лишнюю
    колонку и разъезжала строку — поэтому черта экранируется, а не удаляется:
    читателю причина нужна дословно.
    """
    flat = _BARE_PIPE_RE.sub(r"\\|", _SPACE_RE.sub(" ", str(text)).strip())
    return flat if len(flat) <= limit else flat[:limit - 1] + "…"


def _failure_template(text: str) -> str:
    """Ключ однотипности: даты и числа заменены плейсхолдерами."""
    return _NUM_RE.sub("N", _DATE_RE.sub("<дата>", text))


def collapse_failures(failures, *, max_groups: int = 6) -> list[str]:
    """Однотипные пофакторные сбои -> по одному пункту на вид.

    58 строк «остатки на 2026-09-12: HTTP 503» — это один сбой, повторённый
    58 раз; списком они вытесняли из причины всё остальное и делали строку
    сводки нечитаемой. Порядок видов — по первому появлению.
    """
    groups: dict[str, dict] = {}
    for raw in failures or []:
        text = one_line(raw)
        if not text:
            continue
        key = _failure_template(text)
        group = groups.setdefault(key, {"count": 0, "last": text})
        group["count"] += 1
        group["last"] = text
    items = []
    for key, group in list(groups.items())[:max_groups]:
        if group["count"] == 1:
            items.append(group["last"])
        else:
            items.append(f"{key} — {group['count']} раз, "
                         f"последняя: {group['last']}")
    hidden = len(groups) - len(items)
    if hidden > 0:
        items.append(f"и ещё {hidden} видов сбоев")
    return items


# ---------------------------------------------------------------------------
# Общая сборка объекта снапшота
# ---------------------------------------------------------------------------

def _iso_dates(date_from: date, date_to: date) -> list[str]:
    """Даты date_from..date_to включительно; дата = ночь с неё на завтра."""
    return [(date_from + timedelta(days=i)).isoformat()
            for i in range((date_to - date_from).days + 1)]


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _base_object(username: str, recipe: dict, granularity: str,
                 default_engine: str = "?") -> dict:
    return {
        "username": username,
        "site": recipe.get("site", ""),
        "engine": recipe.get("engine", default_engine),
        "source_kind": "module",
        "granularity": granularity,
        "units": {},
        "checked_at": _now_iso(),
        "status": "ok",
        "reason": "",
        "source_urls": [],
    }


def _finish(obj: dict, failures: list[str], broken: Optional[str], *,
            refused: Optional[str] = None,
            refusal_status: Optional[int] = None) -> tuple[dict, Optional[str]]:
    """Довести объект до статуса. refused — «нас не пустили», рецепт ЖИВ.

    Отказ хоста возвращается вторым элементом как None (broken_reason нет) и
    кладётся в объект полем refusal: считать сутки подряд и решать, пора ли
    ломать рецепт, — дело CLI (occupancy_core.note_refusal), а не одного
    снимка.
    """
    obj["checked_at"] = _now_iso()
    has_known = any(
        cell.get("state") in ("free", "busy", "sales_not_open")
        for cells in obj["units"].values() for cell in cells.values())
    collapsed = collapse_failures(failures)
    if refused:
        obj["status"] = "partial" if has_known else "insufficient_data"
        obj["reason"] = "; ".join([one_line(refused)] + collapsed)
        obj["refusal"] = {"reason": one_line(refused), "at": _now_iso()}
        if refusal_status is not None:
            obj["refusal"]["status"] = refusal_status
        return obj, None
    if broken:
        obj["status"] = "insufficient_data"
        obj["reason"] = one_line(broken)
    elif not has_known:
        obj["status"] = "insufficient_data"
        obj["reason"] = ("; ".join(collapsed)
                         or "движок не отдал ни одной известной клетки")
    elif collapsed:
        obj["status"] = "partial"
        obj["reason"] = "; ".join(collapsed)
    return obj, broken
