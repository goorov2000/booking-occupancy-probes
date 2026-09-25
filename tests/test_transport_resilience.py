# -*- coding: utf-8 -*-
"""Живучесть транспорта (тикет 03, транспортная часть): джиттер, Retry-After,
потолок тела ответа, разделение «нас не пустили» и «схема сменилась».

Сети здесь нет: сессия requests подменена фейком, время и sleep — фейки.
Проверяется ровно то, что ломалось в бою: один 403 помечал рецепт broken
навсегда, пауза ровно 1.2 с была узнаваемым машинным ритмом, а тело ответа
читалось целиком, каким бы оно ни пришло.
"""
import json
import re
import threading
import time
from datetime import date

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
    """Ответ по контракту requests: потоковое тело, .text/.json() из _content.

    Транспорт читает тело сам (с потолком) и кладёт прочитанное обратно в
    _content — фейк повторяет ровно эту механику requests.
    """

    def __init__(self, body=b'{"ok": true}', status_code=200, headers=None,
                 chunk=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body
        self._chunk = chunk or len(body) or 1
        self._content = b""
        self.closed = False
        self.read_bytes = 0
        self.encoding = "utf-8"

    def iter_content(self, size):
        for i in range(0, len(self._body), self._chunk):
            piece = self._body[i:i + self._chunk]
            self.read_bytes += len(piece)
            yield piece

    def close(self):
        self.closed = True

    @property
    def text(self):
        return self._content.decode(self.encoding or "utf-8", "replace")

    def json(self):
        return json.loads(self._content)


class FakeSession:
    def __init__(self, response=None):
        self.response = response or (lambda url: FakeResponse())
        self.calls = []

    def _reply(self, url):
        self.calls.append(url)
        return self.response(url) if callable(self.response) else self.response

    def get(self, url, headers=None, timeout=None, stream=False):
        return self._reply(url)

    def post(self, url, json=None, headers=None, timeout=None, stream=False):
        return self._reply(url)


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(_common.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(_common.time, "sleep", fake.sleep)
    monkeypatch.setattr(_common, "_last_request_by_host", {})
    monkeypatch.setattr(_common, "_host_not_before", {})
    # Память потока (бюджет объекта и Retry-After последнего ответа) — тоже
    # чистая: соседний тест не должен подсказывать вердикт этому.
    monkeypatch.setattr(_common, "_local", threading.local())
    return fake


def use_session(monkeypatch, session):
    monkeypatch.setattr(requests, "Session", lambda: session)


# ---------------------------------------------------------------------------
# Джиттер: пауза всегда >= 1.2 с, но не всегда ровно 1.2
# ---------------------------------------------------------------------------

def test_pause_is_never_shorter_than_the_floor(clock, monkeypatch):
    use_session(monkeypatch, FakeSession())
    monkeypatch.setattr(_common.random, "uniform", lambda a, b: b)  # худший случай
    fetch = _common.make_fetch("get_json")
    fetch("https://a.example/x", {})
    fetch("https://a.example/y", {})
    assert clock.sleeps[0] >= _common.PAUSE_SEC
    assert clock.sleeps[0] == pytest.approx(
        _common.PAUSE_SEC + _common.PAUSE_JITTER_SEC)


def test_pause_is_not_always_the_same(clock, monkeypatch):
    """Ритм рваный: два подряд ожидания не совпадают до миллисекунды."""
    use_session(monkeypatch, FakeSession())
    values = iter([0.5, 0.1, 0.4])
    monkeypatch.setattr(_common.random, "uniform",
                        lambda a, b: next(values, 0.0))
    fetch = _common.make_fetch("get_json")
    for _ in range(3):
        fetch("https://a.example/x", {})
    assert len(set(clock.sleeps)) > 1
    assert all(s >= _common.PAUSE_SEC for s in clock.sleeps)


# ---------------------------------------------------------------------------
# Retry-After у 429
# ---------------------------------------------------------------------------

def test_retry_after_is_respected_before_next_request(clock, monkeypatch):
    use_session(monkeypatch, FakeSession(
        FakeResponse(status_code=429, headers={"Retry-After": "5"})))
    monkeypatch.setattr(_common.random, "uniform", lambda a, b: 0.0)
    fetch = _common.make_fetch("get_json")
    status, _ = fetch("https://a.example/x", {})
    assert status == 429            # транспорт не ретраит и не прячет отказ
    fetch("https://a.example/y", {})
    assert clock.sleeps == [pytest.approx(5.0)]


def test_long_retry_after_skips_the_object_instead_of_sleeping(clock,
                                                               monkeypatch):
    """Хост просит ждать полчаса — объект пропускается, процесс не висит."""
    use_session(monkeypatch, FakeSession(
        FakeResponse(status_code=429, headers={"Retry-After": "1800"})))
    fetch = _common.make_fetch("get_json")
    fetch("https://a.example/x", {})
    with pytest.raises(_common.AccessRefused) as e:
        fetch("https://a.example/y", {})
    assert "1800" in str(e.value) or "подожд" in str(e.value)
    assert clock.sleeps == []
    # пробники, ещё не знающие о новом каскаде, поймают это как сетевой сбой
    # и деградируют в partial, а не рухнут посреди прогона
    assert isinstance(e.value, OSError)


def test_parse_retry_after_accepts_seconds_and_http_date():
    assert _common.parse_retry_after("7") == 7
    assert _common.parse_retry_after(None) is None
    assert _common.parse_retry_after("не число") is None
    assert _common.parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is not None


# ---------------------------------------------------------------------------
# Потолок тела ответа
# ---------------------------------------------------------------------------

def test_huge_body_is_not_read_to_the_end(clock, monkeypatch):
    big = b"x" * (_common.MAX_BODY_BYTES + 5 * 1024 * 1024)
    resp = FakeResponse(body=big, chunk=64 * 1024)
    use_session(monkeypatch, FakeSession(resp))
    fetch = _common.make_fetch("get_text")
    with pytest.raises(_common.ResponseTooLarge):
        fetch("https://a.example/x", {})
    assert resp.read_bytes <= _common.MAX_BODY_BYTES + 64 * 1024
    assert resp.closed
    assert isinstance(_common.ResponseTooLarge("x"), OSError)


def test_normal_body_still_arrives_whole(clock, monkeypatch):
    use_session(monkeypatch, FakeSession(
        FakeResponse(body='{"привет": "мир"}'.encode("utf-8"), chunk=3)))
    assert _common.make_fetch("get_json")("https://a.example/x", {}) == (
        200, {"привет": "мир"})
    use_session(monkeypatch, FakeSession(
        FakeResponse(body="тело страницы".encode("utf-8"), chunk=5)))
    assert _common.make_fetch("get_text")("https://b.example/x", {}) == (
        200, "тело страницы")


# ---------------------------------------------------------------------------
# «Нас не пустили» отдельно от «схема сменилась»
# ---------------------------------------------------------------------------

def test_classify_response_separates_refusal_from_schema_change():
    assert _common.classify_response(200, True, "юнит").kind == "ok"
    refused = _common.classify_response(403, True, "юнит")
    assert refused.kind == "refused" and "403" in refused.reason
    limited = _common.classify_response(429, True, "юнит",
                                        headers={"Retry-After": "12"})
    assert limited.kind == "refused" and limited.retry_after == 12
    assert _common.classify_response(404, True, "юнит").kind == "broken"
    assert _common.classify_response(200, False, "юнит").kind == "broken"
    assert _common.classify_response(502, True, "юнит").kind == "network"


def test_classify_response_sees_a_challenge_page():
    verdict = _common.classify_response(
        200, False, "страница",
        body_text="<html><title>Just a moment...</title>cf-chl-bypass")
    assert verdict.kind == "refused"


def test_classify_http_keeps_the_old_two_tuple_contract():
    """Старый каскад пробников не трогаем: 403 у него по-прежнему broken."""
    broken, net = _common.classify_http(403, True, "юнит")
    assert net is None and "403" in broken
    assert _common.classify_http(200, True, "юнит") == (None, None)


# ---------------------------------------------------------------------------
# Сворачивание однотипных сбоев
# ---------------------------------------------------------------------------

def test_58_identical_failures_collapse_into_one_item():
    failures = [f"остатки на 2026-09-{d:02d}: HTTP 503" for d in range(1, 30)]
    failures += [f"остатки на 2026-10-{d:02d}: HTTP 503" for d in range(1, 30)]
    items = _common.collapse_failures(failures)
    assert len(items) == 1
    assert "58" in items[0]
    assert "503" in items[0]


def test_different_failures_stay_apart():
    items = _common.collapse_failures([
        "остатки на 2026-09-01: HTTP 503",
        "остатки на 2026-09-02: HTTP 503",
        "страница 2026-09: сетевой сбой (обрыв)",
    ])
    assert len(items) == 2


def table_cells(row: str) -> list:
    """Разбить строку markdown-таблицы по НЕэкранированным разделителям."""
    return re.split(r"(?<!\\)\|", row)


def test_failure_text_becomes_one_line():
    """Пайп и перевод строки чужой ошибки не рвут markdown-таблицу сводки.

    Причина из снимка вклеивается в колонку «Источник» (build_summary
    _source_label), поэтому чужой текст обязан быть однострочным И без
    голого разделителя: одного 4xx с пайпом в теле хватало, чтобы строка
    таблицы occupancy.md разъехалась.
    """
    items = _common.collapse_failures(["юнит: чужая ошибка\nвторая | строка"])
    assert "\n" not in items[0]
    assert r"\|" in items[0] and "вторая" in items[0]
    row = f"| объект | 50% | {items[0]} |"
    assert len(table_cells(row)) == 5      # пустые края + три колонки

    obj = {"username": "x", "units": {}}
    _common._finish(obj, ["юнит: ошибка\nс переводом | и пайпом"], None)
    assert "\n" not in obj["reason"]
    assert len(table_cells(f"| a | {obj['reason']} |")) == 4


def test_escaping_does_not_double_up():
    """Уже экранированный пайп не превращается в \\\\| при повторном проходе."""
    once = _common.one_line("а | б")
    assert _common.one_line(once) == once


# ---------------------------------------------------------------------------
# Пауза по хосту при потоках (тикет 05: цели разных хостов идут параллельно)
# ---------------------------------------------------------------------------

class CountingSession:
    """Настоящий (медленный) запрос: помечает начало и держится 20 мс."""

    def __init__(self, stamps, lock):
        self.stamps = stamps
        self.lock = lock

    def _reply(self, url):
        with self.lock:
            self.stamps.append((_common.host_of(url), time.monotonic()))
        time.sleep(0.02)
        return FakeResponse()

    def get(self, url, headers=None, timeout=None, stream=False):
        return self._reply(url)

    def post(self, url, json=None, headers=None, timeout=None, stream=False):
        return self._reply(url)


def run_threads(targets, pause_sec, monkeypatch):
    """Пустить каждый URL своим потоком; вернуть журнал (хост, момент)."""
    stamps, guard = [], threading.Lock()
    monkeypatch.setattr(_common, "_last_request_by_host", {})
    monkeypatch.setattr(_common, "_host_not_before", {})
    monkeypatch.setattr(_common, "PAUSE_JITTER_SEC", 0.0)
    monkeypatch.setattr(requests, "Session",
                        lambda: CountingSession(stamps, guard))

    def worker(urls):
        fetch = _common.make_fetch("get_json", pause_sec=pause_sec)
        for url in urls:
            fetch(url, {})

    threads = [threading.Thread(target=worker, args=(urls,))
               for urls in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return stamps


def test_lock_holds_the_pause_under_eight_threads(monkeypatch):
    """Восемь потоков на ОДИН хост — пауза между запросами всё равно держится.

    Без замка _last_request_by_host (обычный dict) паузу теряет: восемь
    потоков одновременно читают «последний запрос был давно» и бьют чужой
    хост залпом. Время здесь настоящее, поэтому пауза уменьшена до 50 мс —
    проверяется инвариант, а не константа 1.2 с.
    """
    pause = 0.05
    stamps = run_threads([[f"https://one.example/{i}"] for i in range(8)],
                         pause, monkeypatch)
    moments = sorted(t for _, t in stamps)
    assert len(moments) == 8
    gaps = [b - a for a, b in zip(moments, moments[1:])]
    assert min(gaps) >= pause


def test_two_hosts_do_not_wait_for_each_other(monkeypatch):
    """Разные хосты идут одновременно: очередь у каждого своя.

    Ради этого и заводился параллелизм: последовательный прогон 53 целей
    занимал ~87 минут против окна таймера, хотя цели ждали друг друга без
    всякой нужды — пауза приличия нужна ОДНОМУ хосту, а не всей машине.
    """
    pause = 0.4
    started = time.monotonic()
    stamps = run_threads([["https://a.example/1", "https://a.example/2"],
                          ["https://b.example/1", "https://b.example/2"]],
                         pause, monkeypatch)
    elapsed = time.monotonic() - started
    assert len(stamps) == 4
    # последовательно четыре запроса к одной очереди заняли бы >= 3*0.4 с
    assert elapsed < 3 * pause


# ---------------------------------------------------------------------------
# Глубина запроса тоже скользящая
# ---------------------------------------------------------------------------

def test_deep_date_to_slides_with_today(monkeypatch):
    import occupancy_core as core
    monkeypatch.setattr(core, "today", lambda: date(2026, 11, 5))
    assert _common.deep_date_to(date(2026, 11, 30)) == core.grid_horizon()
    far = date(2028, 1, 1)
    assert _common.deep_date_to(far) == far   # что просили глубже — сохраняем


# ---------------------------------------------------------------------------
# Волна 3: тело НЕудачного ответа доезжает до пробника (ревью wave2-probes)
#
# Cloudflare и DDoS-Guard отдают страницу-заслон кодом HTTP 200 с HTML-телом:
# у JSON-пробника это (200, None), и без текста тела каскад читал такой ответ
# как «схема сменилась» и слал живой рецепт на переразведку. Ветка
# is_challenge в classify_response существовала с тикета 03, но передать ей
# тело было нечем — транспорт его выбрасывал.
# ---------------------------------------------------------------------------

CHALLENGE = ("<html><head><title>Just a moment...</title></head>"
             "<body>cf-chl-bypass checking your browser</body></html>")


def test_transport_hands_the_body_of_a_blocked_page_to_the_probe(clock,
                                                                 monkeypatch):
    use_session(monkeypatch, FakeSession(
        FakeResponse(body=CHALLENGE.encode("utf-8"))))
    fetch = _common.make_fetch("get_json")
    status, data = fetch("https://a.example/x", {})
    assert (status, data) == (200, None)          # контракт fetch не менялся
    assert "cf-chl" in _common.last_body_text(fetch)
    assert "cf-chl" in fetch.last_body_text
    verdict = _common.classify_response(
        status, data is not None, "календарь",
        body_text=_common.last_body_text(fetch))
    assert verdict.kind == "refused"              # рецепт остаётся живым


def test_body_of_a_good_answer_is_not_kept(clock, monkeypatch):
    """Удачный ответ в памяти не держим: календарь на год — сотни килобайт."""
    session = FakeSession(lambda url: FakeResponse(
        body=CHALLENGE.encode("utf-8") if "bad" in url else b'{"ok": true}'))
    use_session(monkeypatch, session)
    fetch = _common.make_fetch("get_json")
    assert fetch.last_body_text == ""             # до первого запроса — пусто
    fetch("https://a.example/bad", {})
    assert fetch.last_body_text
    fetch("https://a.example/good", {})
    assert fetch.last_body_text == ""


def test_kept_body_is_only_the_head_of_it(clock, monkeypatch):
    """Держим начало тела, а не всё: смотрит в него только is_challenge."""
    use_session(monkeypatch, FakeSession(
        FakeResponse(body=b"<html>" + b"x" * 200000, status_code=503)))
    fetch = _common.make_fetch("get_text")
    fetch("https://a.example/x", {})
    assert len(fetch.last_body_text) == _common.BODY_SNIPPET_CHARS


def test_a_probe_that_never_asked_gets_an_empty_body():
    """Чужой fetch (подставной в тестах пробников) аксессор не роняет."""
    assert _common.last_body_text(lambda url, headers: (200, {})) == ""
    assert _common.last_body_text(None) == ""


# ---------------------------------------------------------------------------
# Волна 3: пауза меряется от КОНЦА запроса, а не от заголовков
# ---------------------------------------------------------------------------

def test_pause_counts_from_the_end_of_the_body(clock, monkeypatch):
    """Чтение тела входит в запрос: иначе пауза к хосту фактически короче.

    С переходом на stream=True ответ возвращается по ЗАГОЛОВКАМ, а тело
    читается после. Отметка времени, поставленная в этот момент, делала
    паузу между концом чтения ответа N и стартом запроса N+1 короче 1.2 с на
    объектах с крупными календарями (a_ureki 195 КБ в снапшоте 04.09).
    """
    class SlowBody(FakeResponse):
        def iter_content(self, size):
            for piece in super().iter_content(size):
                clock.now += 0.5       # чтение тела заняло полсекунды
                yield piece

    use_session(monkeypatch, FakeSession(lambda url: SlowBody()))
    monkeypatch.setattr(_common.random, "uniform", lambda a, b: 0.0)
    fetch = _common.make_fetch("get_json")
    fetch("https://a.example/x", {})
    fetch("https://a.example/y", {})
    assert clock.sleeps == [pytest.approx(_common.PAUSE_SEC)]


# ---------------------------------------------------------------------------
# Волна 3: host_turn — шов для тех, кто ходит к хосту не нашим fetch
# ---------------------------------------------------------------------------

def test_host_turn_serialises_a_foreign_transport(monkeypatch):
    """Очередь к хосту одна на процесс, чем бы запрос ни отправлялся.

    Разведчик и любой чужой транспорт обязаны брать её этим контекстом:
    голый _wait_host_turn паузу считает, но соседа в неё не пускает не он, а
    замок хоста.
    """
    monkeypatch.setattr(_common, "_last_request_by_host", {})
    monkeypatch.setattr(_common, "_host_not_before", {})
    monkeypatch.setattr(_common, "PAUSE_JITTER_SEC", 0.0)
    pause, stamps, guard = 0.05, [], threading.Lock()

    def worker():
        with _common.host_turn("https://one.example/x", pause):
            with guard:
                stamps.append(time.monotonic())
            time.sleep(0.01)
            _common._note_request("https://one.example/x")

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    moments = sorted(stamps)
    assert len(moments) == 4
    assert min(b - a for a, b in zip(moments, moments[1:])) >= pause


def test_a_live_probe_reads_a_challenge_page_as_refusal(clock, monkeypatch):
    """Сквозная проверка контракта волны 3: транспорт -> пробник -> каскад.

    Тесты пробников подставляют fetch с готовым атрибутом last_body_text и
    проверяют только свою половину контракта. Здесь работает НАСТОЯЩИЙ
    транспорт (подменена лишь сессия requests): заслон с кодом 200 и
    HTML-телом обязан дойти до каскада как «нас не пустили» и оставить
    рецепт живым — иначе одна ночь под WAF выбивает рецепты пачками.
    """
    import probes

    use_session(monkeypatch, FakeSession(
        FakeResponse(body=CHALLENGE.encode("utf-8"))))
    recipe = {
        "site": "https://istra-cottage.ru", "engine": "travelline",
        "status": "ok",
        "request": {"url_template": (
            "https://ru-ibe.tlintegration.ru/ApiWebDistribution/"
            "AvailabilityCalendar/room_type_availability_2?hotel="
            "{hotel_code}&start_date={date_from}&end_date={date_to}"),
            "method": "GET", "params": {"hotel_code": "11269"},
            "headers": {}, "date_substitution": "iso"},
        "discovered_at": "2026-08-14T18:00:00+03:00",
        "notes": "", "source_urls": [],
    }
    obj, broken = probes.run_recipe("istracottage", recipe,
                                    date(2026, 9, 4), date(2026, 9, 4))
    assert broken is None                       # рецепт НЕ ломается
    assert "не пустили" in obj["refusal"]["reason"]


# ---------------------------------------------------------------------------
# Волна 4: бюджет времени на объект РЕЖЕТ запросы (ревью wave3-core)
# ---------------------------------------------------------------------------
#
# Дедлайн прогона останавливает выдачу НОВЫХ целей, но начатая цель шла до
# конца: у ветки tl_api это три обязательных запроса плюс POST на каждую из 46
# ночей фонда, у pineriver_hotel замер дал 117 запросов. Цель, начатая на 89-й
# минуте при дедлайне 90 и TimeoutStartSec=120min, съедала весь хвост прогона
# (сводка, слой, телеграм, Run Log). Бюджет обязан не давать слать НОВЫЕ
# запросы, а не переклеивать ярлык постфактум.

def test_budget_stops_new_requests(clock, monkeypatch):
    session = FakeSession()
    use_session(monkeypatch, session)
    monkeypatch.setattr(_common.random, "uniform", lambda a, b: 0.0)
    fetch = _common.make_fetch("get_json")
    with _common.request_budget(10):
        fetch("https://a.example/1", {})
        clock.now += 11                       # бюджет объекта вышел
        with pytest.raises(_common.BudgetExceeded):
            fetch("https://a.example/2", {})
    assert session.calls == ["https://a.example/1"]


def test_budget_error_is_a_network_failure_not_a_refusal(clock, monkeypatch):
    """Пробники ловят OSError и деградируют; refusal — это про чужой отказ.

    Будь это AccessRefused, исчерпанный бюджет считался бы «нас не пустили» и
    через трое суток ломал бы живой рецепт.
    """
    assert issubclass(_common.BudgetExceeded, OSError)
    assert not issubclass(_common.BudgetExceeded, _common.AccessRefused)


def test_budget_is_per_thread_and_is_returned(clock, monkeypatch):
    """Цели разных хостов снимаются в разных потоках — бюджет у каждой свой."""
    use_session(monkeypatch, FakeSession())
    seen = {}

    def other_thread():
        seen["inside"] = _common.budget_left()

    with _common.request_budget(30):
        assert 0 < _common.budget_left() <= 30
        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join()
    assert seen["inside"] is None             # чужой поток бюджетом не связан
    assert _common.budget_left() is None      # и после выхода он снят


def test_no_budget_means_no_limit(clock, monkeypatch):
    session = FakeSession()
    use_session(monkeypatch, session)
    monkeypatch.setattr(_common.random, "uniform", lambda a, b: 0.0)
    fetch = _common.make_fetch("get_json")
    clock.now += 10_000
    fetch("https://a.example/1", {})           # без бюджета режима «стоп» нет
    assert session.calls == ["https://a.example/1"]


# ---------------------------------------------------------------------------
# Волна 4: Retry-After доезжает до вердикта (ревью wave3-core)
# ---------------------------------------------------------------------------
#
# Параметр headers у classify_response не передавал ни один из десяти вызовов
# в пробниках, поэтому retry_after в вердикте был всегда None и обещанная
# приписка «хост просит подождать N с» не появлялась никогда.

def test_retry_after_reaches_the_verdict_without_headers(clock, monkeypatch):
    use_session(monkeypatch, FakeSession(
        FakeResponse(status_code=429, headers={"Retry-After": "7"})))
    fetch = _common.make_fetch("get_json")
    status, data = fetch("https://a.example/x", {})
    verdict = _common.classify_response(status, data is not None, "юнит")
    assert verdict.kind == "refused"
    assert verdict.retry_after == 7
    assert "подожд" in verdict.reason and "7" in verdict.reason


def test_a_quiet_host_leaves_no_stale_retry_after(clock, monkeypatch):
    """Просьба одного ответа не должна прилипнуть к следующему вердикту."""
    replies = iter([FakeResponse(status_code=429,
                                 headers={"Retry-After": "7"}),
                    FakeResponse(status_code=403)])
    use_session(monkeypatch, FakeSession(lambda url: next(replies)))
    monkeypatch.setattr(_common.random, "uniform", lambda a, b: 0.0)
    fetch = _common.make_fetch("get_json")
    fetch("https://a.example/x", {})
    status, data = fetch("https://b.example/y", {})
    verdict = _common.classify_response(status, data is not None, "юнит")
    assert verdict.kind == "refused" and verdict.retry_after is None


def test_explicit_headers_still_win(clock, monkeypatch):
    verdict = _common.classify_response(429, True, "юнит",
                                        headers={"Retry-After": "9"})
    assert verdict.retry_after == 9
