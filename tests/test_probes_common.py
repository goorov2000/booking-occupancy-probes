# -*- coding: utf-8 -*-
"""Общий транспорт пробников (probes/_common): пауза по хосту, каскад ошибок.

Пауза тестируется БЕЗ сети и без реального сна: время и sleep — фейки
(monkeypatch), сессия requests подменена. Ключевая находка ревью 14.08:
трекер последнего запроса живёт на уровне МОДУЛЯ, поэтому пауза >= 1.2 с
держится и МЕЖДУ разными fetch — два объекта одного движка подряд больше
не бьют общий хост (ibe.tlintegration.ru) без паузы.

Правка 04.09.2026 (тикет 03): у паузы появился джиттер, и тело ответа
читается потоком с потолком. Поэтому ожидания здесь сравниваются не с
точным PAUSE_SEC, а с вилкой [PAUSE_SEC, PAUSE_SEC+PAUSE_JITTER_SEC],
а фейк ответа отдаёт тело через iter_content, как настоящий requests.
Джиттер и потолок проверяются отдельно — tests/test_transport_resilience.py.
"""
import pytest
import requests

from probes import _common


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, sec):
        self.sleeps.append(sec)
        self.now += sec


class FakeResponse:
    """Тело отдаётся потоком: транспорт читает его сам, с потолком."""

    status_code = 200
    headers = {}
    encoding = "utf-8"

    def __init__(self):
        self._content = b""

    def iter_content(self, size):
        yield "тело страницы".encode("utf-8")

    def close(self):
        pass

    @property
    def text(self):
        return self._content.decode("utf-8")

    def json(self):
        return {"ok": True}


class FakeSession:
    def get(self, url, headers=None, timeout=None, stream=False):
        return FakeResponse()

    def post(self, url, json=None, headers=None, timeout=None, stream=False):
        return FakeResponse()


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(_common.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(_common.time, "sleep", fake.sleep)
    monkeypatch.setattr(_common, "_last_request_by_host", {})
    monkeypatch.setattr(_common, "_host_not_before", {})
    monkeypatch.setattr(requests, "Session", FakeSession)
    return fake


def pause_range(elapsed=0.0):
    """Вилка ожидания с джиттером: от PAUSE_SEC до PAUSE_SEC+джиттер."""
    return (_common.PAUSE_SEC - elapsed,
            _common.PAUSE_SEC + _common.PAUSE_JITTER_SEC - elapsed)


# ---------------------------------------------------------------------------
# Пауза по хосту
# ---------------------------------------------------------------------------

def test_pause_holds_between_different_fetchers_same_host(clock):
    """Два объекта подряд (два make_fetch) к одному хосту — пауза держится."""
    first = _common.make_fetch("get_json")
    second = _common.make_fetch("get_json")
    first("https://ibe.tlintegration.ru/api/object1", {})
    second("https://ibe.tlintegration.ru/api/object2", {})
    low, high = pause_range()
    assert len(clock.sleeps) == 1 and low <= clock.sleeps[0] <= high


def test_no_pause_between_different_hosts(clock):
    fetch = _common.make_fetch("get_json")
    fetch("https://a.example/x", {})
    fetch("https://b.example/x", {})
    assert clock.sleeps == []


def test_pause_is_only_the_remainder(clock):
    """Между запросами прошло 0.5 с — досыпаем только недостающие 0.7 с."""
    fetch = _common.make_fetch("get_json")
    fetch("https://a.example/x", {})
    clock.now += 0.5
    fetch("https://a.example/y", {})
    low, high = pause_range(0.5)
    assert len(clock.sleeps) == 1 and low <= clock.sleeps[0] <= high
    clock.sleeps.clear()
    clock.now += _common.PAUSE_SEC + 1  # пауза уже выдержана сама
    fetch("https://a.example/z", {})
    assert clock.sleeps == []


def test_host_noted_even_when_request_raises(clock, monkeypatch):
    """Сбой запроса тоже трогает хост — следующий запрос ждёт паузу."""
    class FailingSession:
        def get(self, url, headers=None, timeout=None, stream=False):
            raise OSError("обрыв")

    monkeypatch.setattr(requests, "Session", FailingSession)
    broken_fetch = _common.make_fetch("get_json")
    with pytest.raises(OSError):
        broken_fetch("https://a.example/x", {})
    monkeypatch.setattr(requests, "Session", FakeSession)
    fetch = _common.make_fetch("get_json")
    fetch("https://a.example/y", {})
    low, high = pause_range()
    assert len(clock.sleeps) == 1 and low <= clock.sleeps[0] <= high


# ---------------------------------------------------------------------------
# Три варианта транспорта
# ---------------------------------------------------------------------------

def test_fetch_modes_shapes(clock, monkeypatch):
    seen = {}

    class RecordingSession:
        def get(self, url, headers=None, timeout=None, stream=False):
            seen["get"] = (url, timeout)
            return FakeResponse()

        def post(self, url, json=None, headers=None, timeout=None,
                 stream=False):
            seen["post_payload"] = json
            seen["post_timeout"] = timeout
            return FakeResponse()

    monkeypatch.setattr(requests, "Session", RecordingSession)
    post = _common.make_fetch("post_json")
    assert post("https://a.example/x", {}, {"module_id": 1}) == (200, {"ok": True})
    assert seen["post_payload"] == {"module_id": 1}
    assert seen["post_timeout"] == _common.TIMEOUT_SEC

    text_fetch = _common.make_fetch("get_text")
    assert text_fetch("https://b.example/y", {}) == (200, "тело страницы")
    assert seen["get"][1] == _common.TIMEOUT_SEC

    with pytest.raises(ValueError):
        _common.make_fetch("delete_json")


# ---------------------------------------------------------------------------
# Каскад ошибок
# ---------------------------------------------------------------------------

def test_classify_http_ok():
    assert _common.classify_http(200, True, "x") == (None, None)


def test_classify_http_4xx_is_broken_with_hint_and_detail():
    broken, net = _common.classify_http(
        401, True, "юнит", detail="Отель временно не принимает бронирования")
    assert net is None
    assert "401" in broken and "не принимает бронирования" in broken
    broken, net = _common.classify_http(403, True, "юнит", hint="антибот?")
    assert net is None and broken.endswith("антибот?")


def test_classify_http_bad_body_is_broken():
    broken, net = _common.classify_http(200, False, "юнит")
    assert net is None and "не JSON" in broken


def test_classify_http_5xx_is_network_not_broken():
    broken, net = _common.classify_http(502, True, "юнит")
    assert broken is None and "502" in net


# ---------------------------------------------------------------------------
# Версия пробника и ключ хоста (поля снапшота и группировки прогона)
# ---------------------------------------------------------------------------

def test_probe_version_comes_from_the_engine_module():
    """Версия РАЗБОРА объявляется модулем движка, а не транспортом.

    Иначе снимки до и после смены разбора подписываются одинаково, и в ряду
    сезонности «снято старым кодом» неотличимо от «снято новым».
    """
    class Module:
        PROBE_VERSION = 7

    assert _common.probe_version_of("uhotels", Module) == "uhotels@7"


def test_probe_version_falls_back_to_the_transport_version():
    class Module:            # движок своей версии ещё не объявил
        pass

    assert (_common.probe_version_of("litepms", Module)
            == f"litepms@{_common.PROBE_VERSION}")


def test_host_of_is_the_key_of_the_pause_and_of_the_grouping():
    """По этому ключу CLI строит очереди: один хост — одна последовательная."""
    assert (_common.host_of("https://ibe.tlintegration.ru/api/x?d=1")
            == "ibe.tlintegration.ru")
    assert _common.host_of("") == ""


# ---------------------------------------------------------------------------
# Кириллица в URL и заголовках: punycode ставит транспорт, а не рецепт
# ---------------------------------------------------------------------------
# Живая проверка 05.09.2026 00:00: smr_mesto_schastya (местосчастья.рф) упал
# с «UnicodeEncodeError: 'latin-1' codec can't encode characters in position
# 0-9» — http.client кодирует заголовки latin-1, а рецепт нёс кириллическое
# значение. Пробник получил insufficient_data из-за одного знака в реестре.
# Транспорт обязан пережить любой рецепт: хост в URL и в URL-заголовках
# уходит как xn--…, а значение, которое на провод не положить вовсе,
# выбрасывается (ниже него лежит умолчание DEFAULT_HEADERS).

IDN_SITE = "https://местосчастья.рф"
IDN_PUNY = "https://xn--80ajujobbee1c4cub.xn--p1ai"


class WireStrictSession:
    """Сессия, которая, как http.client, не примет ни одного не-latin-1 знака."""

    def __init__(self, seen):
        self.seen = seen

    def _check(self, url, headers):
        for name, value in headers.items():
            value.encode("latin-1")  # ровно здесь падал живой прогон
        self.seen["url"] = url
        self.seen["headers"] = dict(headers)

    def get(self, url, headers=None, timeout=None, stream=False):
        self._check(url, headers)
        return FakeResponse()

    def post(self, url, json=None, headers=None, timeout=None, stream=False):
        self._check(url, headers)
        self.seen["payload"] = json
        return FakeResponse()


@pytest.fixture
def wire(clock, monkeypatch):
    seen = {}
    monkeypatch.setattr(requests, "Session", lambda: WireStrictSession(seen))
    return seen


def test_idn_url_and_url_headers_go_to_the_wire_as_punycode(wire):
    """Кириллический хост в url, Referer и Origin уходит как xn--…; тело POST
    не трогается — там кириллица законна (JSON в UTF-8)."""
    fetch = _common.make_fetch("get_json")
    payload = {"name": "Дом у озера", "n": 1}
    status, data = fetch(f"{IDN_SITE}/api/calendar?d=01-10-2026",
                         {"Referer": f"{IDN_SITE}/", "Origin": IDN_SITE},
                         payload)
    assert (status, data) == (200, {"ok": True})
    assert wire["url"] == f"{IDN_PUNY}/api/calendar?d=01-10-2026"
    assert wire["headers"]["Referer"] == f"{IDN_PUNY}/"
    assert wire["headers"]["Origin"] == IDN_PUNY
    assert wire["payload"] == payload
    # умолчания транспорта под рецептом остаются
    assert wire["headers"]["User-Agent"] == _common.DEFAULT_HEADERS["User-Agent"]


def test_header_value_that_cannot_go_to_the_wire_is_dropped_not_fatal(wire):
    """Рецепт smr_mesto_schastya нёс "User-Agent": "браузерный" — слово-
    заглушку вместо строки браузера. Такое значение не кодируется никак:
    его выбрасываем, и на провод идёт умолчание транспорта, а не падение."""
    fetch = _common.make_fetch("get_json")
    fetch("https://litepms.ru/widget/calendar?id=12001",
          {"Referer": f"{IDN_PUNY}/", "User-Agent": "браузерный"})
    assert wire["headers"]["User-Agent"] == _common.DEFAULT_HEADERS["User-Agent"]
    assert wire["headers"]["Referer"] == f"{IDN_PUNY}/"


def test_idn_and_punycode_spellings_share_one_host_queue(wire, clock):
    """Пауза к хосту меряется по нормализованному имени: кириллический и
    punycode-адрес одного сайта — один хост, а не два без паузы между ними."""
    fetch = _common.make_fetch("get_json")
    fetch(f"{IDN_SITE}/a", {})
    fetch(f"{IDN_PUNY}/b", {})
    low, high = pause_range()
    assert len(clock.sleeps) == 1 and low <= clock.sleeps[0] <= high


def test_punycode_url_keeps_port_path_and_ascii_urls_untouched():
    assert _common.punycode_url("https://a.example/x?y=1") == "https://a.example/x?y=1"
    assert (_common.punycode_url("https://Местосчастья.рф:8443/путь?q=1")
            == "https://xn--80ajujobbee1c4cub.xn--p1ai:8443/путь?q=1")
    assert _common.punycode_url("") == ""


def test_wire_headers_encodes_the_path_of_a_url_header_too():
    """Referer с кириллицей и в хосте, и в пути: хост — punycode, путь —
    процентное кодирование; выбрасывать такой Referer нельзя — его ждёт WAF."""
    clean = _common.wire_headers({"Referer": f"{IDN_SITE}/бронь/"})
    assert clean["Referer"] == f"{IDN_PUNY}/%D0%B1%D1%80%D0%BE%D0%BD%D1%8C/"
    clean["Referer"].encode("latin-1")
