# -*- coding: utf-8 -*-
"""Разведчик рецептов: сайт объекта -> опознанный движок + черновик рецепта.

Зачем машина. 31 самарский объект руками агента — это 31 сессия с браузером.
Живая проверка 04.09.2026 показала, что рутина детерминированна: на ПЯТИ
самарских сайтах из пяти движок опознался одним обычным GET главной, а
главный параметр рецепта достался регуляркой из того же HTML (dachi63.ru и
piskali.ru — Bnovo, lakevilleglamping.ru — «Бронируй Онлайн»,
prostory-village.ru — TravelLine, смоларелакс.рф — litepms). Плюс колонка
«Движок бронирования» в таблице области 01.09 ВРЁТ (первый же объект помечен
custom, а на сайте живой znms-виджет) — опознавать надо по факту.

Что машина НЕ берёт на себя (решение Р5 спеки 2026-09-04): спорные случаи.
Два сильных маркера разных движков на одной странице — всегда отказ и вердикт
агента: прецедент pineriver.ru, где хвост TravelLine 2022 года лежит поверх
работающего UHotels, и остановка на первом маркере дала бы ложное «данных
нет» на объекте, который снимается целиком. Поэтому detect_engines
возвращает СПИСОК движков, а не первое совпадение.

Три исхода (verdict):
  high   — ровно один движок и все обязательные параметры добыты;
  low    — движок опознан, чего-то не хватает (см. missing_params);
  refuse — движка нет, антибот/капча, сетевой сбой или два сильных маркера.

Разведчик НИЧЕГО не включает в производство: выход — файл-черновик в
agent-runtime/research/glamping/occupancy/scout-out/<ключ>.json. Реестр
рецептов правит человек (тикет 12) и только после успешного прогона
пробником — здесь нет ни одной строки записи в него, и это проверяется
тестом test_module_never_references_the_registry.

Грабли, на которых собран разбор HTML:
- Tilda прячет разметку блока внутри JSON страницы, поэтому кавычки и слэши
  приходят экранированными (`\\"token\\":\\"Sh0gLTkBgP\\"` у homereserve).
  Весь поиск идёт по РАСЭКРАНИРОВАННОМУ тексту (_unescape) — иначе половина
  сайтов на Tilda молча читается как «маркеров нет».
- Bnovo на живых сайтах инициализируется не BookingIframe из SKILL.md, а
  `Bnovo_Widget.open('_bn_widget_', {uid:"..."})` — ловим uid как UUID при
  любом конструкторе.
- znmsWidget.init принимает ДВА аргумента (селектор и опции), поэтому
  moduleId ищется не сразу за скобкой, а в теле объекта.
- 403 на ru-ibe.tlintegration.ru — известная ложная тревога WAF Qrator
  (SKILL.md:84), а не закрытый API: движок уже опознан, поэтому такой отказ
  роняет исход до low (доразведка агентом), а не до refuse.

Что изменилось после ревью волны 1 (04.09.2026) и почему:
- параметры ищутся по тексту ВСЕХ снятых страниц, а не только той, где нашёлся
  маркер: самая частая живая раскладка — скрипт виджета в шапке всех страниц и
  вызов с uid на /booking, и объект с полностью добываемым рецептом уходил в
  low при уже скачанной странице с параметром;
- регулярки токена HomeReserve, moduleId «Бронируй Онлайн» и hotel UHotels
  привязаны к ВЫЗОВУ виджета: у сайтов на Tilda посторонний JSON с ключом
  token и счётчик с полем moduleId стоят выше виджета, и «первое совпадение на
  странице» уносило в рецепт чужое значение с вердиктом high;
- «нас не пустили» опознаётся ОДНИМ предикатом на весь скилл
  (probes/_common.is_challenge): свой список маркеров не знал ни «just a
  moment», ни «cf-chl», и челлендж Cloudflare со статусом 200 читался как
  «модуля бронирования нет» — объект за антиботом уезжал в «данных нет
  навсегда» вместо ветки доразведки браузером;
- нежилые позиции модуля (палаточные места, сертификаты, услуги) в юниты не
  идут и названы вслух: живой piskali.ru отдал 52 «юнита», из них 36 — «Место
  под палатку»;
- ноль юнитов больше не выдаётся за «справочник не снимался»;
- два СЛАБЫХ маркера разных движков — отказ, а не выбор по алфавиту;
- минимальный срок проживания пишется туда, где его читает пробник
  (request.params.min_stay), добывается у Bnovo расширением окна (GET) и у
  HomeReserve — из календаря, потому что справочник домиков отдаёт null.

Что изменилось после ревью волны 2 (04.09.2026) и почему:
- отсев нежилых позиций перестал выбрасывать домики. Список слов проверили на
  157 живых именах из боевых снапшотов: он рубил «✨ Мечта … камин и мангал»,
  «🏡 Флора … и мангалом», «Комфорт с мангалом и холодильником. Сфера…»
  (знаменатель занятости объекта падал на 17%), а «Дом «Победа»» ловился на
  подстроку «обед». Теперь основание одно из двух: позицию назвал услугой САМ
  движок (нулевая вместимость, тип-услуга, цена за место) либо в имени есть
  признак нежилой позиции по границе слова И нет ни одного признака жилья.
  Слова удобств («мангал», «завтрак», «прокат») из списка убраны совсем, а
  каждая отсеянная позиция уходит в черновик вместе с ПРИЧИНОЙ;
- токен HomeReserve больше не вычитывается из адреса самого маркера движка:
  запасная регулярка ловила `homereserve.ru/widget.js` и отдавала
  token='widget' с вердиктом high, а строгая падала на законной раскладке
  вызова (вложенный объект перед ключом token);
- неопределённый минимальный срок у Bnovo больше не даёт вердикт high:
  лестница окон запускается ВСЯКИЙ раз, когда однночное окно не показало ни
  одной свободной категории (в том числе когда селектов с остатком на
  странице нет вовсе), справочник берётся и с окна пошире, а если срок так и
  не определился — min_stay уходит в missing_params и черновик не «готов»
  (тикет 07: без него пробник красит занятыми все ночи горизонта);
- предикат заслона снова ОДИН на весь скилл: свой потолок размера тела в
  20 КБ делал разведку строго слабее probes/_common.is_challenge;
- заголовки черновика повторяют живые рецепты реестра — у bnovo и litepms
  один Referer, Origin остаётся у travelline, bronirui, homereserve, uhotels;
- access_refused взводит только отказ САМОГО САЙТА объекта: 403 Qrator на
  ru-ibe.tlintegration.ru — документированная ложная тревога, и объект с
  живым сайтом уезжал по ней в ветку «нужен браузер»;
- живой fetch берёт ОБЩИЙ замок хоста (transport.host_turn), а не голую
  паузу;
- справочник номеров bronirui пишется в КАНОНИЧЕСКОМ виде рецепта
  ({number_id: {"name", "rooms_count"}}) — плоский вид не доносил фонд до
  пробника, и починка счёта домиков (тикет 04) в бою была no-op.

Что изменилось после ревью волны 3 (04.09.2026) и почему:
- отсеянные нежилые позиции уходят из params.apartment_ids HomeReserve: это
  белый список, по которому пробник выбирает, что снимать, и знаменатель
  занятости объекта — до починки отсев был виден в черновике и не работал в
  бою (тот же класс, что no-op справочника bronirui в волне 2);
- границу вызова виджета держит СКОБКА (call_arguments), а не длина окна:
  класс [^{}] запрещал законный вложенный объект внутри вызова, а сменившее
  его окно «N знаков после вызова» брало первый ключ token/moduleId в
  соседнем теге script — на Tilda форма обратной связи стоит ниже виджета
  так же часто, как выше;
- сильный маркер движка отменяет вердикт заслона: разведка зовёт предикат на
  КАЖДОЙ удачной странице чужого сайта, и штатный тег reCAPTCHA формы
  обратной связи уводил объект с полностью добываемым рецептом в ветку
  «нужен агент с браузером»;
- слова «сертификат», «подарочн», «депозит», «трансфер» признак жилья в имени
  больше не отменяет: «Подарочный сертификат на домик» — сертификат, и в
  знаменателе занятости ему нечего делать.

Что изменилось после ревью волны 4 (04.09.2026) и почему:
- живая разведка берёт ТОТ ЖЕ замок, что и прогон пробников (host_lock,
  один на машину). Прежде замок брал только прогон, а разведчик ходил на те
  же чужие хосты своим процессом мимо него — 04.09 разведка 54 сайтов уже
  шла параллельно с прогонами агентов, и пауза >= 1.2 с к хосту не
  соблюдалась ни разу. Цена названа вслух: пока идёт плановый прогон (окно
  06:31, до 90 минут), живая разведка НЕВОЗМОЖНА — она отвечает отказом с
  причиной, а не ждёт молча. Осознанный обход — флаг --ignore-run-lock: он
  печатает предупреждение и идёт мимо замка, потому что запирать агента на
  полтора часа без выхода хуже, чем дать ему видимый в журнале выбор.
  Разбор уже снятых страниц (подменённый fetch) замка не берёт: в сеть он
  не ходит.

Железные правила SKILL.md действуют дословно: только чтение, никаких форм,
броней и обхода капч; один запрос — одна попытка (ретраев нет); пауза >= 1.2 с
между запросами к одному хосту — живой fetch собран на ОБЩЕМ транспорте
probes/_common.py (трекер хостов, потолок тела ответа, уважение Retry-After),
чтобы разведка и пробники не били один хост наперегонки.
POST-шаги (справочник номеров bronirui, справочник домиков homereserve)
выключены по умолчанию: разведка должна обходиться безобидным GET, а
POST-ветка включается флагом --allow-post осознанно.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, NamedTuple, Optional

import occupancy_core as core
from probes import _common as transport

# Страницы, которые смотрит разведка. Не одна главная: живая бронь часто
# живёт на отдельной странице (у pineriver виджет UHotels нашёлся именно на
# /booking, а на главной был только мёртвый хвост TravelLine).
DEFAULT_PAGES = ("", "booking", "bron")

# Смещения в днях для пар дат справочника юнитов. Сервер Bnovo и API bronirui
# рендерят ТОЛЬКО категории/номера, доступные на запрошенные даты, поэтому
# справочник собирается союзом пар из РАЗНЫХ месяцев (метод тикета 03).
# Полнота союза не гарантирована — 04.09 bronirui на 22.09 отдал 1 номер, а
# на 20.10 ноль при четырёх домиках на сайте, — поэтому scout всегда пишет,
# СКОЛЬКО юнитов нашёл, а сверка с сайтом остаётся за агентом.
UNIT_PAIR_OFFSETS = (18, 55, 96)

DEFAULT_OUT_DIR = core.OCCUPANCY_ROOT / "scout-out"

# Транспорт и его константы — ОБЩИЕ с пробниками (probes/_common, тикет 03):
# потолок тела ответа, уважение Retry-After, один трекер пауз на процесс.
# Своих копий разведка не держит: две редакции «сколько ждать» и «что считать
# заслоном» неизбежно разъезжаются, а ходит она по 31 НЕизвестному сайту —
# в самое рискованное место.
TIMEOUT_SEC = transport.TIMEOUT_SEC
PAUSE_SEC = transport.PAUSE_SEC

# Единственное отличие заголовков разведки от пробников: она читает СТРАНИЦЫ,
# а не JSON API, поэтому Accept другой.
_ACCEPT = {"Accept": "text/html,application/xhtml+xml,application/json,*/*"}

# ---------------------------------------------------------------------------
# Маркеры движков (таблица SKILL.md «Опознать движок по маркерам в HTML»)
# ---------------------------------------------------------------------------
# strong — маркер, который бывает только у живого/бывшего модуля этого движка;
# weak — упоминание имени (ссылка «работает на Bnovo», подпись в футере).
# Два СИЛЬНЫХ маркера разных движков = отказ; слабый в решение не входит,
# иначе ссылка на bnovo.ru в подвале ломала бы опознание соседа.
ENGINE_MARKERS = {
    "travelline": {
        "strong": ("tlintegration", "tl-int-", "tl-booking-open"),
        "weak": ("travelline",),
    },
    "bnovo": {
        "strong": ("widget.reservationsteps.ru", "reservationsteps.ru",
                   "bookingiframe", "bnovo_widget"),
        "weak": ("bnovo",),
    },
    "bronirui": {
        "strong": ("bronirui-online.ru", "bronirui.online", "znmswidget"),
        "weak": (),
    },
    "litepms": {
        "strong": ("litepms.ru/widget", "litepmsembed_id",
                   "litepms.ru/js/widget_embed"),
        "weak": ("litepms",),
    },
    "homereserve": {
        "strong": ("homereserve.ru/widget.js", "initwidgetsearch",
                   "realtycalendar.ru"),
        "weak": ("homereserve",),
    },
    "uhotels": {
        "strong": ("api.uhotels.app", "artdg", "adg-booking-widget"),
        "weak": ("uhotels",),
    },
    # Модуль ЕСТЬ, но пробника v1 нет: рецепт живёт только со status=broken
    # (прецедент реестра — lafa_club).
    "custom-livewire": {"strong": (), "weak": ("livewire",)},
}

SUPPORTED_ENGINES = ("travelline", "bnovo", "bronirui", "litepms",
                     "homereserve", "uhotels")

# Обязательные параметры рецепта: без них пробник не снимет ни одной ночи.
# Справочник юнитов у bronirui и bnovo обязателен не по вкусу, а по механике
# движков: календарь bronirui без number_id отдаёт ВСЕ ночи закрытыми, а
# min_prices без room_type_id даёт агрегат, который красит ночь занятой,
# только когда проданы все категории.
REQUIRED_PARAMS = {
    "travelline": ("hotel_code",),
    "bnovo": ("uid", "room_types"),
    "bronirui": ("module_id", "numbers"),
    "litepms": ("property_id",),
    "homereserve": ("token",),
    "uhotels": ("hotel",),
}

_ANTIBOT_STATUSES = (401, 403, 429)


# ---------------------------------------------------------------------------
# Разбор HTML
# ---------------------------------------------------------------------------

def unescape(text: str) -> str:
    """Расэкранировать HTML, спрятанный внутри JSON страницы (Tilda и т.п.).

    Без этого `\\"token\\":\\"Sh0gLTkBgP\\"` не ловится ни одной регуляркой
    рецепта, и сайт на Tilda молча читается как «модуля нет».
    """
    return (text.replace("\\/", "/").replace('\\"', '"')
            .replace("\\'", "'").replace("\\n", "\n"))


# Потолок длины аргументов вызова: живой initWidgetSearch самарских объектов
# укладывается в сотни знаков (baninaozerah — 190), 20 КБ берутся с запасом на
# минифицированный конфиг. Больше — считаем, что скобку не нашли: лучше «токен
# не добыт» (объект уедет на доразведку), чем токен из чужого тега.
_CALL_ARGS_LIMIT = 20000


def call_arguments(text: str, opener: "re.Pattern") -> Optional[str]:
    """Текст аргументов вызова, ограниченный ЕГО закрывающей скобкой.

    opener — регулярка, кончающаяся открывающей скобкой вызова. Границу
    держим скобками, а не длиной окна: обе прежние попытки провалились
    зеркально — класс [^{}] запрещал законный вложенный объект внутри вызова,
    а окно «N знаков после вызова» пускало внутрь соседний тег script с чужим
    ключом того же имени. Кавычки учитываются, иначе скобка в имени домика
    («Дом «Победа» (2)») сбивает счёт.
    """
    m = opener.search(text)
    if m is None:
        return None
    start = m.end()
    depth = 1
    quote = None
    i = start
    end = min(len(text), start + _CALL_ARGS_LIMIT)
    while i < end:
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return text[start:i]
        i += 1
    return None


def detect_engines(html: str) -> list[dict]:
    """HTML -> список найденных движков (по одной записи на движок).

    Возвращается СПИСОК, а не первое совпадение: на одной странице живут два
    движка, и первый по тексту может быть мёртвым (pineriver, решение Р5).
    Порядок: сначала сильные маркеры, внутри — как в ENGINE_MARKERS.
    """
    text = unescape(html).lower()
    found: list[dict] = []
    for engine, markers in ENGINE_MARKERS.items():
        hit = next((m for m in markers["strong"] if m in text), None)
        strength = "strong"
        if hit is None:
            hit = next((m for m in markers["weak"] if m in text), None)
            strength = "weak"
        if hit is not None:
            found.append({"engine": engine, "marker": hit,
                          "strength": strength})
    found.sort(key=lambda f: 0 if f["strength"] == "strong" else 1)
    return found


def looks_like_antibot(status: int, body: str,
                       own_site: bool = True) -> Optional[str]:
    """Причина отказа антибота или None. Ретраев не бывает — это конец пути.

    Список маркеров ОДИН на весь скилл (probes/_common.is_challenge): свой
    не знал ни «just a moment», ни «cf-chl», и челлендж читался как «модуля
    бронирования на сайте нет» — объект за антиботом уезжал в «данных нет
    навсегда» вместо ветки «нужен агент с браузером» (тикет 12). Потолок
    размера тела был снят по той же причине и возвращён быть не может.

    Своё у разведки ровно одно и не по размеру, а по смыслу: на странице
    САМОГО САЙТА объекта сильный маркер движка бронирования ОТМЕНЯЕТ вердикт
    заслона. Пробники зовут предикат на НЕуспешном ответе, разведка — на
    каждой удачной странице чужого маркетингового сайта, а там штатный тег
    reCAPTCHA формы обратной связи попадает в те же первые 4096 знаков, что
    смотрит is_challenge (ревью волны 3). Страница-заслон виджета объекта не
    несёт никогда, поэтому отмена не пропускает настоящий заслон, а только не
    выдумывает его на живой странице.

    own_site=False (ответ стороннего API движка — realtycalendar, bronirui,
    reservationsteps) отмену выключает: заслон Cloudflare называет хост, на
    который нас не пустили, а хост движка — это и есть его сильный маркер, и
    отмена превратила бы честное «нас не пустили» в «ответ не той формы».
    """
    if status in _ANTIBOT_STATUSES:
        return f"антибот или отказ доступа (HTTP {status})"
    body = body or ""
    if own_site and any(found["strength"] == "strong"
                        for found in detect_engines(body)):
        return None
    if transport.is_challenge(body):
        head = body[:4096].lower()  # то же окно, что смотрит is_challenge
        hit = next((m for m in transport.CHALLENGE_MARKERS if m in head),
                   "маркер заслона")
        return f"антибот: страница похожа на челлендж ({hit})"
    return None


# --- регулярки параметров -------------------------------------------------
_RE_TL_CONTEXT = re.compile(r"TL-INT-[A-Za-z0-9_.\-]+")
_RE_TL_HOTEL_CODE = re.compile(r"[?&]hotel(?:s\[0\]\.code)?=(\d{2,7})")
_RE_BNOVO_UID = re.compile(
    r"uid\s*[:=]\s*[\"']([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")
_RE_BNOVO_UID_URL = re.compile(
    r"reservationsteps\.ru/rooms/index/([0-9a-fA-F-]{36})")
# Привязка к ВЫЗОВУ виджета обязательна: до ревью волны 1 регулярка брала
# первое совпадение на странице, и `var analytics={moduleId: 99}` счётчика,
# стоящий выше виджета, уходил в рецепт как id модуля брони. Окно «N знаков
# после вызова» этот дефект не лечит, а зеркалит: счётчик ниже виджета так же
# обычен, как выше (ревью волны 3). Поэтому граница берётся не длиной, а
# СКОБКОЙ вызова — call_arguments.
_RE_BRONIRUI_CALL = re.compile(r"znmsWidget\.init\s*\(")
_RE_MODULE_ID_KEY = re.compile(
    r"module[_]?[Ii]d[\"']?\s*[:=]\s*[\"']?(\d+)")
# Запасной путь для сайтов, где init виджета собран не из литерала: годится,
# только если на всей странице ровно одно значение moduleId — иначе выбор
# опять сводится к «первое попавшееся».
_RE_BRONIRUI_MODULE_LOOSE = re.compile(
    r"module[_]?[Ii]d[\"']?\s*[:=]\s*[\"']?(\d+)")
_RE_LITEPMS_EMBED = re.compile(r"litepmsembed_id\s*=\s*(\d+)")
_RE_LITEPMS_URL = re.compile(r"litepms\.ru/widget/\w+\?id=(\d+)")
# Токен HomeReserve берётся ТОЛЬКО из вызова initWidgetSearch: у сайтов на
# Tilda (а это большинство самарских) в теле страницы почти всегда лежит
# посторонний JSON с ключом token — форма обратной связи, — и непривязанная
# регулярка уносила в рецепт его, помечая исход high. Границу вызова держит
# call_arguments, а не длина окна: класс [^{}] ронял законный вложенный
# объект перед токеном («guests»:{...},"token":...) — ревью волны 2, — а
# сменивший его [\s\S]{0,2000} границу снял вовсе и брал ПЕРВЫЙ ключ token
# после вызова, в том числе из соседнего тега script (ревью волны 3).
_RE_HR_CALL = re.compile(r"initWidgetSearch\s*\(")
_RE_TOKEN_KEY = re.compile(
    r"[\"']token[\"']\s*:\s*[\"']([A-Za-z0-9_\-]{4,32})")
# Запасной путь — токен в адресе вида homereserve.ru/<токен>. Хвост с точкой
# исключён лукахедом, а имена путей самого движка — списком: без этого
# регулярка ловила `homereserve.ru/widget.js` из тега script, то есть САМ
# сильный маркер движка (ENGINE_MARKERS), и в рецепт уходило token='widget'.
_RE_HR_TOKEN_URL = re.compile(
    r"homereserve\.ru/([A-Za-z0-9_\-]{6,20})(?![A-Za-z0-9_\-.])")
_HR_NOT_TOKENS = ("widget", "widgets", "static", "assets", "images", "script",
                  "scripts", "booking", "search", "calendar", "apartments")
_RE_HR_APARTMENTS = re.compile(r"[\"']apartments[\"']\s*:\s*\[([0-9,\s]*)\]")
# Токен UHotels — у ключа hotel конфигурации artDg; форма «число:32 hex»
# встречается и у счётчиков, поэтому голая регулярка осталась запасной и
# работает только при единственном совпадении на странице.
_RE_UHOTELS_HOTEL = re.compile(
    r"[\"']hotel[\"']\s*:\s*[\"'](\d+:[0-9a-f]{32})")
_RE_UHOTELS_TOKEN = re.compile(r"(\d+:[0-9a-f]{32})")
# Bnovo rooms/index: селект категории несёт ЕЁ id в data-room-id, а подкроватей
# («subrooms») — чужой id при том же data-real-room-id; берём только строки,
# где id совпали, иначе в рецепт попадут несуществующие категории.
_RE_BNOVO_SELECT = re.compile(
    r'data-real-room-id="(\d+)"[^>]*?data-room-id="(\d+)"[^>]*?'
    r'data-room-type-title="([^"]*)"')
_RE_BNOVO_CATEGORY = re.compile(
    r'data-category-id="(\d+)"[^>]*?data-name="([^"]*)"')
_RE_BNOVO_SELECT_TAG = re.compile(r"<select\b[^>]*>")
_RE_TAG_ATTR = re.compile(r'([a-zA-Z-]+)="([^"]*)"')
_RE_ACCOUNT_ID_A = re.compile(
    r'name=["\']?account_id["\']?[^>]{0,200}?value=["\']?(\d+)')
_RE_ACCOUNT_ID_B = re.compile(
    r'value=["\']?(\d+)["\']?[^>]{0,200}?name=["\']?account_id')
_RE_LITEPMS_ROOM = re.compile(r'class="room-title"')

# Нежилые позиции модуля в справочник юнитов не идут: занятость считается по
# ДОМИКАМ. Живой piskali.ru 04.09 отдал 52 «юнита», из которых 36 — «Место
# под палатку»: они продаются иначе, их занятость занижает занятость домиков
# (спека, User Story 1-2), и каждая стоит пробнику отдельного запроса с паузой
# 1.2 с — 52 запроса вместо 16 это ~63 с на объект вместо ~19.
#
# Отсев работает по ДВУМ разным основаниям, и порядок их важен:
#  1) позицию назвал услугой САМ модуль (нулевая вместимость, тип-услуга,
#     цена за место) — самое надёжное, потому что это не догадка по имени;
#  2) в имени стоит признак нежилой позиции И в нём НЕТ ни одного признака
#     жилья.
# Второе условие появилось после ревью волны 2: список слов проверили на 157
# живых именах из боевых снапшотов (прогоны 01-04.09), и оказалось, что он
# выбрасывает НАСТОЯЩИЕ домики — «✨ Мечта … камин и мангал», «🏡 Флора …
# костровой зоной и мангалом», «Комфорт с мангалом и холодильником. Сфера с
# панорамным видом на лес» (bani_na_ozerah 12 -> 10 домиков, glamping_iva_spa
# 6 -> 5), а «Дом «Победа»» ловился на подстроку «обед». Поэтому: слова
# удобств («мангал», «завтрак», «прокат») из списка убраны совсем, поиск идёт
# по границе слова, а признак жилья в имени отменяет отсев.
# Норма прежняя (SKILL.md:100): спорное имя лучше оставить домиком и дать
# агенту сверить (тикет 12), чем молча выбросить жильё.
# Слова-признаки услуги, которые признак жилья в имени НЕ отменяет: домика с
# таким словом в имени не бывает по построению («Подарочный сертификат на
# домик» — сертификат, а не домик). Проверено на 189 живых именах из боевых
# снапшотов (agent-runtime/research/glamping/occupancy, все прогоны): ни
# одного вхождения, то есть отмена ничего не спасала, а сертификат с домиком
# в имени раздувал знаменатель занятости (ревью волны 3).
_SERVICE_WORDS = (
    "сертификат", "подарочн", "депозит",
    "трансфер", "экскурс",
    "ранний заезд", "поздний выезд", "дополнительное место", "доп. место",
)

# Слова, где отмена по признаку жилья обязательна: «Купольный дом с
# парковкой» — домик, а «Место под палатку» — нет.
_NON_RESIDENTIAL_WEAK = (
    "палатк", "автодом", "кемпер", "машиноместо", "парковк", "парковочн",
    "стоянк",
)

_NON_RESIDENTIAL = _SERVICE_WORDS + _NON_RESIDENTIAL_WEAK

# Признаки ЖИЛЬЯ. Хоть один в имени — позиция остаётся домиком, чем бы её имя
# ни заканчивалось. Список собран по 157 живым именам из снапшотов: там есть
# и «Сафари-тент», и «Шатер стандарт 10 м2», и «Баня» — всё это сдаваемое
# жильё, а не услуги.
_DWELLING = (
    "дом", "домик", "коттедж", "шале", "лодж", "номер", "люкс", "апартамент",
    "студи", "бунгало", "шатер", "шатёр", "тент", "типи", "юрт", "изба",
    "баня", "барн", "капсул", "куб", "сфер", "купол", "вилл", "модул",
    "глэмпинг", "глемпинг", "хаус", "house", "хижин", "дача", "баррель",
    "a-frame", "a_frame", "aframe", "фрейм", "чум", "таунхаус", "вигвам",
)

# Как движок сам говорит «это не размещение».
_SERVICE_TYPES = ("service", "услуга", "extra", "additional", "дополнительн")
_RE_PER_PLACE = re.compile(r"за\s+мест|per[_\s-]?person|за\s+чел")


def _word_pattern(words: tuple) -> "re.Pattern":
    """Регулярка «слово начинается здесь» по списку основ.

    Граница нужна буквально: подстрочный поиск ловил «обед» внутри «Победы»,
    а «дом» — внутри «автодома», и обе ошибки меняли цифру занятости.
    """
    body = "|".join(re.escape(word) for word in sorted(words, key=len,
                                                       reverse=True))
    return re.compile(r"(?<![а-яёa-z0-9])(" + body + ")")


_RE_NON_RESIDENTIAL = _word_pattern(_NON_RESIDENTIAL)
_RE_SERVICE_WORD = _word_pattern(_SERVICE_WORDS)
_RE_DWELLING = _word_pattern(_DWELLING)


def module_service_signal(item: object) -> Optional[str]:
    """Признак САМОГО модуля, что позиция не размещение, или None.

    Смотрим только на поля, которые движки реально отдают вместе со списком
    позиций (справочник TL, /v2/numbers bronirui, apartments HomeReserve).
    Нет полей — нет и вывода: молчание модуля не улика.

    Причина возвращается вместе с ИМЕНЕМ поля и его значением нарочно: формы
    ответов bronirui и HomeReserve живьём не проверены (POST-ветка выключена
    по умолчанию), и если движок вкладывает в поле другой смысл, агент увидит
    это в черновике — а не потеряет домик молча.
    """
    if not isinstance(item, dict):
        return None
    for key in ("is_service", "service", "is_extra", "extra"):
        if item.get(key) is True:
            return f"модуль пометил позицию как услугу ({key}=true)"
    for key in ("type", "kind", "category", "item_type"):
        value = item.get(key)
        if isinstance(value, str) and any(word in value.lower()
                                          for word in _SERVICE_TYPES):
            return f"тип позиции у движка — {value!r}, а не размещение"
    for key in ("capacity", "occupancy", "max_occupancy", "places",
                "max_guests", "guests_max"):
        value = item.get(key)
        if isinstance(value, int) and not isinstance(value, bool) \
                and value == 0:
            return f"вместимость по данным движка нулевая ({key}=0)"
    for key in ("price_unit", "unit", "price_type", "pricing"):
        value = item.get(key)
        if isinstance(value, str) and _RE_PER_PLACE.search(value.lower()):
            return f"цена у движка за место, а не за домик ({key}={value!r})"
    return None


def unit_exclusion_reason(name: str, item: object = None) -> Optional[str]:
    """Почему позиция не идёт в домики, или None (значит — домик).

    Причина возвращается ТЕКСТОМ нарочно: она уходит в черновик, и агент
    должен видеть не только что отброшено, но и на каком основании.
    """
    signal = module_service_signal(item)
    if signal:
        return signal
    low = (name or "").lower().replace("ё", "е")
    marker = _RE_NON_RESIDENTIAL.search(low)
    if marker is None:
        return None
    if _RE_DWELLING.search(low) and not _RE_SERVICE_WORD.search(low):
        # Признак жилья в имени сильнее СЛАБОГО слова-признака: «Купольный дом
        # с парковкой» — домик, а не парковка. Но сильные слова услуги он не
        # отменяет: «Подарочный сертификат на домик» — сертификат.
        return None
    return (f"нежилая позиция: в имени есть «{marker.group(1)}» и ни одного "
            f"признака жилья")


def _unit_name(value: object) -> str:
    """Имя позиции из значения справочника: и {id: имя}, и {id: запись}."""
    if isinstance(value, dict):
        return str(value.get("name") or value.get("title") or "")
    return str(value or "")


def partition_units(units: dict, items: Optional[dict] = None
                    ) -> tuple[dict, dict]:
    """Справочник -> (жильё, отсеянное {id: {"name", "reason"}}).

    Значение справочника — либо имя, либо запись рецепта ({"name", ...});
    items — разбор ответа движка, если он богаче имени (там и живут признаки
    самого модуля). Отсеянное не выбрасывается, а возвращается с причиной:
    иначе агент не отличит «модуль отдал 16 домиков» от «мы выкинули 36
    позиций, среди которых могло быть жильё».
    """
    keep: dict = {}
    drop: dict = {}
    for uid, value in units.items():
        # Разбор ответа движка сильнее записи рецепта: в рецепте лежит
        # канонический минимум (имя и фонд), а признаки услуги — в ответе.
        meta = (items or {}).get(uid)
        if meta is None and isinstance(value, dict):
            meta = value
        name = _unit_name(value)
        reason = unit_exclusion_reason(name, meta)
        if reason:
            drop[uid] = {"name": name, "reason": reason}
        else:
            keep[uid] = value
    return keep, drop


def excluded_lines(drop: dict) -> tuple:
    """Отсеянное -> строки «имя — почему» для черновика и ленты оператора."""
    return tuple(f"{rec['name']} — {rec['reason']}" for rec in drop.values())


def _residential(name: str) -> bool:
    """Домик ли это по одному имени (без подсказок движка)."""
    return unit_exclusion_reason(name) is None


def split_residential(units: dict) -> tuple[dict, dict]:
    """{id: имя} -> (жильё, отсеянные нежилые позиции {id: имя}).

    Прежняя форма ответа сохранена ради обратной совместимости; причины
    отсева отдаёт partition_units, на котором эта функция и стоит.
    """
    keep, drop = partition_units(units)
    return keep, {uid: rec["name"] for uid, rec in drop.items()}


def parse_bnovo_room_types(html: str, *, keep_all: bool = False) -> dict:
    """Страница rooms/index -> {room_type_id: имя категории}.

    keep_all=True отдаёт справочник ЦЕЛИКОМ, вместе с нежилыми позициями:
    отсев делает вызывающий (partition_units), потому что отсеянное надо
    назвать в черновике, а не потерять по дороге. Умолчание — прежнее
    поведение: только жильё.
    """
    text = unescape(html)
    out: dict[str, str] = {}
    for real_id, room_id, title in _RE_BNOVO_SELECT.findall(text):
        title = title.strip()
        if real_id == room_id and title and (keep_all or _residential(title)):
            out[room_id] = title
    for cat_id, name in _RE_BNOVO_CATEGORY.findall(text):
        name = name.strip()
        if name and (keep_all or _residential(name)):
            out.setdefault(cat_id, name)
    return out


def parse_bnovo_availability(html: str) -> dict:
    """Страница rooms/index -> {категория: сколько номеров свободно}.

    Признак тот же, которым живёт пробник (data-available в селекте категории,
    probes/travelline.parse_rooms_page_inventory). Разведке он нужен ради
    одного: отличить «на эти даты всё продано» от «окно короче минимального
    срока проживания» — см. _bnovo_min_stay_ladder.

    ПУСТОЙ словарь и словарь из нулей — разное: пусто значит «на странице нет
    ни одного селекта с остатком» (вёрстка сменилась либо выбирать нечего), и
    лестницу окон по такой странице запускать нельзя — это будут два запроса
    в никуда.
    """
    text = unescape(html)
    free: dict[str, int] = {}
    for tag in _RE_BNOVO_SELECT_TAG.findall(text):
        attrs = dict(_RE_TAG_ATTR.findall(tag))
        code = attrs.get("data-real-room-id")
        available = attrs.get("data-available")
        if not code or not available or not available.isdigit():
            continue
        free[code] = max(free.get(code, 0), int(available))
    return free


def parse_bnovo_free_units(html: str) -> int:
    """Сколько номеров свободно на rooms/index (сумма по категориям)."""
    return sum(parse_bnovo_availability(html).values())


def unique_match(pattern, text: str) -> Optional[str]:
    """Значение регулярки, если на странице оно РОВНО ОДНО, иначе None.

    Страховка запасных (непривязанных к виджету) путей добычи: пока совпадение
    единственное, выбирать не из чего и ошибиться нельзя; как только их два —
    «первое попавшееся» снова становится монеткой, и честнее вернуть пусто.
    """
    values = {m for m in pattern.findall(text)}
    return next(iter(values)) if len(values) == 1 else None


def parse_account_id(html: str) -> Optional[str]:
    """account_id из скрытого поля формы листа ожидания (может не быть).

    Отсутствие — НЕ ошибка: у объектов с выключенным листом ожидания поля нет
    вовсе (проверка 15.08 на yck_kuzminskoe), просто фонд типов пробник будет
    снимать тяжёлым страничным фолбэком.
    """
    text = unescape(html)
    for pattern in (_RE_ACCOUNT_ID_A, _RE_ACCOUNT_ID_B):
        m = pattern.search(text)
        if m:
            return m.group(1)
    return None


def parse_bronirui_numbers(payload: object) -> dict:
    """Ответ /v2/numbers -> {number_id: {"name": имя, "rooms_count": фонд}}.

    Это КАНОНИЧЕСКИЙ вид справочника в рецепте, и он не косметика: пробник
    берёт фонд номера именно отсюда (probes/bronirui.directory, тикет 04).
    Пока разведка писала плоское {number_id: имя}, фонд не доходил до пробника
    ни при каком раскладе — тип из трёх домиков считался одним, и починка
    счёта домиков в бою была no-op (ревью волны 2).

    rooms_count пишется, ТОЛЬКО если движок его отдал целым числом: поля нет —
    ключа нет, и пробник честно скажет «считано по номерам без фонда». Выдумать
    единицу значило бы соврать про фонд.

    Форма ответа живьём НЕ проверена (POST-ветка выключена по умолчанию,
    04.09 живой прогон шёл только GET-ами), поэтому парсер терпимый: принимает
    и {"numbers": [...]}, и голый список; запись без id пропускается.
    """
    items = payload
    if isinstance(payload, dict):
        for key in ("numbers", "data", "items", "result"):
            if isinstance(payload.get(key), list):
                items = payload[key]
                break
    if not isinstance(items, list):
        return {}
    out: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("id") in (None, ""):
            continue
        number_id = str(item["id"])
        name = str(item.get("name") or item.get("title") or "").strip()
        record: dict = {"name": name or f"Номер {number_id}"}
        rooms = item.get("rooms_count", item.get("roomsCount"))
        if isinstance(rooms, int) and not isinstance(rooms, bool) \
                and rooms >= 1:
            record["rooms_count"] = rooms
        out[number_id] = record
    return out


def parse_bronirui_items(payload: object) -> dict:
    """Ответ /v2/numbers -> {number_id: ЗАПИСЬ движка как есть}.

    Отдельно от parse_bronirui_numbers нарочно: в рецепт идёт только
    канонический вид ({name, rooms_count}), а сырая запись нужна одному —
    признакам самого модуля (тип позиции, вместимость), по которым
    partition_units отличает услугу от домика.
    """
    items = payload
    if isinstance(payload, dict):
        for key in ("numbers", "data", "items", "result"):
            if isinstance(payload.get(key), list):
                items = payload[key]
                break
    if not isinstance(items, list):
        return {}
    return {str(item["id"]): item for item in items
            if isinstance(item, dict) and item.get("id") not in (None, "")}


def merge_numbers(known: dict, fresh: dict) -> dict:
    """Союз справочников номеров: имя посвежее, фонд — наибольший виденный.

    Справочник собирается союзом 2-3 пар дат, и движок на разные даты отдаёт
    разные подмножества номеров. Голый update терял фонд: запись без
    rooms_count со второй даты затирала запись с rooms_count=3 с первой.
    """
    out = dict(known)
    for number_id, record in fresh.items():
        before = out.get(number_id)
        if not isinstance(before, dict):
            out[number_id] = dict(record)
            continue
        merged = {**before, **record}
        rooms = [value for value in (before.get("rooms_count"),
                                     record.get("rooms_count"))
                 if isinstance(value, int) and not isinstance(value, bool)]
        if rooms:
            merged["rooms_count"] = max(rooms)
        out[number_id] = merged
    return out


def find_provider(payload: object) -> Optional[str]:
    """Рекурсивно найти hotel_code в профиле TL (ключ provider где угодно).

    Форма ответа /integration/profile у TL менялась между редакциями виджета,
    а нужен из него ровно один код — поэтому ищем ключ, а не путь.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "provider" and isinstance(value, (str, int)):
                code = str(value).strip()
                if code.isdigit():
                    return code
            found = find_provider(value)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = find_provider(item)
            if found:
                return found
    return None


# ---------------------------------------------------------------------------
# Ключ цели
# ---------------------------------------------------------------------------
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def transliterate(text: str) -> str:
    return "".join(_TRANSLIT.get(ch, ch) for ch in text.lower())


def site_key(url: str, prefix: str = "smr_") -> str:
    """Сайт -> ключ цели `smr_<транслит>` (решение Р2 спеки).

    Ключ навсегда связывает снапшоты объекта, поэтому он не домен и не
    инстаграм (домены умирают: dvoryanovka.ru и luka-village.ru уже отдают
    402). Префикс обязателен: в самарском списке есть «Заповедник», а в
    действующем targets.json живёт подмосковный zapovednik_glamp.
    """
    host = urllib.parse.urlsplit(_normalize_site(url)).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if "xn--" in host:  # punycode IDN -> юникод, иначе транслит бессмыслен
        try:
            host = host.encode("ascii").decode("idna")
        except (UnicodeError, UnicodeDecodeError):
            pass
    labels = host.split(".")
    name = ".".join(labels[:-1]) if len(labels) > 1 else host
    slug = re.sub(r"[^a-z0-9]+", "_", transliterate(name)).strip("_")
    return f"{prefix}{slug}"


def _normalize_site(url: str) -> str:
    """Сайт объекта = схема + хост (без пути): рецепт хранит именно его."""
    if "//" not in url:
        url = "https://" + url
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _page_url(site: str, page: str) -> str:
    return f"{site}/{page.lstrip('/')}" if page.strip("/") else f"{site}/"


def ascii_site(site: str) -> str:
    """Сайт для ЗАГОЛОВКОВ: кириллический домен -> punycode.

    Живой прогон 04.09 упал на смоларелакс.рф: HTTP-заголовок кодируется
    latin-1, и Referer с кириллицей роняет запрос ещё до сети
    (UnicodeEncodeError в http.client). В самом рецепте домен остаётся
    человеческим — его читают люди, а в headers идёт xn--…, который поймёт
    любой сервер.
    """
    parts = urllib.parse.urlsplit(site)
    try:
        host = parts.netloc.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        return site
    return f"{parts.scheme}://{host}"


# ---------------------------------------------------------------------------
# Транспорт: бюджет запросов, пауза по хосту, ретраев нет
# ---------------------------------------------------------------------------

class _Fetcher:
    """Обёртка над fetch: счётчик запросов, бюджет, журнал evidence.

    Бюджет — щадящий предохранитель разведки: 31 объект × десяток запросов
    легко превращается в нагрузку, за которую сайт вправе обидеться. Кончился
    бюджет — шаг не делается вовсе, и это честно попадает в missing_params.
    """

    def __init__(self, fetch: Callable, headers: dict,
                 budget: Optional[int] = None, site: Optional[str] = None):
        self._fetch = fetch
        self._headers = headers
        self.budget = budget
        self.made = 0
        self.evidence: list[dict] = []
        self.exhausted = False
        # «Нас не пустили» — отдельный признак, а не оттенок сбоя: по нему
        # тикет 12 отправляет объект к агенту с браузером, а не в «данных
        # нет навсегда». Взводит его ТОЛЬКО отказ САМОГО САЙТА объекта:
        # 403 на ru-ibe.tlintegration.ru — документированная ложная тревога
        # WAF Qrator (SKILL.md:84), и объект с живым сайтом уезжал по ней в
        # ветку «нужен браузер» вместо «hotel_code добыть вручную».
        self.refused = False
        self._site_host = (transport.host_of(transport.punycode_url(site))
                           if site else None)

    def _own_host(self, url: str) -> bool:
        """Отказал сам сайт объекта, а не сторонний API движка?

        Обе стороны приводятся к punycode (ревью 14.09.2026): страницы строятся
        из кириллического адреса, а хост сайта хранится в xn--, и у .рф-доменов
        сравнение раньше всегда давало False."""
        return (self._site_host is None
                or transport.host_of(transport.punycode_url(url)) == self._site_host)

    def get(self, url: str, what: str, payload: Optional[dict] = None):
        """-> (тело | None, причина отказа | None). Одна попытка, без ретраев."""
        if self.budget is not None and self.made >= self.budget:
            self.exhausted = True
            return None, f"{what}: исчерпан бюджет запросов ({self.budget})"
        self.made += 1
        try:
            if payload is None:
                status, body = self._fetch(url, self._headers)
            else:
                status, body = self._fetch(url, self._headers, payload)
        except transport.AccessRefused as e:
            # Подкласс OSError, и до волны 2 он читался как обрыв связи:
            # «хост просит подождать» и «сеть отвалилась» — разные исходы.
            self.refused = self.refused or self._own_host(url)
            self.evidence.append({"url": url, "what": what,
                                  "result": f"нас не пустили ({e})"})
            return None, f"{what}: нас не пустили ({e})"
        except transport.ResponseTooLarge as e:
            self.evidence.append({"url": url, "what": what,
                                  "result": f"тело ответа не читаем ({e})"})
            return None, f"{what}: {e}"
        except OSError as e:
            self.evidence.append({"url": url, "what": what,
                                  "result": f"сетевой сбой ({e})"})
            return None, f"{what}: сетевой сбой ({e})"
        body = body or ""
        antibot = looks_like_antibot(status, body, self._own_host(url))
        self.evidence.append({
            "url": url, "what": what,
            "result": antibot or f"HTTP {status}, {len(body)} знаков"})
        if antibot:
            self.refused = self.refused or self._own_host(url)
            return None, f"{what}: {antibot}"
        if status != 200:
            return None, f"{what}: HTTP {status}"
        return body, None


HOST_LOCK_NOTE = ("живой съём уже идёт на этой машине — разведка ходит на те "
                  "же чужие хосты своим процессом, а пауза >= 1.2 с к хосту "
                  "процессами не делится")


@contextlib.contextmanager
def host_lock(*, ignore: bool = False):
    """Замок живого съёма на время разведки. -> взят ли он (bool).

    ЗАЧЕМ. Пауза приличия живёт ВНУТРИ процесса (probes/_common), поэтому
    два процесса на одних и тех же хостах бьют их вдвое чаще правила. Замок
    один на машину (core.host_lock_path), и до волны 4 его брал только
    прогон пробников: разведчик шёл мимо. 04.09.2026 это уже случилось —
    разведка 54 самарских сайтов шла параллельно с прогонами агентов.

    ЦЕНА. Замок эксклюзивный, значит во время планового прогона (окно 06:31,
    до 90 минут) живая разведка невозможна — это сказано вслух здесь и в
    сообщении отказа. Такова и была цель: разведка и съём — два потока к
    одним хостам, и «подождать полтора часа» дешевле, чем попасть в бан на
    сайте кандидата в клиенты. Осознанный обход — --ignore-run-lock: он не
    отменяет вреда, но делает его видимым в журнале, а не молчаливым.

    Разбор УЖЕ снятых страниц (подменённый fetch) замка не берёт: в сеть он
    не ходит, запирать его не за что.
    """
    stack = contextlib.ExitStack()
    taken = True
    try:
        stack.enter_context(core.run_lock(core.host_lock_path(),
                                          note=HOST_LOCK_NOTE))
    except core.RunLockError as e:
        if not ignore:
            raise
        taken = False
        print(f"предупреждение: {e}. Идём мимо замка (--ignore-run-lock): "
              f"хост получит два потока запросов вместо одного",
              file=sys.stderr)
    with stack:
        yield taken


def make_fetch(pause_sec: Optional[float] = None) -> Callable:
    """Живой fetch разведки: (url, headers[, payload]) -> (status, текст).

    Механика вся из probes/_common (тикет 03), а не своя:
    - пауза по хосту из ОБЩЕГО трекера — иначе разведка и плановый прогон
      бьют один хост наперегонки, и каждый честно думает, что паузу выдержал;
    - тело читается потоком с потолком MAX_BODY_BYTES: живой прогон 04.09
      тянул страницы по 1.7 МБ целиком, а сайты разведка смотрит незнакомые;
    - просьба хоста подождать (429 + Retry-After) запоминается и уважается
      перед следующим запросом к нему (ретрая по-прежнему нет).
    Собственного режима у _common.make_fetch под «текст ИЛИ POST» нет,
    поэтому здесь собран тот же конвейер из тех же кирпичей — но ни одного
    своего значения таймаута, паузы и потолка.
    """
    import requests

    session = requests.Session()
    pause = transport.PAUSE_SEC if pause_sec is None else pause_sec

    def fetch(url: str, headers: dict, payload: Optional[dict] = None):
        # Очередь к хосту берётся ОБЩИМ замком (transport.host_turn) и на весь
        # запрос, включая чтение тела. Голого _wait_host_turn мало: он
        # выдерживает паузу, но соседа в неё не пускает замок, а не расчёт, —
        # два потока одновременно читают «последний запрос был давно» и бьют
        # чужой хост залпом (тикет 05). Разведка ходит по 31 незнакомому
        # сайту, то есть в самое рискованное для этого место.
        with transport.host_turn(url, pause):
            try:
                merged = {**transport.DEFAULT_HEADERS, **_ACCEPT, **headers}
                if payload is None:
                    resp = session.get(url, headers=merged,
                                       timeout=TIMEOUT_SEC, stream=True)
                else:
                    resp = session.post(url, json=payload, headers=merged,
                                        timeout=TIMEOUT_SEC, stream=True)
                transport._capture_body(resp)
            finally:
                # Отметка — в КОНЦЕ запроса, вместе с чтением тела: правило
                # «>= 1.2 с к одному хосту» меряется от конца ответа.
                transport._note_request(url)
            transport._note_retry_after(url, resp)
            return resp.status_code, resp.text

    return fetch


def unit_date_pairs(today: date, count: int) -> list[tuple[date, date]]:
    """2-3 пары «заезд-выезд» в РАЗНЫХ месяцах для справочника юнитов."""
    return [(today + timedelta(days=off), today + timedelta(days=off + 1))
            for off in UNIT_PAIR_OFFSETS[:max(0, count)]]


def _ddmmyyyy(d: date) -> str:
    return d.strftime("%d-%m-%Y")


# ---------------------------------------------------------------------------
# Добыча параметров по движкам
# ---------------------------------------------------------------------------

class Extract(NamedTuple):
    """Что разведка добыла по одному движку.

    units=None и units=0 — РАЗНОЕ и путать их нельзя: None значит «справочник
    не спрашивали», ноль — «спросили, и он оказался пуст» (живой прецедент:
    bronirui на 20.10 отдал ноль при четырёх домиках на сайте). Ветки
    доразведки тикета 12 у этих исходов разные.
    """

    params: dict
    problems: tuple = ()
    urls: tuple = ()
    units: Optional[int] = None
    excluded: tuple = ()          # «имя — почему» по каждой отсеянной позиции
    min_stay: Optional[int] = None
    # Имена параметров, которые движок обязан был отдать, а он не отдал, и
    # молчать об этом нельзя: они уходят в missing_params и роняют вердикт до
    # low. Пример — неопределённый минимальный срок у Bnovo: по нему пробник
    # выбирает ветку окон (тикет 07), и без него объект даёт тихие ~100%
    # занятости со статусом ok.
    unresolved: tuple = ()


# Лестница окон для проверки минимального срока проживания у Bnovo: сначала
# окно в 2 ночи, потом в 3. Дальше не идём — это уже не «минимальный срок»,
# а закрытое окно продаж, и гадать за движок разведка не должна.
MIN_STAY_SPANS = (2, 3)


def _params_travelline(html: str, site: str, fetcher: _Fetcher, opts: dict):
    params: dict = {}
    problems: list[str] = []
    urls: list[str] = []
    m = _RE_TL_CONTEXT.search(unescape(html))
    code = None
    if m:
        context = m.group(0)
        params["context"] = context
        url = (f"https://ru-ibe.tlintegration.ru/integration/profile/"
               f"{context}/ru")
        body, problem = fetcher.get(url, "профиль TL")
        urls.append(url)
        if body:
            try:
                code = find_provider(json.loads(body))
            except ValueError:
                code = None
            if code is None:
                m2 = _RE_TL_HOTEL_CODE.search(body)
                code = m2.group(1) if m2 else None
        if code is None:
            problems.append(
                f"hotel_code не добыт: {problem or 'в профиле TL нет provider'}")
    if code is None:
        m3 = _RE_TL_HOTEL_CODE.search(unescape(html))
        code = m3.group(1) if m3 else None
    units = None
    excluded: dict = {}
    if code:
        params["hotel_code"] = code
        if not opts["units"]:
            problems.append("справочник категорий не снимался (--no-units)")
        else:
            info_url = ("https://ru-ibe.tlintegration.ru/ApiWebDistribution/"
                        f"BookingForm/hotel_info?hotels[0].code={code}"
                        "&language=ru-ru")
            body, problem = fetcher.get(info_url, "справочник категорий TL")
            urls.append(info_url)
            if body is None:
                problems.append(f"справочник категорий не снят: {problem}")
            else:
                try:
                    hotels = json.loads(body).get("hotels") or []
                    room_types = (hotels[0].get("room_types") or []) if hotels \
                        else []
                    named = {str(rt.get("code") or i):
                             str(rt.get("name") or "")
                             for i, rt in enumerate(room_types)}
                    # Записи справочника передаём целиком: у TL там и лежат
                    # признаки самого модуля (тип позиции, вместимость), а
                    # они надёжнее догадки по имени.
                    items = {str(rt.get("code") or i): rt
                             for i, rt in enumerate(room_types)
                             if isinstance(rt, dict)}
                    kept, excluded = partition_units(named, items)
                    units = len(kept)
                except (ValueError, AttributeError, IndexError):
                    problems.append("справочник категорий TL: ответ не той "
                                    "формы — схема движка сменилась")
    return Extract(params, tuple(problems), tuple(urls), units,
                   excluded_lines(excluded))


class Ladder(NamedTuple):
    """Итог лестницы окон Bnovo: срок, снятые url, причина, найденные типы."""

    min_stay: Optional[int]
    urls: tuple
    problem: Optional[str]
    room_types: dict


def _bnovo_min_stay_ladder(uid: str, fetcher: _Fetcher, opts: dict) -> Ladder:
    """Окно в одну ночь ничего не показало -> расширяем окно.

    Зачем так можно. Заезд на две ночи требует обе ночи свободными, поэтому
    ночь, недоступную для одной ночи, нельзя продать и на две — если только
    отказ не был про минимальный срок проживания. Значит «пусто на 1 ночь,
    свободно на 2» это НЕ аншлаг, а минимум в 2 ночи (та же механика, на
    которой стоит ветка пробника _probe_bnovo_min_stay, разведка 15.08
    dacha_limerence).

    Со страниц лестницы забирается и СПРАВОЧНИК категорий: у объекта с
    минимальным сроком однночное окно рендерит пустую страницу, и до ревью
    волны 2 такой объект уходил с units_found=0 и пустым room_types, хотя
    окно пошире отдаёт и категории тоже.

    Ходим только GET-ом и только когда однночные окна ничего не показали: у
    объекта со свободными номерами лестница не запускается вовсе.
    """
    date_from = unit_date_pairs(opts["today"], 1)[0][0]
    urls: list[str] = []
    found: dict[str, str] = {}
    for span in MIN_STAY_SPANS:
        date_to = date_from + timedelta(days=span)
        url = (f"https://reservationsteps.ru/rooms/index/{uid}?lang=ru"
               f"&dfrom={_ddmmyyyy(date_from)}&dto={_ddmmyyyy(date_to)}")
        body, problem = fetcher.get(
            url, f"окно в {span} ноч. (минимальный срок)")
        urls.append(url)
        if body is None:
            return Ladder(None, tuple(urls),
                          f"минимальный срок не проверен: {problem}", found)
        found.update(parse_bnovo_room_types(body, keep_all=True))
        if parse_bnovo_free_units(body) > 0:
            return Ladder(span, tuple(urls), None, found)
    return Ladder(None, tuple(urls), (
        f"минимальный срок проживания не определён: на окнах 1-"
        f"{MIN_STAY_SPANS[-1]} ноч. свободных категорий нет — это может быть "
        f"и закрытое окно продаж, гадать за движок разведка не должна; "
        f"пробнику min_stay нужен (тикет 07), поэтому черновик не готов"),
        found)


def _params_bnovo(html: str, site: str, fetcher: _Fetcher, opts: dict):
    text = unescape(html)
    params: dict = {}
    problems: list[str] = []
    urls: list[str] = []
    m = _RE_BNOVO_UID.search(text) or _RE_BNOVO_UID_URL.search(text)
    if not m:
        return Extract(params)
    uid = m.group(1)
    params["uid"] = uid
    if not opts["units"]:
        problems.append("справочник категорий не снимался (--no-units)")
        return Extract(params, tuple(problems), tuple(urls))
    scanned = False          # хоть одна страница справочника снялась
    rest_seen = False        # на странице вообще были селекты с остатком
    free_seen = False        # хоть где-то есть свободный номер
    all_types: dict[str, str] = {}
    account_id: Optional[str] = None
    for date_from, date_to in unit_date_pairs(opts["today"],
                                              opts["unit_pairs"]):
        url = (f"https://reservationsteps.ru/rooms/index/{uid}?lang=ru"
               f"&dfrom={_ddmmyyyy(date_from)}&dto={_ddmmyyyy(date_to)}")
        body, problem = fetcher.get(url, "справочник категорий Bnovo")
        urls.append(url)
        if body is None:
            problems.append(f"справочник категорий не снят: {problem}")
            break
        scanned = True
        all_types.update(parse_bnovo_room_types(body, keep_all=True))
        available = parse_bnovo_availability(body)
        rest_seen = rest_seen or bool(available)
        free_seen = free_seen or sum(available.values()) > 0
        account_id = account_id or parse_account_id(body)
    if account_id:
        params["account_id"] = int(account_id)
    min_stay = None
    unresolved: tuple = ()
    if scanned and not free_seen:
        # Ни одного свободного номера на однночных окнах. Причин ровно три и
        # различить их со страницы нельзя: аншлаг, закрытое окно продаж и
        # минимальный срок проживания. Тикет 07 зовёт последнее системным
        # риском Bnovo (20 самарских объектов), поэтому спрашиваем окна
        # пошире. Условие нарочно шире прежнего (было «категории есть И
        # селекты с остатком есть»): ревью волны 2 показало, что страница без
        # селектов и страница без категорий молча уносили объект в вердикт
        # high с неопределённым минимальным сроком.
        if not rest_seen:
            problems.append(
                "остатки не читаются: на rooms/index нет ни одного селекта с "
                "data-available — свободные ночи по однночному окну не видны "
                "(вёрстка движка сменилась либо выбирать нечего)")
        ladder = _bnovo_min_stay_ladder(uid, fetcher, opts)
        urls.extend(ladder.urls)
        # Категории с окна пошире тоже наши: у объекта с минимальным сроком
        # однночное окно рендерит пустую страницу.
        all_types.update(ladder.room_types)
        min_stay = ladder.min_stay
        if ladder.problem:
            problems.append(ladder.problem)
        if min_stay is not None:
            params["min_stay"] = min_stay
        else:
            # Пробник читает min_stay из request.params и без него у такого
            # объекта красит занятыми все ночи горизонта — тихие ~100%
            # занятости со статусом ok. Черновик не готов, это решает агент.
            unresolved = ("min_stay",)
    room_types, excluded = partition_units(all_types)
    if room_types:
        params["room_types"] = room_types
    units = len(room_types) if scanned else None
    return Extract(params, tuple(problems), tuple(urls), units,
                   excluded_lines(excluded), min_stay, unresolved)


def _params_bronirui(html: str, site: str, fetcher: _Fetcher, opts: dict):
    text = unescape(html)
    params: dict = {}
    problems: list[str] = []
    urls: list[str] = []
    module_id = _bronirui_module_id(text)
    if module_id is None:
        return Extract(params)
    params["module_id"] = int(module_id)
    numbers: dict[str, dict] = {}
    raw_items: dict[str, dict] = {}
    scanned = False
    if not opts["units"]:
        problems.append("справочник номеров не снимался (--no-units)")
    elif not opts["allow_post"]:
        # Календарь bronirui без number_id отдаёт ВСЕ ночи закрытыми, то есть
        # без справочника рецепт нерабочий; а справочник живёт только за POST.
        problems.append("справочник номеров живёт за POST — нужен "
                        "--allow-post")
    else:
        url = "https://api.bronirui-online.ru/v2/numbers"
        for date_from, date_to in unit_date_pairs(opts["today"],
                                                  opts["unit_pairs"]):
            payload = {"module_id": int(module_id),
                       "widget_type": "booking-rooms",
                       "date_from": date_from.isoformat(),
                       "date_to": date_to.isoformat(), "adult_count": 2}
            body, problem = fetcher.get(url, "справочник номеров bronirui",
                                        payload)
            if url not in urls:
                urls.append(url)
            if body is None:
                problems.append(f"справочник номеров не снят: {problem}")
                break
            try:
                answer = json.loads(body)
                numbers = merge_numbers(numbers,
                                        parse_bronirui_numbers(answer))
                raw_items.update(parse_bronirui_items(answer))
            except ValueError:
                problems.append("справочник номеров: ответ не JSON — "
                                "схема движка сменилась")
                break
            scanned = True
    kept, excluded = partition_units(numbers, raw_items)
    if kept:
        params["numbers"] = kept
    units = len(kept) if scanned else None
    fund = sum(rec.get("rooms_count", 1) for rec in kept.values())
    if kept and fund != len(kept):
        # Один number_id держит несколько домиков (живая разведка 04.09 видела
        # rooms_count=3). Число домиков и число позиций справочника — разные
        # величины, и сверять с сайтом агент будет именно фонд.
        problems.append(f"позиций справочника {len(kept)}, домиков по фонду "
                        f"(rooms_count) {fund}")
    return Extract(params, tuple(problems), tuple(urls), units,
                   excluded_lines(excluded))


def _params_litepms(html: str, site: str, fetcher: _Fetcher, opts: dict):
    text = unescape(html)
    params: dict = {}
    problems: list[str] = []
    urls: list[str] = []
    m = _RE_LITEPMS_EMBED.search(text) or _RE_LITEPMS_URL.search(text)
    if not m:
        return Extract(params)
    property_id = m.group(1)
    params["property_id"] = property_id
    units = None
    if not opts["units"]:
        problems.append("справочник юнитов не снимался (--no-units)")
    else:
        url = (f"https://litepms.ru/widget/calendar?id={property_id}"
               "&mode=embed")
        body, problem = fetcher.get(url, "календарь litepms")
        urls.append(url)
        if body is None:
            problems.append(f"календарь не снят: {problem}")
        else:
            # Ноль здесь честный: страница снялась, а строк юнитов на ней нет.
            units = len(_RE_LITEPMS_ROOM.findall(body))
    return Extract(params, tuple(problems), tuple(urls), units)


def _homereserve_calendar_min_stay(token: str, apartment_id: str,
                                   fetcher: _Fetcher, opts: dict):
    """Минимальный срок из КАЛЕНДАРЯ одного домика -> (значение, url, причина).

    Почему не из справочника домиков: у живого объекта apartments отдаёт
    min_stay=null во ВСЕХ записях (фикстура apartments_bani_na_ozerah.json),
    а посуточный календарь того же объекта несёт min_stay=2 в каждом дне
    (calendar_bani_house.json). Спрашиваем один домик, а не все: смысл поля
    у HomeReserve объектный, а бить хост дюжиной POST ради одного числа —
    ровно та нагрузка, которой разведка избегает.
    """
    url = f"https://realtycalendar.ru/v2/widget/{token}/calendar"
    payload = {"begin_date": opts["today"].isoformat(),
               "end_date": (opts["today"] + timedelta(days=14)).isoformat(),
               "guests": {"adults": 2, "children": []},
               "apartment_id": int(apartment_id)}
    body, problem = fetcher.get(url, "календарь домика HomeReserve", payload)
    if body is None:
        return None, url, f"минимальный срок не снят: {problem}"
    try:
        days = json.loads(body).get("calendar") or []
    except (ValueError, AttributeError):
        return None, url, "календарь HomeReserve: ответ не той формы"
    stays = [d.get("min_stay") for d in days
             if isinstance(d, dict) and isinstance(d.get("min_stay"), int)
             and not isinstance(d.get("min_stay"), bool)]
    if not stays:
        return None, url, None
    return max(stays), url, None


def _bronirui_module_id(text: str) -> Optional[str]:
    """moduleId «Бронируй Онлайн» со страницы, или None.

    Авторитет — конфиг ВНУТРИ вызова znmsWidget.init. Запасной путь (единое
    значение moduleId на всей странице) годится только тогда, когда конфига
    в вызове не видно вовсе — init собран из переменной: если литерал конфига
    есть и модуля не называет, совпадение снаружи заведомо чужое (счётчик), а
    ложный id дороже отсутствия — черновик с ним уходит в реестр как готовый.
    """
    call = call_arguments(text, _RE_BRONIRUI_CALL)
    if call:
        m = _RE_MODULE_ID_KEY.search(call)
        if m:
            return m.group(1)
        if "{" in call:
            return None
    return unique_match(_RE_BRONIRUI_MODULE_LOOSE, text)


def homereserve_token(text: str) -> Optional[str]:
    """Токен HomeReserve со страницы, или None.

    Сначала вызов виджета (единственное место, где токен точно наш), потом
    запасной путь по адресу — и тот только при ЕДИНСТВЕННОМ кандидате, из
    которого выброшены пути самого движка. Ложный токен здесь дороже
    отсутствия: черновик с чужим значением уходит в реестр как готовый, а
    пробник получает 404 на чужой endpoint и метит рецепт broken — объект
    теряется по причине, которой на сайте нет (ревью волны 2).
    """
    call = call_arguments(text, _RE_HR_CALL)
    if call:
        m = _RE_TOKEN_KEY.search(call)
        if m:
            return m.group(1)
    candidates = {value for value in _RE_HR_TOKEN_URL.findall(text)
                  if value.lower() not in _HR_NOT_TOKENS}
    return next(iter(candidates)) if len(candidates) == 1 else None


def _params_homereserve(html: str, site: str, fetcher: _Fetcher, opts: dict):
    text = unescape(html)
    params: dict = {}
    problems: list[str] = []
    urls: list[str] = []
    token = homereserve_token(text)
    if token is None:
        return Extract(params, (
            "token не добыт: вызова initWidgetSearch на снятых страницах нет, "
            "а адрес скрипта движка (homereserve.ru/widget.js) токеном не "
            "считается",))
    params["token"] = token
    params["apartments_endpoint"] = (
        f"https://realtycalendar.ru/v2/widget/{token}/apartments")
    units = None
    excluded: dict = {}
    by_unit: dict[str, int] = {}
    apartment_ids: list[int] = []
    ids = _RE_HR_APARTMENTS.search(text)
    if ids:
        apartment_ids = [int(x) for x in ids.group(1).split(",") if x.strip()]
        if apartment_ids:
            # Список домиков лежит прямо в initWidgetSearch — лишний запрос за
            # тем же самым делать незачем.
            params["apartment_ids"] = apartment_ids
            units = len(apartment_ids)
    if apartment_ids and not (opts["units"] and opts["allow_post"]):
        # Честно про границу GET-ветки: в вызове виджета лежат ТОЛЬКО id, имён
        # позиций там нет, значит отсеять сертификат или услугу нечем. Молчать
        # об этом нельзя — белый список рецепта и есть знаменатель занятости.
        problems.append(
            f"белый список из {len(apartment_ids)} id взят из вызова виджета, "
            "имён позиций не видели — нежилые позиции не отсеяны (нужен "
            "--allow-post)")
    if opts["units"] and opts["allow_post"]:
        url = params["apartments_endpoint"]
        payload = {"begin_date": opts["today"].isoformat(),
                   "end_date": (opts["today"] + timedelta(days=1)).isoformat(),
                   "guests": {"adults": 2, "children": []},
                   "apartment_ids": [], "page": 1}
        body, problem = fetcher.get(url, "справочник домиков HomeReserve",
                                    payload)
        urls.append(url)
        if body is None:
            problems.append(f"справочник домиков не снят: {problem}")
        else:
            try:
                apartments = json.loads(body).get("apartments") or []
            except (ValueError, AttributeError):
                apartments = []
                problems.append("справочник домиков: ответ не той формы")
            named = {str(a.get("id")): str(a.get("title") or "")
                     for a in apartments if isinstance(a, dict)
                     and a.get("id") not in (None, "")}
            # Записи целиком: в них лежат признаки самого модуля (тип
            # позиции, вместимость), а они надёжнее догадки по имени.
            items = {str(a.get("id")): a for a in apartments
                     if isinstance(a, dict) and a.get("id") not in (None, "")}
            kept, excluded = partition_units(named, items)
            units = len(kept)
            kept_ids = [int(uid) for uid in kept if uid.isdigit()]
            if kept_ids:
                apartment_ids = kept_ids
            elif excluded:
                problems.append(
                    "справочник домиков: жилых позиций не осталось — белый "
                    "список рецепта оставлен как в вызове виджета, сверить "
                    "глазами")
            # Отсев обязан дойти до РЕЦЕПТА, а не только до черновика:
            # params.apartment_ids — тот самый белый список, по которому
            # пробник выбирает, что снимать (probes/homereserve.py:218), и он
            # же знаменатель занятости объекта. До ревью волны 3 здесь
            # переприсваивалась только локальная переменная, а в params
            # оставался неотфильтрованный список из HTML — сертификаты и
            # услуги молча занижали занятость (тот же класс, что «починка
            # справочника bronirui в бою была no-op»).
            if apartment_ids:
                params["apartment_ids"] = apartment_ids
            for a in apartments:
                if not isinstance(a, dict) or str(a.get("id")) not in kept:
                    continue
                stay = a.get("min_stay")
                if isinstance(stay, int) and not isinstance(stay, bool):
                    by_unit[str(a["id"])] = stay
        if not by_unit and apartment_ids:
            # apartments у живого объекта молчит про минимальный срок —
            # спрашиваем календарь одного домика.
            stay, cal_url, why = _homereserve_calendar_min_stay(
                token, str(apartment_ids[0]), fetcher, opts)
            urls.append(cal_url)
            if why:
                problems.append(why)
            if stay is not None:
                by_unit[str(apartment_ids[0])] = stay
                problems.append(
                    f"минимальный срок {stay} ноч. снят по календарю домика "
                    f"{apartment_ids[0]}; у остальных домиков не спрашивали")
    min_stay = None
    if by_unit:
        params["min_stay_by_unit"] = by_unit
        # Скаляр рецепта — САМОЕ строгое ограничение объекта: пробник по нему
        # выбирает ветку окон, и минимум в 1 ночь у одного домика не должен
        # выключать её для домика с минимумом в 2 (тикет 07). Поюнитные
        # значения при этом не теряются — они рядом, в min_stay_by_unit.
        min_stay = max(by_unit.values())
        if len(set(by_unit.values())) > 1:
            problems.append(
                "минимальный срок у домиков разный "
                + ", ".join(f"{uid}: {stay} ноч."
                            for uid, stay in sorted(by_unit.items()))
                + " — в рецепт пошёл самый строгий")
    return Extract(params, tuple(problems), tuple(urls), units,
                   excluded_lines(excluded), min_stay)


def _params_uhotels(html: str, site: str, fetcher: _Fetcher, opts: dict):
    text = unescape(html)
    params: dict = {}
    m = _RE_UHOTELS_HOTEL.search(text)
    hotel = m.group(1) if m else unique_match(_RE_UHOTELS_TOKEN, text)
    if hotel is None:
        return Extract(params)
    params["hotel"] = hotel
    params["lang"] = "ru"
    params["currency"] = "RUB"
    # Ночь снимается двумя окнами (1 и 2 ночи): движок отвечает «можно ли
    # забронировать такой заезд», и однночный запрос на объекте с минимумом в
    # две ночи даёт пустой список — первая редакция пробника прочла это как
    # аншлаг (аудит 16.08).
    params["spans"] = [1, 2]
    # Категории приходят тем же ответом, что и доступность, — отдельный
    # справочник юнитов рецепту не нужен.
    return Extract(params)


_PARAM_EXTRACTORS = {
    "travelline": _params_travelline,
    "bnovo": _params_bnovo,
    "bronirui": _params_bronirui,
    "litepms": _params_litepms,
    "homereserve": _params_homereserve,
    "uhotels": _params_uhotels,
}


# ---------------------------------------------------------------------------
# Черновик рецепта (SCHEMA (а) occupancy_core)
# ---------------------------------------------------------------------------
_REQUEST_TEMPLATES = {
    "travelline": {
        "url_template": (
            "https://ru-ibe.tlintegration.ru/ApiWebDistribution/"
            "AvailabilityCalendar/room_type_availability_2?aggregate_dates="
            "false&currency=RUB&max_nights=1&hotel={hotel_code}&shared=false"
            "&start_date={date_from}&end_date={date_to}"),
        "method": "GET",
        "date_substitution": (
            "start_date/end_date = YYYY-MM-DD, один GET на весь горизонт; "
            "пробник сам добавляет BookingForm/hotel_info (справочник "
            "категорий + окно продаж) и AvailabilityCalendar/"
            "hotel_booking_rules (агрегат для пустых ночей)"),
    },
    "bnovo": {
        "url_template": ("https://public-api.reservationsteps.ru/v1/api/"
                         "min_prices?uid={uid}&dfrom={date_from}&dto={date_to}"
                         "&room_type_id={room_type_id}"),
        "method": "GET",
        "date_substitution": (
            "dfrom/dto = DD-MM-YYYY; один GET на каждую категорию из "
            "params.room_types (подстановка room_type_id); ответ — даты ISO"),
    },
    "bronirui": {
        "url_template": "https://api.bronirui-online.ru/v2/widget/calendar",
        "method": "POST",
        "date_substitution": (
            "dateFrom/dateTo = YYYY-MM-DD в JSON-теле POST; один POST на "
            "каждый номер из params.numbers (подстановка number_id); поля "
            "тела snake_case (module_id), ответ — dates.elements{дата ISO}"),
    },
    "litepms": {
        "url_template": ("https://litepms.ru/widget/calendar?id={property_id}"
                         "&mode=embed&d={month_anchor}"),
        "method": "GET",
        "date_substitution": (
            "month_anchor = 01-MM-YYYY первого месяца страницы; каждая "
            "страница несёт 3 месяца всех юнитов, пробник шагает якорями по "
            "3 месяца до покрытия date_from..date_to"),
    },
    "homereserve": {
        "url_template": "https://realtycalendar.ru/v2/widget/{token}/calendar",
        "method": "POST",
        "date_substitution": (
            "begin_date/end_date = YYYY-MM-DD. Два POST с телом JSON: "
            "(1) apartments {begin_date, end_date, guests, apartment_ids:[], "
            "page} -> список домиков; (2) calendar {begin_date, end_date, "
            "guests, apartment_id} -> посуточный массив на каждый домик"),
    },
    "uhotels": {
        "url_template": "https://api.uhotels.app/api/widget/v1/booking/rooms",
        "method": "POST",
        "date_substitution": (
            "dateIn = ночь YYYY-MM-DD, dateOut = ночь + длина окна; окна "
            "берутся из params.spans (1 и 2 ночи), то есть на каждую ночь "
            "горизонта два POST. Ответ — список категорий, поле max = сколько "
            "домиков доступно на ВЕСЬ отрезок"),
    },
}

_HEADERS_BY_ENGINE = {
    # У TravelLine Origin стоит в 8 живых рецептах из 10 (сверено с реестром
    # скилла 04.09; без Origin — kuzminskoeglamp.ru и ok-reka.ru) — WAF Qrator
    # его ждёт (SKILL.md:84). Прежние «10 из 12» не сходились ни одним числом:
    # рецептов TravelLine десять, всего в реестре 25.
    "travelline": lambda site: {"Referer": f"{site}/", "Origin": site},
    "bronirui": lambda site: {"Referer": f"{site}/", "Origin": site},
    "homereserve": lambda site: {"Referer": f"{site}/", "Origin": site,
                                 "Content-Type": "application/json"},
    "uhotels": lambda site: {"Referer": "https://api.uhotels.app/widget/"
                                        "booking2/",
                             "Origin": "https://api.uhotels.app"},
}


def _headers_for(engine: str, site: str) -> dict:
    site = ascii_site(site)  # заголовки кодируются latin-1, IDN туда нельзя
    maker = _HEADERS_BY_ENGINE.get(engine)
    if maker is not None:
        return maker(site)
    # Умолчание — ОДИН Referer, как в живых рецептах реестра: все четыре
    # рабочих bnovo (a_ureki, chekhovapi, wood_glamp, dacha_limerence) и
    # единственный litepms (dachavsosnah) обходятся им. Лишний Origin на GET
    # к API за WAF — ровно тот класс отличий, который даёт 403 у одного
    # объекта при зелёных тестах у всех (ревью волны 2).
    return {"Referer": f"{site}/"}


def build_draft(engine: str, site: str, params: dict, *, status: str,
                notes: str, source_urls: list, discovered_at: str,
                min_stay: Optional[int],
                broken_reason: Optional[str] = None) -> dict:
    """Черновик рецепта в SCHEMA (а) — как есть, без переделки пробником."""
    template = _REQUEST_TEMPLATES.get(engine)
    params = dict(params)
    # Минимальный срок пишется в ДВА места нарочно: наверху его читает человек
    # и тикет 12, а пробник берёт его только из request.params
    # (probes/travelline.py:343) — пока значение лежало лишь наверху, до
    # пробника оно не доходило ни при каком раскладе.
    if min_stay is not None:
        params.setdefault("min_stay", min_stay)
    draft = {
        "site": site,
        "engine": engine,
        "status": status,
        "request": {
            "url_template": template["url_template"] if template else "",
            "method": template["method"] if template else "GET",
            "params": params,
            "headers": _headers_for(engine, site),
            "date_substitution": (template["date_substitution"]
                                  if template else ""),
        },
        "discovered_at": discovered_at,
        # Поле тикета 07: пробник должен знать минимальный срок проживания,
        # иначе «нельзя начать заезд» читается как «занято». Движки, кроме
        # HomeReserve, его наружу не отдают — тогда честный null.
        "min_stay": min_stay,
        "notes": notes,
        "source_urls": source_urls,
    }
    if broken_reason:
        draft["broken_reason"] = broken_reason
    return draft


# ---------------------------------------------------------------------------
# Разведка объекта
# ---------------------------------------------------------------------------

def scout(url: str, fetch: Callable, *, pages: tuple = DEFAULT_PAGES,
          units: bool = True, allow_post: bool = False,
          unit_pairs: int = 2, budget: Optional[int] = None,
          today: Optional[date] = None, key: Optional[str] = None,
          now: Optional[str] = None) -> dict:
    """Сайт -> {verdict, engine, recipe_draft, confidence, evidence, ...}.

    fetch(url, headers[, payload]) -> (status, текст). Сеть трогает только он;
    вся логика — чистая, поэтому тесты живут на фикстурах.
    """
    today = today or date.today()
    now = now or datetime.now().astimezone().isoformat(timespec="seconds")
    site = _normalize_site(url)
    fetcher = _Fetcher(fetch, {"Referer": f"{ascii_site(site)}/"}, budget,
                       site=ascii_site(site))
    result = {
        "key": key or site_key(site),
        "site": site,
        "scouted_at": now,
        "verdict": "refuse",
        "engine": None,
        "engines_found": [],
        "confidence": None,
        "units_found": None,
        # Нежилые позиции модуля (палаточные места, сертификаты): в юниты они
        # не идут, но названы вслух — иначе агент не отличит «модуль отдал
        # 16 домиков» от «мы выкинули 36 позиций».
        "units_excluded": 0,
        "excluded_units": [],
        "min_stay": None,
        "missing_params": [],
        "note": "",
        "requests_made": 0,
        # «Нас не пустили» отдельным признаком: по нему тикет 12 отправляет
        # объект к агенту с браузером, а не в «данных нет навсегда».
        "access_refused": False,
        "evidence": fetcher.evidence,
        "refusal_reason": None,
        "recipe_draft": None,
    }

    # 1. Страницы. Смотрим ВСЕ настроенные, а не до первого маркера: живой
    #    виджет часто стоит на /booking, а на главной висит мёртвый хвост.
    found: dict[str, dict] = {}
    html_by_url: dict[str, str] = {}
    problems: list[str] = []
    for page in pages:
        page_url = _page_url(site, page)
        body, problem = fetcher.get(page_url, f"страница {page or '/'}")
        if body is None:
            problems.append(problem)
            continue
        html_by_url[page_url] = body
        for hit in detect_engines(body):
            known = found.get(hit["engine"])
            if known is None or (known["strength"] == "weak"
                                 and hit["strength"] == "strong"):
                found[hit["engine"]] = {**hit, "url": page_url}
    result["requests_made"] = fetcher.made
    result["access_refused"] = fetcher.refused

    if not html_by_url:
        result["refusal_reason"] = "; ".join(problems) or "страницы не снялись"
        return result

    result["engines_found"] = sorted(found)
    for hit in found.values():
        result["evidence"].append({
            "url": hit["url"], "what": f"маркер движка {hit['engine']}",
            "result": f"{hit['marker']} ({hit['strength']})"})

    strong = [e for e, hit in found.items() if hit["strength"] == "strong"]
    if len(strong) > 1:
        # Решение Р5: два сильных маркера — всегда вердикт агента. Один из них
        # может быть мёртвым (pineriver), и машина этого не отличает.
        result["refusal_reason"] = (
            "два сильных маркера разных движков — "
            + ", ".join(f"{e} ({found[e]['marker']})" for e in sorted(strong))
            + "; какой из них живой, решает агент (SKILL.md, прецедент "
              "pineriver)")
        return result
    if not found:
        scanned = ", ".join(sorted(html_by_url))
        if fetcher.refused:
            # Причина обязана называть отказ хоста ПЕРВЫМ: 403 на /booking при
            # живой главной раньше был виден только в evidence, а в причине
            # стояло «маркеров нет» — то есть объект за антиботом уезжал в
            # «данных нет навсегда» вместо ветки доразведки браузером
            # (тикет 12).
            result["refusal_reason"] = (
                "нас не пустили: " + "; ".join(problems)
                + f"; на снятых страницах ({scanned}) маркеров модуля "
                  "бронирования нет")
        elif problems:
            # 404 на /bron у сайта без такой страницы — обычное дело, поэтому
            # главной причиной остаётся отсутствие маркеров, а неснятые
            # страницы идут припиской: агент должен видеть, что смотрели
            # не всё.
            result["refusal_reason"] = (
                "маркеров модуля бронирования нет на снятых страницах "
                + scanned + "; не снялись: " + "; ".join(problems))
        else:
            result["refusal_reason"] = (
                "маркеров модуля бронирования нет ни на одной странице "
                + scanned)
        return result
    if not strong and len(found) > 1:
        # Р5 спеки: неоднозначность решает агент. Раньше выбор между двумя
        # слабыми маркерами делался молча по алфавиту (b < t), и в черновик
        # уходил движок, которого на объекте может не быть вовсе.
        result["refusal_reason"] = (
            "два слабых маркера разных движков — "
            + ", ".join(f"{e} ({found[e]['marker']})" for e in sorted(found))
            + "; сильного маркера (живого виджета) нет ни у одного, какой из "
              "них настоящий, решает агент")
        return result

    engine = strong[0] if strong else sorted(found)[0]
    hit = found[engine]
    result["engine"] = engine
    result["confidence"] = hit["strength"]
    # Параметры ищем по ТЕКСТУ ВСЕХ снятых страниц, а не только по той, где
    # нашёлся маркер: самая частая живая раскладка — скрипт виджета в шапке
    # всех страниц, а вызов с uid на /booking (SKILL.md:94). Страница маркера
    # идёт первой, чтобы при совпадениях побеждала она.
    html = "\n".join([html_by_url[hit["url"]]]
                     + [body for url, body in html_by_url.items()
                        if url != hit["url"]])
    source_urls = [hit["url"]] + [url for url in html_by_url
                                  if url != hit["url"]]

    # 2. Параметры. Движок без пробника v1 параметров не имеет — черновик
    #    сразу broken (прецедент реестра lafa_club).
    opts = {"units": units, "allow_post": allow_post, "today": today,
            "unit_pairs": unit_pairs}
    if engine in _PARAM_EXTRACTORS:
        got = _PARAM_EXTRACTORS[engine](html, site, fetcher, opts)
    else:
        got = Extract({})
    params, why = got.params, list(got.problems)
    units_found, min_stay = got.units, got.min_stay
    result["requests_made"] = fetcher.made
    result["access_refused"] = fetcher.refused
    result["units_found"] = units_found
    result["units_excluded"] = len(got.excluded)
    result["excluded_units"] = list(got.excluded)
    result["min_stay"] = min_stay
    source_urls.extend(got.urls)

    # missing_params — ГОЛЫЕ имена: их читает тикет 12, раскладывая объекты по
    # веткам доразведки. Человеческое «почему» живёт рядом, в note, иначе
    # машинная ветка сверяет строку с объяснением вместо имени параметра.
    required = REQUIRED_PARAMS.get(engine, ())
    absent = [name for name in required if not params.get(name)]
    # Неразрешённое (Extract.unresolved) идёт туда же: параметр, который движок
    # обязан был отдать по обстоятельствам объекта, но не отдал, — это такая же
    # незаконченная разведка, как отсутствующий uid. Пример — минимальный срок
    # у Bnovo без единой свободной ночи (тикет 07).
    absent += [name for name in got.unresolved if name not in absent]
    result["missing_params"] = absent
    if fetcher.exhausted:
        why = why + [f"исчерпан бюджет запросов ({budget})"]
    result["note"] = "; ".join(why)

    supported = engine in SUPPORTED_ENGINES
    if supported and not absent:
        verdict, status, broken_reason = "high", "ok", None
    elif supported:
        verdict, status = "low", "broken"
        broken_reason = ("черновик разведки: не добыты параметры "
                         + ", ".join(absent)
                         + (f" ({result['note']})" if result["note"] else ""))
    else:
        verdict, status = "low", "broken"
        broken_reason = f"движок {engine} не поддержан пробником v1"
    result["verdict"] = verdict

    # Ноль юнитов и «не снималось» — РАЗНЫЕ ветки доразведки: справочник,
    # ответивший пусто (окно продаж закрыто, всё продано), и справочник, о
    # котором мы не спрашивали, требуют от агента разного.
    units_text = ("не снималось" if units_found is None
                  else f"{units_found}")
    notes = (f"ЧЕРНОВИК scout от {now[:10]}, пробником НЕ проверен. "
             f"Движок опознан по маркеру {hit['marker']!r} ({hit['strength']}) "
             f"на {hit['url']}. Юнитов найдено: {units_text} — "
             "сверка с числом домиков на сайте за агентом (справочник движка "
             "отдаёт только доступные на запрошенные даты). Рецепт попадает в "
             "реестр только после успешного прогона пробником.")
    if got.excluded:
        shown = list(got.excluded)[:5]
        notes += (f" Отсеяно нежилых позиций: {len(got.excluded)} ("
                  + ", ".join(shown)
                  + (", …" if len(got.excluded) > len(shown) else "")
                  + ") — они продаются иначе, чем домики, и в знаменатель "
                    "занятости не идут; сверка за агентом.")
    if min_stay is not None:
        notes += f" Минимальный срок проживания: {min_stay} ноч."
    if result["missing_params"]:
        notes += (" Не добыто: " + ", ".join(result["missing_params"])
                  + (f" ({result['note']})" if result["note"] else "") + ".")
    elif result["note"]:
        notes += f" Замечания разведки: {result['note']}."
    result["recipe_draft"] = build_draft(
        engine, site, params, status=status, notes=notes,
        source_urls=source_urls, discovered_at=now, min_stay=min_stay,
        broken_reason=broken_reason)
    return result


def write_draft(result: dict, out_dir) -> Path:
    """Черновик на диск: <out_dir>/<ключ>.json. В реестр не пишем никогда."""
    path = Path(out_dir) / f"{result['key']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def summary_line(result: dict, path: Path) -> str:
    """Человеческая строка исхода — то, что читает оператор в терминале."""
    head = (f"{result['key']}: {result['verdict']} "
            f"движок={result['engine'] or '—'} "
            f"запросов={result['requests_made']}")
    if result["verdict"] == "refuse":
        return f"{head} — {result['refusal_reason']} -> {path}"
    tail = ""
    if result["units_found"] is not None:
        tail += f" юнитов={result['units_found']}"
    if result["missing_params"]:
        tail += " не добыто: " + ", ".join(result["missing_params"])
    if result["note"]:
        tail += f" ({result['note']})"
    return f"{head}{tail} -> {path}"


def main(argv=None, fetch: Optional[Callable] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scout",
        description="Разведка рецепта съёма: сайт -> движок -> черновик "
                    "рецепта (в реестр НЕ пишет).")
    parser.add_argument("--url", action="append", default=[], required=True,
                        metavar="URL", help="сайт объекта (можно несколько)")
    parser.add_argument("--key", default=None,
                        help="ключ цели (по умолчанию smr_<транслит домена>); "
                             "осмысленно только с одним --url")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR),
                        help="куда класть черновики (дефолт: "
                             "agent-runtime/.../occupancy/scout-out)")
    parser.add_argument("--pages", default=",".join(DEFAULT_PAGES),
                        help="страницы через запятую (дефолт: главная, "
                             "booking, bron); пустой элемент = главная")
    parser.add_argument("--no-units", action="store_true",
                        help="не собирать справочник юнитов (экономит запросы, "
                             "но у bnovo и bronirui рецепт останется неполным)")
    parser.add_argument("--allow-post", action="store_true",
                        help="разрешить POST-шаги справочников (bronirui, "
                             "homereserve); по умолчанию разведка только GET")
    parser.add_argument("--unit-pairs", type=int, default=2,
                        help="сколько пар дат в разных месяцах для союза "
                             "справочника юнитов (1-3, дефолт 2)")
    parser.add_argument("--budget", type=int, default=None,
                        help="максимум запросов на объект (щадящий режим)")
    parser.add_argument("--today", default=None,
                        help="подменить «сегодня» (YYYY-MM-DD) — для тестов")
    parser.add_argument("--ignore-run-lock", action="store_true",
                        help="идти в сеть, даже когда на машине идёт живой "
                             "съём. Хост получит два потока запросов вместо "
                             "одного — обход громкий и осознанный")
    args = parser.parse_args(argv)

    pages = tuple(p.strip() for p in args.pages.split(","))
    try:
        today = date.fromisoformat(args.today) if args.today else date.today()
    except ValueError:
        print("ошибка: --today не YYYY-MM-DD", file=sys.stderr)
        return 2
    if args.key and len(args.url) > 1:
        print("ошибка: --key осмыслен только с одним --url", file=sys.stderr)
        return 2

    # Замок берётся только на ЖИВОЙ разведке: подменённый fetch (тесты,
    # разбор сохранённых страниц) в сеть не ходит и чужим хостам не мешает.
    with contextlib.ExitStack() as machine:
        if fetch is None:
            try:
                machine.enter_context(host_lock(ignore=args.ignore_run_lock))
            except core.RunLockError as e:
                print(f"ошибка: {e}. Разведка подождёт конца прогона "
                      f"(или --ignore-run-lock, если знаете, что делаете)",
                      file=sys.stderr)
                return 2
        live = fetch or make_fetch()
        for url in args.url:
            result = scout(url, live, pages=pages, units=not args.no_units,
                           allow_post=args.allow_post,
                           unit_pairs=args.unit_pairs, budget=args.budget,
                           today=today, key=args.key)
            path = write_draft(result, args.out_dir)
            print(summary_line(result, path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
