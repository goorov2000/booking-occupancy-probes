# -*- coding: utf-8 -*-
"""Прогон не теряет день (тикет 02) + происхождение снимка (тикет 10).

Сети здесь нет: диспетчер пробников подменён фейком. Проверяется ровно то,
что стоило бы дня данных в бою: снапшот писался одним куском в конце, и
обрыв на 50-й цели из 53 стирал весь прогон; у прогона не было ни дедлайна,
ни бюджета на объект; необработанное исключение одного пробника роняло всё.

Волна 2 (тикет 05) добавила сюда параллелизм по хостам: цели группируются по
хосту рецепта, внутри группы идут строго последовательно, группы — вместе.
Времена в тестах параллелизма настоящие (доли секунды), потому что проверяется
именно одновременность, а фейковых часов на неё не хватает.
"""
import inspect
import json
import threading
import time
from datetime import date, timedelta

import pytest

import cli
import occupancy_core as core


@pytest.fixture(autouse=True)
def machine_lock_in_tmp(tmp_path, monkeypatch):
    """Замок живого съёма — один на МАШИНУ; в тестах уводим его в tmp.

    Иначе прогон тестов брал бы ровно тот файл, которым плановый прогон 06:30
    защищает чужие хосты, и два процесса дрались бы за него вхолостую.
    """
    monkeypatch.setenv(core.HOST_LOCK_ENV, str(tmp_path / ".hosts.lock"))


def recipe(engine="travelline", url="https://x/{date_from}"):
    return {
        "site": "https://example.com",
        "engine": engine,
        "status": "ok",
        "request": {"url_template": url, "method": "GET", "params": {},
                    "headers": {}, "date_substitution": "iso"},
        "discovered_at": "2026-08-14T10:00:00+03:00",
        "notes": "", "source_urls": [],
    }


def write_recipes(tmp_path, names, **kw):
    path = tmp_path / "recipes.json"
    core.save_recipes(path, {name: recipe(**kw) for name in names})
    return path


def snapped(username, status="ok", reason=""):
    return {
        "username": username,
        "site": "https://example.com",
        "engine": "travelline",
        "source_kind": "module",
        "granularity": "per_unit",
        "units": {"Дом 1": {"2026-11-06": {"state": "busy"}}},
        "checked_at": "2026-11-05T06:30:00+03:00",
        "status": status,
        "reason": reason,
        "source_urls": [],
    }


def run_cli(tmp_path, names, recipes_path, extra=(), run_id="2026-11-05-0630"):
    return cli.main(["--recipes", str(recipes_path),
                     "--snapshot-root", str(tmp_path / "snapshots"),
                     "--run-id", run_id]
                    + [a for name in names for a in ("--probe", name)]
                    + list(extra))


# ---------------------------------------------------------------------------
# Снапшот пишется по частям
# ---------------------------------------------------------------------------

def test_crash_on_the_third_target_keeps_the_first_two(tmp_path, monkeypatch):
    names = ["a", "b", "c", "d", "e"]
    recipes = write_recipes(tmp_path, names)

    def fake_run(username, rec, date_from, date_to, **kw):
        if username == "c":
            raise KeyboardInterrupt("systemd прибил прогон")
        return snapped(username), None

    monkeypatch.setattr(cli.probes, "run_recipe", fake_run)
    with pytest.raises(KeyboardInterrupt):
        run_cli(tmp_path, names, recipes)

    snap = tmp_path / "snapshots" / "2026-11-05-0630"
    assert (snap / "run.json").is_file()
    assert (snap / "a.json").is_file() and (snap / "b.json").is_file()
    assert not (snap / "d.json").exists()
    run = json.loads((snap / "run.json").read_text(encoding="utf-8"))
    assert run["targets"] == names        # план записан ДО съёма
    assert core.read_snapshot(snap)["objects"].keys() == {"a", "b"}


def test_deadline_gives_every_remaining_target_an_honest_reason(tmp_path,
                                                                monkeypatch):
    names = ["a", "b"]
    recipes = write_recipes(tmp_path, names)
    monkeypatch.setattr(cli.probes, "run_recipe",
                        lambda u, r, f, t, **kw: (snapped(u), None))
    rc = run_cli(tmp_path, names, recipes, extra=["--deadline-min", "0"])
    assert rc != 0
    snap = core.read_snapshot(tmp_path / "snapshots" / "2026-11-05-0630")
    assert set(snap["objects"]) == {"a", "b"}
    for obj in snap["objects"].values():
        assert obj["status"] == "insufficient_data"
        assert "дедлайн" in obj["reason"]
    assert snap["run"]["finished_at"]


def test_slow_target_is_partial_and_the_run_goes_on(tmp_path, monkeypatch):
    names = ["fast", "slow", "next"]
    recipes = write_recipes(tmp_path, names)
    clock = {"t": 1000.0}
    monkeypatch.setattr(cli.time, "monotonic", lambda: clock["t"])

    def fake_run(username, rec, date_from, date_to, **kw):
        if username == "slow":
            clock["t"] += 700          # бюджет по умолчанию 600 с
        return snapped(username), None

    monkeypatch.setattr(cli.probes, "run_recipe", fake_run)
    assert run_cli(tmp_path, names, recipes) == 0
    objects = core.read_snapshot(tmp_path / "snapshots"
                                 / "2026-11-05-0630")["objects"]
    assert objects["slow"]["status"] == "partial"
    assert "бюджет" in objects["slow"]["reason"]
    assert objects["fast"]["status"] == "ok"
    assert objects["next"]["status"] == "ok"


def test_unhandled_probe_error_is_one_honest_row(tmp_path, monkeypatch):
    names = ["boom", "ok_one"]
    recipes = write_recipes(tmp_path, names)

    def fake_run(username, rec, date_from, date_to, **kw):
        if username == "boom":
            raise ValueError("движок отдал совсем не то")
        return snapped(username), None

    monkeypatch.setattr(cli.probes, "run_recipe", fake_run)
    assert run_cli(tmp_path, names, recipes) == 0
    objects = core.read_snapshot(tmp_path / "snapshots"
                                 / "2026-11-05-0630")["objects"]
    assert objects["boom"]["status"] == "insufficient_data"
    assert "совсем не то" in objects["boom"]["reason"]
    assert objects["ok_one"]["status"] == "ok"


def test_second_parallel_run_refuses_by_lock(tmp_path, monkeypatch, capsys):
    names = ["a"]
    recipes = write_recipes(tmp_path, names)
    monkeypatch.setattr(cli.probes, "run_recipe",
                        lambda u, r, f, t, **kw: (snapped(u), None))
    root = tmp_path / "snapshots"
    root.mkdir(parents=True, exist_ok=True)
    with core.run_lock(root / ".run.lock"):
        rc = run_cli(tmp_path, names, recipes)
    assert rc == 2
    err = capsys.readouterr().err
    assert "прогон уже идёт" in err
    assert "Traceback" not in err


def test_registry_is_written_atomically(tmp_path, monkeypatch):
    """Реестр меняется через временный файл: обрыв не оставляет обрезанный JSON."""
    path = tmp_path / "recipes.json"
    core.save_recipes(path, {"a": recipe()})
    seen = []
    real_replace = core.os.replace
    monkeypatch.setattr(core.os, "replace",
                        lambda src, dst: (seen.append((src, dst)),
                                          real_replace(src, dst))[1])
    core.save_recipes(path, {"a": recipe(), "b": recipe()})
    assert seen and str(seen[0][1]).endswith("recipes.json")
    assert set(core.load_recipes(path)) == {"a", "b"}


# ---------------------------------------------------------------------------
# Скользящие горизонты доходят до пробника (тикет 01)
#
# Через БОЕВОЙ probes.run_recipe, а не через подменённый диспетчер: прежние
# тесты подставляли вместо него собственный фейк с нужной сигнатурой, то есть
# проверяли мок. В бою cli молча выбрасывал незнакомый диспетчеру аргумент, и
# --inventory-days — главный рычаг цены прогона — был no-op.
# ---------------------------------------------------------------------------

def fake_engine(seen, version=3):
    """Модуль движка по контракту диспетчера: пишет, что до него дошло."""
    class Engine:
        PROBE_VERSION = version

        @staticmethod
        def probe(username, rec, date_from, date_to, fetch=None,
                  inventory=True, inventory_date_to=None):
            seen.update(username=username, date_from=date_from,
                        date_to=date_to, inventory=inventory,
                        inventory_date_to=inventory_date_to)
            return snapped(username), None

    return Engine


def register_engine(monkeypatch, seen, **kw):
    monkeypatch.setitem(cli.probes.ENGINES, "fake", fake_engine(seen, **kw))


def test_horizons_reach_the_probe(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(core, "today", lambda: date(2026, 11, 5))
    recipes = write_recipes(tmp_path, ["a"], engine="fake")
    seen = {}
    register_engine(monkeypatch, seen)

    assert run_cli(tmp_path, ["a"], recipes) == 0
    assert seen["date_from"] == date(2026, 11, 5)
    assert seen["date_to"] == core.grid_horizon()          # +12 месяцев
    assert seen["inventory_date_to"] == core.inventory_horizon()  # +45 ночей
    assert seen["inventory"] is True
    # сводка на экране — скользящее окно месяцев, а не авг-окт 2026
    assert "ноя 2026" in capsys.readouterr().out


def test_inventory_days_is_the_lever(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "today", lambda: date(2026, 11, 5))
    recipes = write_recipes(tmp_path, ["a"], engine="fake")
    seen = {}
    register_engine(monkeypatch, seen)

    run_cli(tmp_path, ["a"], recipes, extra=["--inventory-days", "7"])
    assert seen["inventory_date_to"] == date(2026, 11, 12)


def test_no_inventory_switches_the_fund_off(tmp_path, monkeypatch):
    recipes = write_recipes(tmp_path, ["a"], engine="fake")
    seen = {}
    register_engine(monkeypatch, seen)

    run_cli(tmp_path, ["a"], recipes, extra=["--no-inventory"])
    assert seen["inventory"] is False


def test_dispatcher_accepts_the_inventory_horizon():
    """Контракт волны 2: диспетчер ПРИНИМАЕТ горизонт фонда.

    Шим, молча выбрасывавший незнакомый аргумент, снят: тихий no-op рычага
    цены прогона хуже громкого TypeError. Тест сторожит саму сигнатуру —
    вернуть старую значит вернуть no-op.
    """
    params = inspect.signature(cli.probes.run_recipe).parameters
    assert "inventory_date_to" in params
    assert params["inventory_date_to"].default is None


def test_probe_version_in_the_snapshot_comes_from_the_engine(tmp_path,
                                                             monkeypatch):
    """Версию снимка подписывает МОДУЛЬ движка, а не общая версия транспорта."""
    recipes = write_recipes(tmp_path, ["a"], engine="fake")
    register_engine(monkeypatch, {}, version=9)

    run_cli(tmp_path, ["a"], recipes)
    obj = core.read_snapshot(tmp_path / "snapshots"
                             / "2026-11-05-0630")["objects"]["a"]
    assert obj["probe_version"] == "fake@9"


# ---------------------------------------------------------------------------
# Параллелизм по хостам (тикет 05)
#
# Замер 04.09: 900 запросов и 1591 с на 21 цель; при ~53 целях последовательно
# это ~87 минут против окна таймера. Цели ждали друг друга без нужды: пауза
# приличия нужна ОДНОМУ хосту, а не всей машине.
# ---------------------------------------------------------------------------

def timed_run(tmp_path, monkeypatch, hosts, delays=None, extra=()):
    """Прогон целей с заданными хостами. -> (код, журнал (имя, старт, конец))."""
    names = [f"t{i}" for i in range(len(hosts))]
    path = tmp_path / "recipes.json"
    core.save_recipes(path, {
        name: recipe(url=f"https://{host}/{{date_from}}")
        for name, host in zip(names, hosts)})
    log, guard = [], threading.Lock()
    delays = delays or {}

    def fake_run(username, rec, date_from, date_to, **kw):
        began = time.monotonic()
        time.sleep(delays.get(username, 0.15))
        with guard:
            log.append((username, began, time.monotonic()))
        return snapped(username), None

    monkeypatch.setattr(cli.probes, "run_recipe", fake_run)
    rc = run_cli(tmp_path, names, path, extra=list(extra))
    return rc, {name: (began, ended) for name, began, ended in log}


def overlapped(log, one: str, two: str) -> bool:
    """Пересеклись ли во времени съёмы двух целей."""
    (a_start, a_end), (b_start, b_end) = log[one], log[two]
    return a_start < b_end and b_start < a_end


def test_two_targets_of_one_host_go_one_after_another(tmp_path, monkeypatch):
    """Внутри хоста — строго последовательно: чужой хост бить залпом нельзя."""
    rc, log = timed_run(tmp_path, monkeypatch, ["one.example", "one.example"])
    assert rc == 0 and len(log) == 2
    assert not overlapped(log, "t0", "t1")


def test_two_targets_of_different_hosts_go_at_once(tmp_path, monkeypatch):
    rc, log = timed_run(tmp_path, monkeypatch, ["a.example", "b.example"])
    assert rc == 0 and len(log) == 2
    assert overlapped(log, "t0", "t1")


def test_host_workers_one_gives_back_the_sequential_run(tmp_path, monkeypatch):
    """Рычаг отката: один воркер — прежний последовательный прогон."""
    rc, log = timed_run(tmp_path, monkeypatch, ["a.example", "b.example"],
                        extra=["--host-workers", "1"])
    assert rc == 0
    assert not overlapped(log, "t0", "t1")


def test_targets_without_a_recipe_do_not_hold_a_host_queue(tmp_path,
                                                           monkeypatch):
    """Цель без рецепта хоста не имеет и очередь чужого хоста не занимает."""
    names = ["known", "no_such_obj"]
    path = write_recipes(tmp_path, ["known"])
    monkeypatch.setattr(cli.probes, "run_recipe",
                        lambda u, r, f, t, **kw: (snapped(u), None))
    assert run_cli(tmp_path, names, path) == 0
    objects = core.read_snapshot(tmp_path / "snapshots"
                                 / "2026-11-05-0630")["objects"]
    assert objects["known"]["status"] == "ok"
    assert "рецепта нет" in objects["no_such_obj"]["reason"]


def test_each_finished_target_reports_its_host_to_the_journal(tmp_path,
                                                              monkeypatch,
                                                              capsys):
    """Ход прогона виден в stderr: иначе полтора часа — как зависший процесс."""
    monkeypatch.setattr(cli.probes, "run_recipe",
                        lambda u, r, f, t, **kw: (snapped(u), None))
    path = tmp_path / "recipes.json"
    core.save_recipes(path, {"a": recipe(url="https://one.example/{date_from}")})
    assert run_cli(tmp_path, ["a"], path) == 0
    err = capsys.readouterr().err
    assert "снято a [one.example]" in err and "ok" in err


def test_parallel_run_keeps_the_order_of_targets(tmp_path, monkeypatch,
                                                 capsys):
    """Порядок целей = важность; параллелизм его не перемешивает.

    Самая ВАЖНАЯ цель здесь снимается дольше всех, то есть заканчивается
    последней — и всё равно идёт первой и в плане прогона, и в отчёте.
    """
    rc, _ = timed_run(tmp_path, monkeypatch,
                      ["a.example", "b.example", "c.example"],
                      delays={"t0": 0.30, "t1": 0.15, "t2": 0.02})
    assert rc == 0
    run = json.loads((tmp_path / "snapshots" / "2026-11-05-0630" / "run.json")
                     .read_text(encoding="utf-8"))
    assert run["targets"] == ["t0", "t1", "t2"]
    out = capsys.readouterr().out
    assert out.index("t0") < out.index("t1") < out.index("t2")


# ---------------------------------------------------------------------------
# Происхождение снимка (тикет 10)
# ---------------------------------------------------------------------------

def test_object_carries_provenance(tmp_path, monkeypatch):
    recipes = write_recipes(tmp_path, ["a"])
    monkeypatch.setattr(cli.probes, "run_recipe",
                        lambda u, r, f, t, **kw: (snapped(u), None))
    # Плановое время — вчера: прогон догоняет пропущенный день (простой
    # машины), и снимок обязан нести это отставание, иначе ночь уедет в
    # рыночную медиану как снятая вовремя (правило Р4 спеки).
    planned = f"{date.today() - timedelta(days=1)}T06:30:00"
    run_cli(tmp_path, ["a"], recipes, extra=["--planned-at", planned])
    obj = core.read_snapshot(tmp_path / "snapshots"
                             / "2026-11-05-0630")["objects"]["a"]
    assert obj["probe_version"].startswith("travelline@")
    assert len(obj["recipe_hash"]) == 64
    assert obj["run_planned_at"] == planned
    assert obj["gap_days"] == 1


def test_recipe_hash_follows_the_request_not_the_notes():
    base = recipe()
    same_notes_changed = recipe()
    same_notes_changed["notes"] = "переразведано 04.09, тот же запрос"
    other = recipe(url="https://x/v2/{date_from}")
    assert core.recipe_hash(base) == core.recipe_hash(same_notes_changed)
    assert core.recipe_hash(base) != core.recipe_hash(other)
    assert core.recipe_hash({}) is None


def test_snapshot_without_provenance_reads_as_before(tmp_path):
    """Снапшоты до 04.09 не несут новых полей — читаются и считаются как раньше."""
    root = tmp_path / "snapshots"
    core.write_snapshot(root, "2026-08-14-1000",
                        {"started_at": "2026-08-14T10:00:00+03:00",
                         "targets": ["a"]}, [snapped("a")])
    obj = core.read_snapshot(root / "2026-08-14-1000")["objects"]["a"]
    assert "probe_version" not in obj and "gap_days" not in obj
    metrics = core.aggregate(obj["units"], ["2026-11"])
    assert metrics[0]["cuts"]["all"]["busy"] == 1


# ---------------------------------------------------------------------------
# Отказ хоста считается сутками, а не запросами (тикет 03, часть в ядре)
# ---------------------------------------------------------------------------

def test_three_refusal_days_break_the_recipe_two_do_not():
    rec = recipe()
    assert core.note_refusal(rec, "HTTP 403", today_=date(2026, 11, 1)) is None
    assert core.note_refusal(rec, "HTTP 403", today_=date(2026, 11, 1)) is None
    assert core.note_refusal(rec, "HTTP 403", today_=date(2026, 11, 2)) is None
    assert rec["refusals"]["count"] == 2
    core.clear_refusals(rec)           # между отказами прогон удался
    assert "refusals" not in rec
    assert core.note_refusal(rec, "HTTP 403", today_=date(2026, 11, 3)) is None
    broken = core.note_refusal(rec, "HTTP 429", today_=date(2026, 11, 4))
    assert broken is None
    broken = core.note_refusal(rec, "HTTP 429", today_=date(2026, 11, 5))
    assert broken and "3 суток" in broken


def test_a_gap_in_the_refusals_resets_the_counter():
    """Сутки без отказа обрывают череду: счётчик начинается заново.

    Считать РАЗНЫЕ сутки нельзя: одиночный 403 раз в месяц (чужой WAF икнул)
    за три месяца накопил бы порог и сломал живой рецепт, а сообщение при
    этом утверждало бы «нас не пускают 3 суток подряд». Обнуления по
    успешному съёму тут мало: в день, когда объект не снимался вовсе
    (дедлайн, пауза, вывод цели из очереди), обнулять нечего.
    """
    rec = recipe()
    assert core.note_refusal(rec, "HTTP 403", today_=date(2026, 11, 1)) is None
    assert core.note_refusal(rec, "HTTP 403", today_=date(2026, 11, 3)) is None
    assert rec["refusals"]["count"] == 1          # 02.11 отказа не было
    assert rec["refusals"]["since"] == "2026-11-03"
    assert core.note_refusal(rec, "HTTP 403", today_=date(2026, 11, 4)) is None
    assert rec["refusals"]["count"] == 2
    broken = core.note_refusal(rec, "HTTP 403", today_=date(2026, 11, 5))
    assert broken and "3 суток" in broken         # 03-05.11 — подряд


def test_refusal_counter_survives_a_hand_edited_recipe():
    """Битый last_day (правка рецепта руками) — начинаем череду заново."""
    rec = recipe()
    rec["refusals"] = {"count": 2, "since": "?", "last_day": "позавчера"}
    assert core.note_refusal(rec, "HTTP 429", today_=date(2026, 11, 5)) is None
    assert rec["refusals"]["count"] == 1
    # Рецепт без since не должен ронять сообщение о поломке KeyError-ом.
    rec["refusals"] = {"count": 2, "last_day": "2026-11-04"}
    broken = core.note_refusal(rec, "HTTP 403", today_=date(2026, 11, 5))
    assert broken and "2026-11-04" in broken


def test_cli_counts_refusals_and_keeps_the_recipe_alive(tmp_path, monkeypatch):
    recipes_path = write_recipes(tmp_path, ["a"])

    def refused(username, rec, date_from, date_to, **kw):
        obj = snapped(username, status="insufficient_data",
                      reason="календарь: HTTP 403 — нас не пустили")
        obj["units"] = {}
        obj["refusal"] = {"reason": "календарь: HTTP 403", "status": 403,
                          "at": "2026-11-05T06:30:00+03:00"}
        return obj, None

    monkeypatch.setattr(cli.probes, "run_recipe", refused)
    assert run_cli(tmp_path, ["a"], recipes_path) == 0
    saved = core.load_recipes(recipes_path)["a"]
    assert saved["status"] == "ok"                 # один 403 рецепт не ломает
    assert saved["refusals"]["count"] == 1
    assert "403" in saved["refusals"]["last_reason"]


# ---------------------------------------------------------------------------
# Волна 3: невалидный объект не обрывает закрытие прогона (ревью wave2-core2)
#
# Проверка схемы живёт в core.append_object, то есть СНАРУЖИ общего except
# вокруг съёма одного объекта. Одна клетка вида state=busy при units_free=1
# роняла поток очереди, а с ним и весь хвост прогона: run.json оставался без
# finished_at, реестр рецептов не сохранялся, сводка и динамика не печатались.
# Слой сезонности незакрытый прогон в работу не берёт — то есть один плохой
# объект стоил дня по ВСЕМ целям.
# ---------------------------------------------------------------------------

def broken_cell_object(username):
    """Объект, который бракует core._validate_capacity: busy при свободном."""
    obj = snapped(username)
    obj["units"] = {"Дом 1": {"2026-11-06": {"state": "busy",
                                             "units_total": 2,
                                             "units_free": 1}}}
    return obj


def test_invalid_object_does_not_abort_the_run(tmp_path, monkeypatch, capsys):
    names = ["a", "bad", "c"]
    recipes = write_recipes(tmp_path, names)

    def fake_run(username, rec, date_from, date_to, **kw):
        if username == "bad":
            return broken_cell_object(username), None
        return snapped(username), None

    monkeypatch.setattr(cli.probes, "run_recipe", fake_run)
    assert run_cli(tmp_path, names, recipes) == 0

    snap = core.read_snapshot(tmp_path / "snapshots" / "2026-11-05-0630")
    assert snap["run"]["finished_at"]                  # прогон ЗАКРЫТ
    assert set(snap["objects"]) == {"a", "bad", "c"}
    bad = snap["objects"]["bad"]
    assert bad["status"] == "insufficient_data"
    # причина несёт САМ диагноз проверки, а не «что-то пошло не так»
    assert "проверку схемы" in bad["reason"]
    assert "state=busy, но свободно 1" in bad["reason"]
    assert snap["objects"]["a"]["status"] == "ok"
    assert snap["objects"]["c"]["status"] == "ok"
    # сводка по всем трём целям напечатана, порядок целей сохранён
    reported = [line.split(" ")[0] for line in capsys.readouterr().out
                .splitlines() if line.startswith(("a ", "bad ", "c "))]
    assert reported == ["a", "bad", "c"]


def test_invalid_object_keeps_its_provenance(tmp_path, monkeypatch):
    """У забракованной строки видно, чем и по какому рецепту снимали."""
    recipes = write_recipes(tmp_path, ["bad"])
    monkeypatch.setattr(cli.probes, "run_recipe",
                        lambda u, r, f, t, **kw: (broken_cell_object(u), None))
    assert run_cli(tmp_path, ["bad"], recipes) == 0
    obj = core.read_snapshot(tmp_path / "snapshots"
                             / "2026-11-05-0630")["objects"]["bad"]
    assert obj["probe_version"].startswith("travelline@")
    assert len(obj["recipe_hash"]) == 64


def test_invalid_object_does_not_break_the_recipe(tmp_path, monkeypatch):
    """Брак схемы рецепт НЕ ломает: broken — это работа переразведки.

    Иначе одна невалидная клетка чужого движка отправляла бы живой рецепт на
    ручную переразведку, которая ничего не чинит.
    """
    recipes = write_recipes(tmp_path, ["bad"])
    monkeypatch.setattr(cli.probes, "run_recipe",
                        lambda u, r, f, t, **kw: (broken_cell_object(u), None))
    run_cli(tmp_path, ["bad"], recipes)
    assert core.load_recipes(recipes)["bad"]["status"] == "ok"


# ---------------------------------------------------------------------------
# Волна 3: блокировка держит РЕЕСТР, а не каталог снапшотов
# ---------------------------------------------------------------------------

def test_second_run_with_another_snapshot_root_refuses_by_registry_lock(
        tmp_path, monkeypatch, capsys):
    """Замер во временный --snapshot-root не должен разойтись с плановым.

    Оба прогона читают recipes.json в начале и переписывают целиком в конце:
    последний писатель молча стирает чужие правки (счётчики отказов,
    broken_reason). Атомарность записи от этого не спасает — файл целый,
    потеряно обновление.
    """
    recipes = write_recipes(tmp_path, ["a"])
    monkeypatch.setattr(cli.probes, "run_recipe",
                        lambda u, r, f, t, **kw: (snapped(u), None))
    with core.run_lock(cli._registry_lock_path(recipes)):
        rc = cli.main(["--recipes", str(recipes),
                       "--snapshot-root", str(tmp_path / "другой-корень"),
                       "--run-id", "2026-11-05-0630", "--probe", "a"])
    assert rc == 2
    assert "прогон уже идёт" in capsys.readouterr().err


def test_fixture_run_does_not_need_the_registry_lock(tmp_path, monkeypatch):
    """Fixture-режим реестра не касается — и замком его не держит."""
    fixture = tmp_path / "grid.json"
    fixture.write_text(json.dumps(snapped("f")), encoding="utf-8")
    recipes = write_recipes(tmp_path, ["a"])
    with core.run_lock(cli._registry_lock_path(recipes)):
        rc = cli.main(["--recipes", str(recipes),
                       "--snapshot-root", str(tmp_path / "snapshots"),
                       "--run-id", "2026-11-05-0630",
                       "--fixture", str(fixture)])
    assert rc == 0


# ---------------------------------------------------------------------------
# Волна 3: снято — это снято (objects_written и подпись снимка)
# ---------------------------------------------------------------------------

def test_objects_written_counts_only_the_targets_actually_probed(tmp_path,
                                                                 monkeypatch):
    """«Планировали 2, сняли 0» должно читаться из снапшота, а не из соседа.

    Цели, до которых очередь не дошла по дедлайну, запросов не стоили: считать
    их снятыми значит утверждать «прогон снял всё», когда он не снял ничего.
    """
    names = ["a", "b"]
    recipes = write_recipes(tmp_path, names)
    monkeypatch.setattr(cli.probes, "run_recipe",
                        lambda u, r, f, t, **kw: (snapped(u), None))
    run_cli(tmp_path, names, recipes, extra=["--deadline-min", "0"])
    run = core.read_snapshot(tmp_path / "snapshots"
                             / "2026-11-05-0630")["run"]
    assert run["targets_planned"] == 2
    assert run["objects_written"] == 0
    assert run["targets_late"] == ["a", "b"]


def test_late_target_is_not_signed_as_probed(tmp_path, monkeypatch):
    """Цель, которую пробник не трогал, не подписывается его версией.

    Иначе через полгода такой снимок неотличим от настоящего снятого — а
    ровно эту неотличимость поля провенанса и заводились убрать (тикет 10).
    """
    recipes = write_recipes(tmp_path, ["a"])
    monkeypatch.setattr(cli.probes, "run_recipe",
                        lambda u, r, f, t, **kw: (snapped(u), None))
    run_cli(tmp_path, ["a"], recipes, extra=["--deadline-min", "0"])
    obj = core.read_snapshot(tmp_path / "snapshots"
                             / "2026-11-05-0630")["objects"]["a"]
    assert "probe_version" not in obj
    assert "recipe_hash" not in obj
    assert obj["run_planned_at"]          # к прогону снимок всё равно привязан


# ---------------------------------------------------------------------------
# Волна 4: прогон закрывается ВСЕГДА (ревью wave3-core, major 1)
# ---------------------------------------------------------------------------
#
# Волна 3 закрыла один ВИД сбоя — брак проверки схемы у объекта. Класс остался
# открыт: любое необработанное исключение между open_snapshot и close_snapshot
# оставляло на диске каталог прогона без finished_at (слой сезонности такой
# прогон в работу не берёт — и правильно) и вылетало наверх, в тот же процесс
# планировщика: сводка не пересобиралась, ledger не писался, дайджест не уходил
# и строки в CF Run Log не было. Тот же исход, против которого писался тикет 02,
# только через другую дверь.

def test_crash_inside_the_run_still_closes_the_snapshot(tmp_path, monkeypatch,
                                                        capsys):
    names = ["a"]
    recipes = write_recipes(tmp_path, names)

    def boom(*a, **kw):
        raise AttributeError("'list' object has no attribute 'get'")

    monkeypatch.setattr(cli, "_host_queues", boom)
    rc = run_cli(tmp_path, names, recipes)
    assert rc == 2
    run = core.read_snapshot(tmp_path / "snapshots"
                             / "2026-11-05-0630")["run"]
    assert run["finished_at"]                     # прогон ЗАКРЫТ
    assert "AttributeError" in run["error"]       # и сказано, чем оборвался
    err = capsys.readouterr().err
    assert "Traceback" not in err


def test_crash_never_escapes_to_the_scheduler(tmp_path, monkeypatch, capsys):
    """cli.main зовётся В ТОМ ЖЕ процессе, что и планировщик (run_scheduled).

    Исключение наружу — это не код 2, а traceback вместо всего хвоста прогона.
    """
    recipes = write_recipes(tmp_path, ["a"])

    def boom(*a, **kw):
        raise RuntimeError("диск отвалился на открытии снапшота")

    monkeypatch.setattr(cli.core, "open_snapshot", boom)
    rc = run_cli(tmp_path, ["a"], recipes)
    assert rc == 2
    assert "диск отвалился" in capsys.readouterr().err


def test_systemd_kill_still_leaves_the_run_open(tmp_path, monkeypatch):
    """SIGTERM/Ctrl+C прогон НЕ закрывают: «оборвали» и «закрыт» — разное."""
    names = ["a", "b"]
    recipes = write_recipes(tmp_path, names)

    def fake_run(username, rec, date_from, date_to, **kw):
        if username == "b":
            raise KeyboardInterrupt("systemd прибил прогон")
        return snapped(username), None

    monkeypatch.setattr(cli.probes, "run_recipe", fake_run)
    with pytest.raises(KeyboardInterrupt):
        run_cli(tmp_path, names, recipes)
    run = json.loads((tmp_path / "snapshots" / "2026-11-05-0630" / "run.json")
                     .read_text(encoding="utf-8"))
    assert "finished_at" not in run


def test_recipe_of_a_wrong_shape_costs_one_row_not_the_day(tmp_path,
                                                           monkeypatch):
    """У рецепта request списком (правка руки, ошибка разведчика) — своя строка.

    Группировка целей по хосту — это ключ очереди, а не проверка рецепта:
    падать на ней значит терять ВЕСЬ прогон из-за одной кривой записи.
    """
    good = recipe()
    bad = recipe()
    bad["request"] = ["url_template", "https://x/{date_from}"]
    core.save_recipes(tmp_path / "recipes.json", {"a": good, "bad": bad})
    monkeypatch.setattr(cli.probes, "run_recipe",
                        lambda u, r, f, t, **kw: (snapped(u), None))
    rc = run_cli(tmp_path, ["a", "bad"], tmp_path / "recipes.json")
    assert rc == 0
    snap = core.read_snapshot(tmp_path / "snapshots" / "2026-11-05-0630")
    assert set(snap["objects"]) == {"a", "bad"}
    assert snap["run"]["finished_at"]


# ---------------------------------------------------------------------------
# Волна 4: замок держит ХОСТЫ, а не каталог (ревью wave3-core, major 2)
# ---------------------------------------------------------------------------
#
# Проверочный прогон уходит в песочницу с КОПИЕЙ реестра и своим каталогом
# снапшотов — оба прежних замка при этом расходятся с плановым, и два процесса
# идут по одним и тем же чужим хостам. Пауза >= 1.2 с к хосту процессами не
# делится: она модульная (probes/_common), то есть живёт внутри процесса.

def test_live_run_refuses_while_another_live_run_holds_the_machine(
        tmp_path, monkeypatch, capsys):
    """Песочница уводит реестр и снапшоты — хосты у неё те же самые."""
    recipes = write_recipes(tmp_path, ["a"])
    sandbox = tmp_path / "песочница" / "recipes.json"
    sandbox.parent.mkdir(parents=True)
    core.save_recipes(sandbox, {"a": recipe()})
    monkeypatch.setattr(cli.probes, "run_recipe",
                        lambda u, r, f, t, **kw: (snapped(u), None))
    with core.run_lock(core.host_lock_path()):
        rc = cli.main(["--recipes", str(sandbox),
                       "--snapshot-root", str(tmp_path / "песочница" / "snap"),
                       "--run-id", "2026-11-05-0630", "--probe", "a"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "живой съём" in err and "Traceback" not in err
    assert recipes.exists()


def test_fixture_run_does_not_hold_the_machine(tmp_path):
    """Fixture-режим в сеть не ходит — чужим хостам он не мешает."""
    fixture = tmp_path / "grid.json"
    fixture.write_text(json.dumps(snapped("f")), encoding="utf-8")
    with core.run_lock(core.host_lock_path()):
        rc = cli.main(["--snapshot-root", str(tmp_path / "snapshots"),
                       "--run-id", "2026-11-05-0630",
                       "--fixture", str(fixture)])
    assert rc == 0


def test_machine_lock_does_not_depend_on_the_paths_of_the_run(monkeypatch, tmp_path):
    """Один замок на машину: от --recipes и --snapshot-root он не зависит."""
    monkeypatch.delenv(core.HOST_LOCK_ENV, raising=False)
    assert core.host_lock_path() == core.DEFAULT_HOST_LOCK
    own = tmp_path / "свой.lock"           # витрина: без /tmp — тест идёт и на Windows
    monkeypatch.setenv(core.HOST_LOCK_ENV, str(own))
    assert core.host_lock_path() == own


# ---------------------------------------------------------------------------
# Волна 4: снято — это стоило запроса (ревью wave3-core, minor 1 и 2)
# ---------------------------------------------------------------------------

def test_broken_recipe_is_neither_written_nor_signed(tmp_path, monkeypatch):
    """Рецепт broken диспетчер отбивает БЕЗ единого запроса.

    Завтра это уже сработает: shale_aframe идёт в очередь с рецептом
    status=broken и записался бы как снятый, с чужой подписью пробника.
    """
    rec = recipe()
    rec["status"] = "broken"
    rec["broken_reason"] = "схема сменилась"
    core.save_recipes(tmp_path / "recipes.json", {"a": rec})
    rc = run_cli(tmp_path, ["a"], tmp_path / "recipes.json")
    assert rc == 0
    snap = core.read_snapshot(tmp_path / "snapshots" / "2026-11-05-0630")
    assert snap["run"]["targets_planned"] == 1
    assert snap["run"]["objects_written"] == 0
    obj = snap["objects"]["a"]
    assert obj["status"] == "insufficient_data"
    assert "probe_version" not in obj and "recipe_hash" not in obj


def test_unsupported_engine_is_neither_written_nor_signed(tmp_path):
    rec = recipe(engine="чего-то-нет")
    core.save_recipes(tmp_path / "recipes.json", {"a": rec})
    assert run_cli(tmp_path, ["a"], tmp_path / "recipes.json") == 0
    snap = core.read_snapshot(tmp_path / "snapshots" / "2026-11-05-0630")
    assert snap["run"]["objects_written"] == 0
    assert "probe_version" not in snap["objects"]["a"]


def test_probed_target_is_still_counted_and_signed(tmp_path, monkeypatch):
    """Обратная сторона: настоящий съём считается и подписывается, как раньше."""
    recipes = write_recipes(tmp_path, ["a"])
    monkeypatch.setattr(cli.probes, "run_recipe",
                        lambda u, r, f, t, **kw: (snapped(u), None))
    assert run_cli(tmp_path, ["a"], recipes) == 0
    snap = core.read_snapshot(tmp_path / "snapshots" / "2026-11-05-0630")
    assert snap["run"]["objects_written"] == 1
    assert snap["objects"]["a"]["probe_version"].startswith("travelline@")


# ---------------------------------------------------------------------------
# Волна 4: бюджет на объект РЕЖЕТ, а не клеит ярлык (ревью wave3-core)
# ---------------------------------------------------------------------------

def test_per_target_budget_reaches_the_transport(tmp_path, monkeypatch):
    """Бюджет объекта живёт в транспорте: он не даёт слать НОВЫЕ запросы.

    Прежде он только мерил время ПОСЛЕ возврата пробника и переклеивал ярлык
    на partial, а начатая цель шла до конца — обещанные полчаса запаса на
    хвост не держались ничем.
    """
    recipes = write_recipes(tmp_path, ["a"])
    seen = []

    def fake_run(username, rec, date_from, date_to, **kw):
        seen.append(cli.transport.budget_left())
        return snapped(username), None

    monkeypatch.setattr(cli.probes, "run_recipe", fake_run)
    assert run_cli(tmp_path, ["a"], recipes,
                   extra=["--per-target-max-sec", "42"]) == 0
    assert seen and 0 < seen[0] <= 42
    # за пределами съёма бюджета нет: разведчик и сводка им не связаны
    assert cli.transport.budget_left() is None


# ---------------------------------------------------------------------------
# Волна 4: дыра в ряду видна из снапшота (ревью wave3-core, gap_days)
# ---------------------------------------------------------------------------

def test_run_records_how_long_the_row_was_silent(tmp_path, monkeypatch):
    """Догон после простоя машины виден по РЯДУ, а не по gap_days.

    gap_days меряет опоздание прогона от его планового момента и на догоне
    честно даёт 0: ночь снята сегодня, просто позже утра. «Машина молчала
    двое суток» — это дыра между прогонами, и она пишется отдельным полем.
    """
    recipes = write_recipes(tmp_path, ["a"])
    monkeypatch.setattr(cli.probes, "run_recipe",
                        lambda u, r, f, t, **kw: (snapped(u), None))
    run_cli(tmp_path, ["a"], recipes, run_id="2026-11-02-0630")
    run_cli(tmp_path, ["a"], recipes, run_id="2026-11-05-1200")
    run = core.read_snapshot(tmp_path / "snapshots"
                             / "2026-11-05-1200")["run"]
    assert run["prev_run_id"] == "2026-11-02-0630"
    assert run["days_since_prev_run"] == 3


def test_broken_yesterday_does_not_cost_today(tmp_path, monkeypatch, capsys):
    """Битый вчерашний снапшот стоит строки динамики, а не сегодняшнего дня."""
    recipes = write_recipes(tmp_path, ["a"])
    monkeypatch.setattr(cli.probes, "run_recipe",
                        lambda u, r, f, t, **kw: (snapped(u), None))
    old = tmp_path / "snapshots" / "2026-11-04-0630"
    old.mkdir(parents=True)
    (old / "run.json").write_text("{это не JSON", encoding="utf-8")
    assert run_cli(tmp_path, ["a"], recipes) == 0
    snap = core.read_snapshot(tmp_path / "snapshots" / "2026-11-05-0630")
    assert snap["objects"]["a"]["status"] == "ok"
    assert "не читается" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Волна 5 (ревью wave4-core): очередь хоста и закрытие прогона
# ---------------------------------------------------------------------------
#
# Пункт 2. Общий except стоит вокруг ОДНОГО вызова пробника (_probe_one), а в
# потоке съёма после него есть ещё подпись снимка, запись на диск и отчёт в
# журнал. Исключение оттуда обрывало ВСЮ очередь своего хоста: оставшиеся
# цели не получали в снапшоте ни одной строки — ни съёма, ни причины. На
# reservationsteps.ru после разведки 04.09 таких целей 22 в одной очереди.

def test_crash_outside_the_probe_guard_costs_one_target_not_the_queue(
        tmp_path, monkeypatch, capsys):
    names = ["a", "b", "c"]           # один хост -> одна очередь, по порядку
    recipes = write_recipes(tmp_path, names)
    monkeypatch.setattr(cli.probes, "run_recipe",
                        lambda u, r, f, t, **kw: (snapped(u), None))
    real_stamp = cli.core.stamp_provenance

    def stamp(obj, **kw):
        if obj["username"] == "a":
            raise TypeError("подпись снимка споткнулась о форму рецепта")
        return real_stamp(obj, **kw)

    monkeypatch.setattr(cli.core, "stamp_provenance", stamp)
    rc = run_cli(tmp_path, names, recipes)

    snap = core.read_snapshot(tmp_path / "snapshots" / "2026-11-05-0630")
    assert set(snap["objects"]) == {"a", "b", "c"}   # очередь дошла до конца
    assert snap["objects"]["b"]["status"] == "ok"
    assert snap["objects"]["c"]["status"] == "ok"
    fallen = snap["objects"]["a"]
    assert fallen["status"] == "insufficient_data"
    assert "TypeError" in fallen["reason"]
    assert snap["run"]["finished_at"]
    assert snap["run"]["targets_degraded"] == ["a"]
    assert rc == 1                    # сигнал есть, а день на диске цел
    err = capsys.readouterr().err
    assert "a" in err and "Traceback" not in err


def test_a_fallen_target_is_not_counted_as_snapped(tmp_path, monkeypatch):
    """Строка об обрыве — это причина, а не съём: в objects_written её нет."""
    names = ["a", "b"]
    recipes = write_recipes(tmp_path, names)
    monkeypatch.setattr(cli.probes, "run_recipe",
                        lambda u, r, f, t, **kw: (snapped(u), None))
    real_stamp = cli.core.stamp_provenance
    monkeypatch.setattr(
        cli.core, "stamp_provenance",
        lambda obj, **kw: (_ for _ in ()).throw(TypeError("бум"))
        if obj["username"] == "a" else real_stamp(obj, **kw))
    run_cli(tmp_path, names, recipes)

    run = core.read_snapshot(tmp_path / "snapshots" / "2026-11-05-0630")["run"]
    assert run["objects_written"] == 1
    assert run["targets_planned"] == 2


def test_the_recipe_of_a_fallen_target_is_not_broken(tmp_path, monkeypatch):
    """Обрыв в НАШЕМ коде — не повод слать чужой рецепт на переразведку."""
    recipes = write_recipes(tmp_path, ["a"])
    monkeypatch.setattr(cli.probes, "run_recipe",
                        lambda u, r, f, t, **kw: (snapped(u), None))
    monkeypatch.setattr(cli.core, "stamp_provenance",
                        lambda obj, **kw: (_ for _ in ()).throw(
                            TypeError("бум")))
    run_cli(tmp_path, ["a"], recipes)

    assert core.load_recipes(recipes)["a"]["status"] == "ok"


# Пункт 4. Провал закрытия прогона печатался в stderr и отдавал код 0:
# планировщик считал день удачным (сводка, ledger, «success» в Run Log), а
# слой сезонности незакрытый прогон не берёт НИКОГДА — день терялся молча.

def test_failure_to_close_the_run_is_never_a_success(tmp_path, monkeypatch,
                                                     capsys):
    recipes = write_recipes(tmp_path, ["a"])
    monkeypatch.setattr(cli.probes, "run_recipe",
                        lambda u, r, f, t, **kw: (snapped(u), None))

    def boom(*a, **kw):
        raise core.SnapshotError("run.json не пишется: на диске нет места")

    monkeypatch.setattr(cli.core, "close_snapshot", boom)
    rc = run_cli(tmp_path, ["a"], recipes)

    assert rc == 2, "код 0 = «день удачный» при незакрытом прогоне"
    err = capsys.readouterr().err
    assert "не закрыт" in err.lower() and "нет места" in err
    assert "Traceback" not in err


def test_a_crash_after_the_row_is_written_keeps_the_data(tmp_path,
                                                         monkeypatch):
    """Упало ПОСЛЕ записи снятого (строка в журнал) — данные дороже строки
    об обрыве: перезаписывать снятый объект честной пустотой нельзя."""
    recipes = write_recipes(tmp_path, ["a"])
    monkeypatch.setattr(cli.probes, "run_recipe",
                        lambda u, r, f, t, **kw: (snapped(u), None))
    real_host = cli._recipe_host
    calls = {"n": 0}

    def host(recipe):
        calls["n"] += 1
        if calls["n"] > 1:              # первый зов — очередь, второй — журнал
            raise RuntimeError("журнал споткнулся")
        return real_host(recipe)

    monkeypatch.setattr(cli, "_recipe_host", host)
    rc = run_cli(tmp_path, ["a"], recipes)

    snap = core.read_snapshot(tmp_path / "snapshots" / "2026-11-05-0630")
    assert snap["objects"]["a"]["status"] == "ok"      # съём на месте
    assert snap["run"]["targets_degraded"] == ["a"]    # но сбой не скрыт
    assert rc == 1
