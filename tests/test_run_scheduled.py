# -*- coding: utf-8 -*-
"""Плановый прогон без агента: очередь целей, один снапшот, каскад кодов.

Точка входа cf-occupancy.timer (16.08.2026, решение владельца «ставим Pine
River на постоянную слежку, отдельным таймером»). Проверяем ровно то, что
ломается молча: порядок и полноту очереди, единственность вызова пробников
(два вызова = два каталога снапшота = поехавшая динамика) и то, что при сбое
пробников сводка НЕ пересобирается по неполным данным.
"""
import json
from datetime import date, datetime
from pathlib import Path

import pytest

import build_summary
import cli
import occupancy_core as core
import run_scheduled


@pytest.fixture(autouse=True)
def _runtime_state_isolated(monkeypatch, tmp_path):
    """Боевые следы прогона — мимо тестов.

    Состояние повторов и каталог копий по умолчанию указывают в
    agent-runtime репозитория: тест, прошедший боевым путём (а такие здесь
    есть — они подменяют core.DEFAULT_SNAPSHOT_ROOT), клал в живой бэкап
    выдуманный прогон и съедал у следующего теста сообщение как «повтор».
    """
    monkeypatch.setattr(run_scheduled, "NOTIFY_STATE_FILE",
                        tmp_path / "notify-state.json")
    monkeypatch.setattr(run_scheduled, "DEFAULT_BACKUP_DIR",
                        tmp_path / "backup-по-умолчанию")
    # Слой сезонности — тот самый ряд, ради которого всё делается: боевое
    # умолчание уводим целиком, чтобы ни один тест (в том числе прогон с
    # подменённым боевым каталогом снапшотов) не мог в него дописать.
    monkeypatch.setattr(run_scheduled.ledger, "DEFAULT_LEDGER_DIR",
                        tmp_path / "слой-по-умолчанию")


def write_targets(tmp_path: Path, targets) -> str:
    path = tmp_path / "targets.json"
    path.write_text(json.dumps(targets, ensure_ascii=False), encoding="utf-8")
    return str(path)


def write_recipes(tmp_path: Path, usernames) -> str:
    """Рецепты знакомого движка на каждое имя — чтобы цель попала в очередь."""
    path = tmp_path / "recipes.json"
    path.write_text(json.dumps({u: {"engine": "travelline", "status": "ok"}
                                for u in usernames}, ensure_ascii=False),
                    encoding="utf-8")
    return str(path)


def args_for(tmp_path: Path, targets, extra=()):
    """Аргументы main для целей: файл целей + рецепты под них.

    Снапшот уводится во временный каталог, а ВСЕ прочие выходы прогона (слой
    сезонности, сводка, копия, реестр) обязаны уйти за ним САМИ. Раньше здесь
    стоял явный --ledger-dir, и он прятал ровно тот дефект, из-за которого
    проверочный прогон дописывал выдуманные прогоны в боевой слой: умолчание
    ключа не проверял ни один тест.
    """
    names = [t["username"] for t in targets if t.get("username")]
    return (["--targets", write_targets(tmp_path, targets),
             "--recipes", write_recipes(tmp_path, names),
             "--snapshot-root", str(tmp_path / "snapshots")] + list(extra))


class Spy:
    """Заглушка вызова: помнит все argv, отвечает заданным кодом."""

    def __init__(self, code=0):
        self.calls = []
        self.code = code

    def __call__(self, argv=None):
        self.calls.append(list(argv or []))
        return self.code


MODULE = {"engine": "travelline", "status": "ok"}
BROKEN = {"engine": "bronirui", "status": "broken"}
AGGREGATOR = {"engine": "aggregator-ostrovok", "status": "aggregator"}


def test_probe_args_keeps_queue_order():
    """Порядок строк targets = важность = порядок строк сводки: не сортируем."""
    assert run_scheduled.probe_args(["b", "a"]) == [
        "--probe", "b", "--probe", "a"]


def test_split_queue_keeps_file_order_and_skips_nameless_rows():
    """В targets.json первой строкой лежит _note-объект без username."""
    targets = [{"_note": "как править файл", "username": "первый"},
               {"priority": 5},
               {"username": "второй"}]
    queue, skipped = run_scheduled.split_queue(
        targets, {"первый": MODULE, "второй": MODULE})
    assert queue == ["первый", "второй"]
    assert skipped == []


def test_aggregator_targets_are_left_to_the_agent():
    """Пустой снимок агрегатора становился последним и ЗАТИРАЛ в сводке
    живые цифры, которые агент снял браузером (регресс 18.08)."""
    targets = [{"username": "модульный"}, {"username": "агрегатор"}]
    queue, skipped = run_scheduled.split_queue(
        targets, {"модульный": MODULE, "агрегатор": AGGREGATOR})
    assert queue == ["модульный"]
    assert skipped == [("агрегатор",
                        "канал aggregator-ostrovok — снимает агент браузером, "
                        "пробника v1 нет", "aggregator")]


def test_broken_recipe_still_goes_into_the_run():
    """Строка «нужна переразведка» — это и есть сигнал позвать агента,
    её нельзя прятать пропуском."""
    queue, skipped = run_scheduled.split_queue([{"username": "x"}],
                                               {"x": BROKEN})
    assert queue == ["x"] and skipped == []


def test_skip_groups_separate_work_from_a_finished_verdict():
    """Пропуск группируется по тому, ЖДЁТ ли строка человека.

    До 09.09.2026 «пропущено 38» читалось владельцем как 38 невыполненных
    дел, хотя 18 из них — объекты с проверенным выводом «онлайн-канала нет».
    """
    recipes = {
        "разведан": MODULE,
        "агрегатор": AGGREGATOR,
        "канала нет": {"site": "https://x.ru", "engine": "none",
                       "status": "no_module",
                       "discovered_at": "2026-09-04T18:40:00+03:00"},
        "чужой движок": {"site": "https://y.ru", "engine": "custom-livewire",
                         "status": "broken",
                         "broken_reason": "движок не поддержан v1"},
    }
    targets = [{"username": name} for name in recipes] + [{"username": "новый"}]
    queue, skipped = run_scheduled.split_queue(targets, recipes)

    assert queue == ["разведан"]
    groups = {name: group for name, _, group in skipped}
    assert groups == {"агрегатор": "aggregator", "канала нет": "no_module",
                      "чужой движок": "scout", "новый": "scout"}
    why = {name: reason for name, reason, _ in skipped}
    # у объекта без канала — дата проверки, а не призыв разведать
    assert why["канала нет"] == ("без онлайн-канала бронирования, "
                                 "проверено 04.09.2026")
    assert "разведк" not in why["канала нет"]
    assert "движок не поддержан v1" in why["чужой движок"]


def test_unknown_engine_is_not_printed_as_an_engine_name():
    """«unknown» — отметка разведки «опознать не вышло», а не имя движка.

    scout_apply пишет её честной записью low/refuse, и строка «движок
    'unknown' не поддержан пробником» выдумывала движок с таким именем.
    """
    _, skipped = run_scheduled.split_queue(
        [{"username": "x"}],
        {"x": {"engine": "unknown", "status": "broken",
               "broken_reason": "маркеров модуля бронирования нет"}})
    (_, why, group), = skipped
    assert group == "scout"
    assert why == ("движок не опознан — ждёт разведки: маркеров модуля "
                   "бронирования нет")


def test_run_report_counts_the_skipped_by_group():
    """Счёт по группам считает тот же, кто считает долю пустых."""
    snapshot = {"objects": {"a": {"status": "ok", "units": {
                    "Дом": {"2026-09-04": {"state": "free"}}}}},
                "run": {"targets": ["a"]}}
    report = run_scheduled.run_report(
        snapshot,
        skipped=[("x", "нет рецепта", "scout"),
                 ("y", "канала нет", "no_module"),
                 ("z", "канала нет", "no_module")])
    assert report["skipped_groups"] == {"scout": 1, "aggregator": 0,
                                        "no_module": 2}
    assert sum(report["skipped_groups"].values()) == len(report["skipped"])


def test_target_without_recipe_is_skipped_with_reason():
    queue, skipped = run_scheduled.split_queue([{"username": "новый"}], {})
    assert queue == []
    assert skipped == [("новый", "рецепта нет в реестре — ждёт разведки",
                        "scout")]


def test_all_targets_go_in_one_probe_call(tmp_path):
    """Один вызов cli = один каталог снапшота. Два вызова порвали бы динамику."""
    targets = [{"username": "x"}, {"username": "y"}, {"username": "z"}]
    argv = args_for(tmp_path, targets)
    make_snapshot(tmp_path, {u["username"]: ("ok", "") for u in targets})
    probe, summarise, logged = Spy(), Spy(), []
    code = run_scheduled.main(argv, probe=probe, summarise=summarise,
                              logger=lambda *a, **kw: logged.append(a))
    assert code == 0
    assert len(probe.calls) == 1
    assert probe.calls[0][:6] == ["--probe", "x", "--probe", "y", "--probe", "z"]
    assert summarise.calls[0][:2] == ["--targets", argv[1]]
    assert "--snapshot-root" in summarise.calls[0]
    assert logged and logged[0][1] == 3


def test_run_stops_when_nobody_is_probeable(tmp_path):
    """Все цели — работа агента: снимать нечего, сводку не трогаем."""
    path = write_targets(tmp_path, [{"username": "art.glamp"}])
    recipes = tmp_path / "recipes.json"
    recipes.write_text('{"art.glamp": {"engine": "none"}}', encoding="utf-8")
    probe, summarise = Spy(), Spy()
    code = run_scheduled.main(["--targets", path, "--recipes", str(recipes),
                               "--snapshot-root", str(tmp_path / "snapshots")],
                              probe=probe, summarise=summarise,
                              logger=lambda *a, **kw: None,
                              notifier=lambda *a, **kw: (0, 0, 0))
    assert code == 2
    assert probe.calls == [] and summarise.calls == []


def test_no_inventory_is_forwarded(tmp_path):
    probe = Spy()
    run_scheduled.main(args_for(tmp_path, [{"username": "x"}],
                                ["--no-inventory"]),
                       probe=probe, summarise=Spy(), logger=lambda *a, **kw: None)
    assert probe.calls[0][-1] == "--no-inventory"


def test_dry_run_touches_nothing(tmp_path, capsys):
    probe, summarise = Spy(), Spy()
    code = run_scheduled.main(
        args_for(tmp_path, [{"username": "x"}, {"username": "y"}],
                 ["--dry-run"]),
        probe=probe, summarise=summarise, logger=lambda *a, **kw: None)
    assert code == 0
    assert probe.calls == [] and summarise.calls == []
    assert "целей в очереди: 2" in capsys.readouterr().out


def test_probe_failure_stops_before_summary(tmp_path):
    """Сводка по неполному прогону хуже отсутствия сводки: не пересобираем.

    Но САМ обрыв обязан быть слышен: правило №6 завода требует строки в Run
    Log именно там, где след нужнее всего (занятая блокировка, битый реестр,
    исключение из потока пробника), а владелец — сообщения в чат."""
    summarise, logged, notified = Spy(), [], []
    code = run_scheduled.main(
        args_for(tmp_path, [{"username": "x"}]),
        probe=Spy(code=2), summarise=summarise,
        logger=lambda *a, **kw: logged.append((a, kw)),
        notifier=lambda t, m, **kw: notified.append(kw["report"]) or (1, 1))
    assert code == 2
    assert summarise.calls == []
    assert logged and logged[0][1]["status"] == "failed"
    assert "пробник" in logged[0][1]["errors"][0]
    assert notified and "пробник" in notified[0]["alert"]


def test_summary_failure_is_reported(tmp_path):
    """Сводка не собралась — это тоже отказ прогона, а не тишина."""
    logged, notified = [], []
    code = run_scheduled.main(
        args_for(tmp_path, [{"username": "x"}]),
        probe=Spy(), summarise=Spy(code=2),
        logger=lambda *a, **kw: logged.append((a, kw)),
        notifier=lambda t, m, **kw: notified.append(kw["report"]) or (1, 1))
    assert code == 2
    assert logged and logged[0][1]["status"] == "failed"
    assert notified and "сводк" in notified[0]["alert"]


def test_broken_run_tells_only_the_summary_not_the_targets(tmp_path):
    """Аварийный выход не ходит за штучными сводками целей: снапшот оборван,
    и читать по нему историю значит слать цифры вчерашнего дня как свежие."""
    seen = {}
    run_scheduled.main(
        args_for(tmp_path, [{"username": "x", "notify": True}]),
        probe=Spy(code=2), summarise=Spy(),
        logger=lambda *a, **kw: None,
        notifier=lambda t, m, **kw: seen.update(targets=t) or (1, 1))
    assert seen["targets"] == []


def test_missing_targets_file_is_an_error(tmp_path):
    code = run_scheduled.main(["--targets", str(tmp_path / "нет.json"),
                               "--snapshot-root", str(tmp_path / "snapshots")],
                              probe=Spy(), summarise=Spy(),
                              logger=lambda *a, **kw: None,
                              notifier=lambda *a, **kw: (0, 0, 0))
    assert code == 2


def test_empty_targets_is_an_error(tmp_path):
    path = write_targets(tmp_path, [])
    code = run_scheduled.main(["--targets", path,
                               "--snapshot-root", str(tmp_path / "snapshots")],
                              probe=Spy(), summarise=Spy(),
                              logger=lambda *a, **kw: None,
                              notifier=lambda *a, **kw: (0, 0, 0))
    assert code == 2


def test_log_run_failure_does_not_break_the_run(monkeypatch, capsys):
    """Журнал недоступен — цифры уже сняты, прогон падать не должен."""
    def boom(*a, **kw):
        raise OSError("нет сети до Sheets")
    monkeypatch.setattr(run_scheduled.subprocess, "run", boom)
    run_scheduled.log_run("2026-08-16T06:30:00+03:00", 25, ["снапшот"])
    assert "Run Log не записан" in capsys.readouterr().err


def test_log_run_sends_started_at_and_outputs(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return None
    monkeypatch.setattr(run_scheduled.subprocess, "run", fake_run)
    run_scheduled.log_run("2026-08-16T06:30:00+03:00", 25, ["снапшот", "свод"])
    cmd = seen["cmd"]
    assert "log-run" in cmd
    assert "--started-at" in cmd
    assert cmd[cmd.index("--started-at") + 1] == "2026-08-16T06:30:00+03:00"
    assert cmd[-2:] == ["снапшот", "свод"]


# ---------------------------------------------------------------------------
# Уведомления в телеграм (просьба оператора 16.08)
# ---------------------------------------------------------------------------

def test_notifier_is_called_after_summary_not_before(tmp_path):
    """Сводка сначала, уведомление потом: сообщение о цифрах, которых ещё
    нет в файлах, читателя обманывает. Ledger между ними: он читает тот же
    снапшот, а отчёты сезонности читают уже его."""
    order = []
    run_scheduled.main(
        args_for(tmp_path, [{"username": "x", "notify": True}]),
        probe=lambda a: order.append("probe") or 0,
        summarise=lambda a: order.append("summary") or 0,
        ledger_append=lambda root, out: order.append("ledger"),
        notifier=lambda t, m, **kw: order.append("notify"),
        logger=lambda *a, **kw: order.append("log"))
    assert order == ["probe", "summary", "ledger", "notify", "log"]


def test_notifier_gets_targets_and_months(tmp_path):
    seen = {}
    run_scheduled.main(
        args_for(tmp_path, [{"username": "x", "notify": True}],
                 ["--months", "2026-08,2026-09"]),
        probe=Spy(), summarise=Spy(), logger=lambda *a, **kw: None,
        notifier=lambda t, m, **kw: seen.update(targets=t, months=m))
    assert seen["months"] == ["2026-08", "2026-09"]
    assert seen["targets"][0]["username"] == "x"


def test_failed_probe_sends_only_the_alert(tmp_path):
    """Прогон не удался — цифр не шлём (сообщение по старым данным хуже
    молчания), но сам отказ уходит одной строкой: иначе единственный сигнал
    остаётся в systemd-алерте, а он ходит тем же заглушаемым транспортом."""
    seen = []
    run_scheduled.main(args_for(tmp_path, [{"username": "x", "notify": True}]),
                       probe=Spy(code=2), summarise=Spy(),
                       notifier=lambda t, m, **kw: seen.append(kw["report"]),
                       logger=lambda *a, **kw: None)
    assert len(seen) == 1 and seen[0]["alert"] and seen[0]["total"] == 0


def test_notify_targets_picks_last_two_snapshots(tmp_path, monkeypatch):
    """Уведомление считает динамику по тем же двум снимкам, что и сводка."""
    seen = {}
    monkeypatch.setattr(run_scheduled.build_summary, "load_history",
                        lambda root, depth=None: {"x": [("r1", {"units": {}, "a": 1}),
                                            ("r2", {"units": {}, "a": 2}),
                                            ("r3", {"units": {}, "a": 3})]})
    monkeypatch.setattr(run_scheduled.tg_digest, "digest",
                        lambda t, o, p, m, d, **kw: seen.update(cur=o, prev=p) or "текст")
    sent, queued, muffled = run_scheduled.notify_targets(
        [{"username": "x", "notify": True}], ["2026-08"],
        sender=lambda text: True)
    assert (sent, queued, muffled) == (1, 1, 0)
    assert seen["cur"]["a"] == 3 and seen["prev"]["a"] == 2


def test_notify_targets_skips_objects_without_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr(run_scheduled.build_summary, "load_history",
                        lambda root, depth=None: {})
    sent, queued, _ = run_scheduled.notify_targets(
        [{"username": "x", "notify": True}], ["2026-08"],
        sender=lambda text: True)
    assert sent == 0 and queued == 0


def test_notify_targets_ignores_targets_without_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(run_scheduled.build_summary, "load_history",
                        lambda root, depth=None: {"x": [("r1", {"units": {}})]})
    run_scheduled.notify_targets([{"username": "x"}], ["2026-08"],
                                 sender=lambda text: calls.append(text) or True)
    assert calls == []


def test_telegram_failure_does_not_break_the_run(monkeypatch, capsys):
    """Чат недоступен — цифры уже сняты, прогон падать не должен."""
    def boom(*a, **kw):
        raise RuntimeError("нет сети")
    monkeypatch.setattr("cf.notify.notify_telegram", boom)
    assert run_scheduled.send_telegram("текст") is False
    assert "уведомление не ушло" in capsys.readouterr().err


def test_send_telegram_reports_refusal_without_raising(monkeypatch):
    """Телеграм не настроен: notify_telegram отвечает False, не бросая."""
    monkeypatch.setattr("cf.notify.notify_telegram", lambda *a, **kw: False)
    assert run_scheduled.send_telegram("текст") is False


# ---------------------------------------------------------------------------
# Общий контракт волны 2: дедлайн, плановый момент, горизонт фонда
# ---------------------------------------------------------------------------

def make_snapshot(tmp_path, objects, late=(), run_id="2026-09-04-0630",
                  units_of=None):
    """Каталог прогона на диске, как его пишет cli: run.json + файл на объект.

    objects: {username: (status, reason)} — сетка ставится непустой всем, у
    кого статус не insufficient_data.
    units_of: {username: сетка} — своя сетка цели (нужно там, где проверяется
    РАЗБОР сетки, а не статус: горизонт из одних unknown).
    """
    units_of = units_of or {}
    snap = tmp_path / "snapshots" / run_id
    snap.mkdir(parents=True)
    (snap / "run.json").write_text(json.dumps(
        {"run_id": run_id, "targets": list(objects), "targets_late": list(late)},
        ensure_ascii=False), encoding="utf-8")
    for username, (status, reason) in objects.items():
        units = ({} if status == "insufficient_data"
                 else {"Дом": {"2026-09-04": {"state": "free"}}})
        units = units_of.get(username, units)
        (snap / f"{username}.json").write_text(json.dumps(
            {"username": username, "status": status, "reason": reason,
             "units": units}, ensure_ascii=False), encoding="utf-8")
    return snap


def test_probe_gets_deadline_planned_at_and_inventory_days(tmp_path):
    """Ключи волны 2 доходят до cli: без них дедлайн и gap_days мертвы."""
    probe = Spy()
    run_scheduled.main(args_for(tmp_path, [{"username": "x"}],
                                ["--planned-at", "2026-09-04T06:30:00+03:00"]),
                       probe=probe, summarise=Spy(),
                       logger=lambda *a, **kw: None,
                       notifier=lambda *a, **kw: 0,
                       ledger_append=lambda root, out: None)
    argv = probe.calls[0]
    assert argv[argv.index("--deadline-min") + 1] == "150"
    assert argv[argv.index("--inventory-days") + 1] == "45"
    assert argv[argv.index("--planned-at") + 1] == "2026-09-04T06:30:00+03:00"


def test_deadline_and_inventory_days_are_overridable(tmp_path):
    probe = Spy()
    run_scheduled.main(args_for(tmp_path, [{"username": "x"}],
                                ["--deadline-min", "30",
                                 "--inventory-days", "10"]),
                       probe=probe, summarise=Spy(),
                       logger=lambda *a, **kw: None,
                       notifier=lambda *a, **kw: 0,
                       ledger_append=lambda root, out: None)
    argv = probe.calls[0]
    assert argv[argv.index("--deadline-min") + 1] == "30"
    assert argv[argv.index("--inventory-days") + 1] == "10"


def test_planned_moment_is_todays_timer_slot():
    """Прогон стартовал после планового часа — плановое сегодняшнее."""
    now = datetime(2026, 9, 4, 6, 47, 12)
    assert run_scheduled.planned_moment("06:30", now) == datetime(
        2026, 9, 4, 6, 30)


def test_planned_moment_falls_back_to_yesterday_before_the_slot():
    """Догон после простоя ночью: плановым было ВЧЕРАШНЕЕ срабатывание,
    и gap_days обязан выйти суткой, а не нулём."""
    now = datetime(2026, 9, 4, 2, 5, 0)
    planned = run_scheduled.planned_moment("06:30", now)
    assert planned == datetime(2026, 9, 3, 6, 30)
    assert core.gap_days(planned.isoformat(), now.isoformat()) == 1


def test_broken_planned_time_is_an_error(tmp_path, capsys):
    code = run_scheduled.main(args_for(tmp_path, [{"username": "x"}],
                                       ["--planned-time", "полседьмого"]),
                              probe=Spy(), summarise=Spy(),
                              logger=lambda *a, **kw: None)
    assert code == 2
    assert "не HH:MM" in capsys.readouterr().err


def test_months_default_is_the_rolling_window(monkeypatch, tmp_path):
    """Дата-бомба DEFAULT_MONTHS снята: месяцы считаются от сегодня."""
    monkeypatch.setattr(core, "today", lambda: date(2026, 11, 17))
    seen = {}
    run_scheduled.main(args_for(tmp_path, [{"username": "x"}]),
                       probe=Spy(), summarise=Spy(),
                       logger=lambda *a, **kw: None,
                       ledger_append=lambda root, out: None,
                       notifier=lambda t, m, **kw: seen.update(months=m))
    assert seen["months"] == ["2026-11", "2026-12", "2027-01"]


# ---------------------------------------------------------------------------
# Код возврата 1 не гасит хвост прогона (major ревью волны 1)
# ---------------------------------------------------------------------------

def test_late_run_still_builds_summary_digest_and_log(tmp_path):
    """Код 1 = «часть целей не успела». День данных на диске есть — сводка,
    дайджест и строка в Run Log обязаны выйти, иначе день потерян наружу."""
    make_snapshot(tmp_path, {"x": ("ok", "")}, late=["y"])
    summarise, logged, notified, ledgered = Spy(), [], [], []
    code = run_scheduled.main(
        args_for(tmp_path, [{"username": "x"}, {"username": "y"}]),
        probe=Spy(code=1), summarise=summarise,
        logger=lambda *a, **kw: logged.append(kw),
        notifier=lambda t, m, **kw: notified.append(kw["report"]),
        ledger_append=lambda root, out: ledgered.append(root))
    # Код 0: опоздание — деградация, а не отказ (см.
    # test_late_targets_do_not_fail_the_unit); наружу день всё равно выходит.
    assert code == 0
    assert summarise.calls and logged and notified and ledgered
    assert notified[0]["late"] == ["y"]
    assert logged[0]["status"] == "insufficient_data"


def test_real_error_still_stops_before_summary(tmp_path):
    """Код 2 — настоящая ошибка (битый реестр, занятая блокировка):
    сводка по обрыву хуже отсутствия сводки."""
    summarise, logged = Spy(), []
    code = run_scheduled.main(args_for(tmp_path, [{"username": "x"}]),
                              probe=Spy(code=2), summarise=summarise,
                              logger=lambda *a, **kw: logged.append(kw),
                              notifier=lambda *a, **kw: (0, 1))
    assert code == 2
    # Сводка не пересобирается, а вот журнал обязан быть: правило №6.
    assert summarise.calls == []
    assert logged and logged[0]["status"] == "failed"


def test_healthy_run_logs_success(tmp_path):
    make_snapshot(tmp_path, {"x": ("ok", "")})
    logged = []
    code = run_scheduled.main(args_for(tmp_path, [{"username": "x"}]),
                              probe=Spy(), summarise=Spy(),
                              logger=lambda *a, **kw: logged.append(kw),
                              notifier=lambda *a, **kw: 0,
                              ledger_append=lambda root, out: None)
    assert code == 0
    assert logged[0]["status"] == "success" and logged[0]["errors"] == []


# ---------------------------------------------------------------------------
# Алерт по ДОЛЕ целей без данных (тикет 16)
# ---------------------------------------------------------------------------

def targets_named(names):
    return [{"username": n} for n in names]


def run_with_snapshot(tmp_path, objects, extra=(), late=()):
    make_snapshot(tmp_path, objects, late=late)
    logged, notified = [], []
    code = run_scheduled.main(
        args_for(tmp_path, targets_named(objects), extra),
        probe=Spy(), summarise=Spy(),
        logger=lambda *a, **kw: logged.append(kw),
        notifier=lambda t, m, **kw: notified.append(kw["report"]),
        ledger_append=lambda root, out: None)
    return code, logged, notified


def test_all_targets_empty_is_not_a_success(tmp_path, capsys):
    """Прогон рапортовал успех, даже когда ВСЕ цели отдали insufficient_data:
    OnFailure=cf-alert@ не срабатывал и день уходил молча."""
    objects = {n: ("insufficient_data", "движок ответил 403")
               for n in ("a", "b", "c")}
    code, logged, notified = run_with_snapshot(tmp_path, objects)
    assert code == 1
    assert notified[0]["alert"]
    assert logged[0]["status"] == "insufficient_data"
    assert "без данных" in capsys.readouterr().err


def test_share_below_threshold_stays_green(tmp_path):
    objects = {"a": ("ok", ""), "b": ("ok", ""), "c": ("ok", ""),
               "d": ("ok", ""), "e": ("insufficient_data", "403")}
    code, logged, notified = run_with_snapshot(tmp_path, objects)
    assert code == 0
    assert notified[0]["empty_share"] == 0.2 and not notified[0]["alert"]


def test_share_above_threshold_alerts(tmp_path):
    objects = {"a": ("ok", ""), "b": ("ok", ""), "c": ("ok", ""),
               "d": ("insufficient_data", "403"),
               "e": ("insufficient_data", "рецепта нет")}
    code, _, notified = run_with_snapshot(tmp_path, objects)
    assert code == 1
    assert notified[0]["empty_share"] == 0.4 and notified[0]["alert"]


def test_threshold_is_tunable(tmp_path):
    objects = {"a": ("ok", ""), "b": ("insufficient_data", "403")}
    code, _, _ = run_with_snapshot(tmp_path, objects,
                                   extra=["--max-empty-share", "0.9"])
    assert code == 0


def test_skipped_targets_are_not_in_the_denominator(tmp_path):
    """Цель без рецепта пробник не снимал — считать её «без данных» нельзя,
    иначе новые самарские объекты одни поднимут алерт на ровном месте."""
    make_snapshot(tmp_path, {"x": ("ok", "")})
    targets = [{"username": "x"}, {"username": "новый1"},
               {"username": "новый2"}]
    recipes = tmp_path / "recipes.json"
    recipes.write_text(json.dumps({"x": MODULE}), encoding="utf-8")
    notified = []
    code = run_scheduled.main(
        ["--targets", write_targets(tmp_path, targets),
         "--recipes", str(recipes),
         "--snapshot-root", str(tmp_path / "snapshots"),
         "--ledger-dir", str(tmp_path / "ledger")],
        probe=Spy(), summarise=Spy(), logger=lambda *a, **kw: None,
        notifier=lambda t, m, **kw: notified.append(kw["report"]),
        ledger_append=lambda root, out: None)
    assert code == 0
    assert notified[0]["total"] == 1 and notified[0]["empty_share"] == 0.0
    assert len(notified[0]["skipped"]) == 2


def test_unreadable_snapshot_is_loud_in_stderr(tmp_path, capsys):
    """Снапшота нет — судить о доле не по чему, но молчать об этом нельзя ни
    кодом возврата (test_empty_snapshot_is_a_failure), ни в журнале юнита:
    правило №2 — честный «нет данных», а не выдуманный вывод."""
    notified = []
    code = run_scheduled.main(
        args_for(tmp_path, [{"username": "x"}]),
        probe=Spy(), summarise=Spy(), logger=lambda *a, **kw: None,
        notifier=lambda t, m, **kw: notified.append(kw["report"]),
        ledger_append=lambda root, out: None)
    assert code == 1
    assert notified[0]["total"] == 0
    assert "снапшот" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# Телеграм: один итог + отдельные сообщения только по notify
# ---------------------------------------------------------------------------

REPORT = {"total": 2, "ok": 2, "partial": 0, "empty": 0, "empty_rows": [],
          "skipped": [], "late": [], "empty_share": 0.0, "threshold": 0.2,
          "alert": ""}


def history_of(*usernames):
    return {u: [("r1", {"units": {}}), ("r2", {"units": {}})]
            for u in usernames}


def test_one_aggregate_message_plus_one_per_watched_target(monkeypatch):
    """53 цели = 53 сообщения = шторм, в котором тонет единственная новость."""
    monkeypatch.setattr(run_scheduled.build_summary, "load_history",
                        lambda root, depth=None: history_of("x", "y"))
    monkeypatch.setattr(run_scheduled.tg_digest, "digest",
                        lambda t, o, p, m, d, **kw: f"про {t['username']}")
    sent = []
    n, queued, _ = run_scheduled.notify_targets(
        [{"username": "x", "notify": True}, {"username": "y"},
         {"username": "z"}],
        ["2026-09"], sender=lambda text: sent.append(text) or True,
        report=REPORT, sleeper=lambda s: None)
    assert n == 2 and queued == 2
    assert sent[0].startswith("Загрузка глэмпингов")
    assert sent[1] == "про x"


def test_aggregate_goes_out_even_without_watched_targets(monkeypatch):
    monkeypatch.setattr(run_scheduled.build_summary, "load_history",
                        lambda root, depth=None: {})
    sent = []
    n, queued, _ = run_scheduled.notify_targets(
        [{"username": "x"}], ["2026-09"],
        sender=lambda text: sent.append(text) or True, report=REPORT,
        sleeper=lambda s: None)
    assert n == 1 and queued == 1 and len(sent) == 1


def test_pause_between_messages_but_not_before_the_first(monkeypatch):
    """Пауза между отправками: ретраев у транспорта нет, и пачка в Bot API
    стоит молчаливой потери сообщений."""
    monkeypatch.setattr(run_scheduled.build_summary, "load_history",
                        lambda root, depth=None: history_of("x", "y"))
    monkeypatch.setattr(run_scheduled.tg_digest, "digest",
                        lambda t, o, p, m, d, **kw: "текст")
    slept = []
    run_scheduled.notify_targets(
        [{"username": "x", "notify": True}, {"username": "y", "notify": True}],
        ["2026-09"], sender=lambda text: True, report=REPORT,
        sleeper=slept.append, pause_sec=1.4)
    assert slept == [1.4, 1.4]


def test_notify_reads_history_from_the_given_snapshot_root(monkeypatch,
                                                           tmp_path):
    seen = {}
    monkeypatch.setattr(run_scheduled.build_summary, "load_history",
                        lambda root, depth=None: seen.setdefault("root", root) and {})
    run_scheduled.notify_targets([{"username": "x", "notify": True}],
                                 ["2026-09"], sender=lambda text: True,
                                 snapshot_root=str(tmp_path))
    assert seen["root"] == str(tmp_path)


# ---------------------------------------------------------------------------
# Ledger после сводки (тикет 13)
# ---------------------------------------------------------------------------

def test_ledger_is_appended_from_the_run(tmp_path):
    make_snapshot(tmp_path, {"x": ("ok", "")})
    seen = {}
    run_scheduled.main(args_for(tmp_path, [{"username": "x"}]),
                       probe=Spy(), summarise=Spy(),
                       logger=lambda *a, **kw: None,
                       notifier=lambda *a, **kw: 0,
                       ledger_append=lambda root, out: seen.update(
                           root=root, out=out))
    assert seen["root"] == str(tmp_path / "snapshots")
    assert seen["out"] == str(tmp_path / "ledger")


def test_ledger_failure_does_not_break_the_run(capsys):
    """Слой сезонности производный: его сбой не стоит дня данных."""
    def boom(*a, **kw):
        raise OSError("диск полон")
    run_scheduled.append_ledger("root", "out", appender=boom)
    assert "слой сезонности" in capsys.readouterr().err


def test_ledger_appends_a_real_layer(tmp_path):
    """Настоящий ledger.append на снапшоте прогона: слой появляется."""
    make_snapshot(tmp_path, {"x": ("ok", "")}, run_id="2026-09-01-0630")
    make_snapshot(tmp_path, {"x": ("ok", "")}, run_id="2026-09-05-0630")
    run_scheduled.append_ledger(str(tmp_path / "snapshots"),
                                str(tmp_path / "ledger"))
    assert (tmp_path / "ledger" / "index.json").is_file()


# ---------------------------------------------------------------------------
# Новые цели без рецептов (самарская партия тикета 12)
# ---------------------------------------------------------------------------

def test_new_targets_without_recipes_do_not_break_the_queue():
    """53 новые цели приходят в targets.json РАНЬШЕ своих рецептов: очередь
    обязана уцелеть, а каждая непрошедшая — получить честную строку."""
    targets = [{"username": f"новый_{i}"} for i in range(5)]
    targets.insert(2, {"username": "снимаемый"})
    queue, skipped = run_scheduled.split_queue(targets, {"снимаемый": MODULE})
    assert queue == ["снимаемый"]
    assert len(skipped) == 5
    assert all(why == "рецепта нет в реестре — ждёт разведки"
               for _, why, _ in skipped)


def test_half_scouted_recipe_says_so():
    """Заготовка рецепта без движка — не «движок \'\' снимает только агент»."""
    queue, skipped = run_scheduled.split_queue(
        [{"username": "x"}], {"x": {"site": "https://example.ru"}})
    assert queue == []
    assert skipped == [("x", "в рецепте не записан движок — ждёт разведки",
                        "scout")]


def test_recipe_of_known_engine_always_runs():
    """Рецепт знакомого движка идёт в прогон при любом статусе."""
    recipes = {"a": MODULE, "b": BROKEN,
               "c": {"engine": "uhotels", "status": "unknown"}}
    queue, skipped = run_scheduled.split_queue(
        [{"username": u} for u in ("a", "b", "c")], recipes)
    assert queue == ["a", "b", "c"] and skipped == []


# ---------------------------------------------------------------------------
# Контракт с боевыми парсерами (а не с заглушками)
# ---------------------------------------------------------------------------

def test_probe_argv_is_accepted_by_the_real_cli(tmp_path):
    """Ключи прогона проверяем на БОЕВОМ парсере cli. Ревью волны 1 поймало
    ровно эту дыру: тест зелёный на подставном пробнике с нужной сигнатурой,
    а в бою аргумент не доходит."""
    captured = []
    run_scheduled.main(args_for(tmp_path, [{"username": "x"}]),
                       probe=lambda argv: captured.append(argv) or 0,
                       summarise=Spy(), logger=lambda *a, **kw: None,
                       notifier=lambda *a, **kw: 0,
                       ledger_append=lambda root, out: None)
    ns = cli._build_parser().parse_args(captured[0])
    assert ns.probe == ["x"]
    assert ns.deadline_min == 150.0 and ns.inventory_days == 45
    assert ns.planned_at and ns.snapshot_root == str(tmp_path / "snapshots")


def test_summary_argv_is_accepted_by_the_real_build_summary(tmp_path):
    """Тот же контракт со сводкой: неизвестный ключ дал бы SystemExit, а не
    код 2, и тест бы упал."""
    captured = []
    run_scheduled.main(args_for(tmp_path, [{"username": "x"}]),
                       probe=Spy(),
                       summarise=lambda argv: captured.append(argv) or 0,
                       logger=lambda *a, **kw: None,
                       notifier=lambda *a, **kw: 0,
                       ledger_append=lambda root, out: None)
    # Снапшотов в tmp нет — сводка честно отвечает 2, но argv она РАЗОБРАЛА.
    assert build_summary.main(captured[0]) == 2


# ---------------------------------------------------------------------------
# Изоляция проверочного прогона (инцидент 04.09 + ревью волны 2)
# ---------------------------------------------------------------------------
#
# Волна 2 научила прогон уводить в песочницу снапшоты и ledger, но не выход
# сводки и не чат: прогон в /tmp переписывал версионируемый occupancy.md и слал
# владельцу боевой дайджест. Изоляция обязана быть ПОЛНОЙ, иначе она хуже, чем
# никакой: агент уверен, что ничего не трогает.

def summary_argv_of(spy):
    """argv единственного вызова сборщика сводки."""
    assert len(spy.calls) == 1
    return spy.calls[0]


def value_of(argv, key):
    return argv[argv.index(key) + 1]


def run_ok(tmp_path, extra=(), targets=None, **kw):
    """Прогон в песочницу с готовым снапшотом. -> (код, probe, summarise)."""
    targets = targets or [{"username": "x"}]
    make_snapshot(tmp_path, {"x": ("ok", "")})
    probe, summarise = Spy(), Spy()
    code = run_scheduled.main(
        args_for(tmp_path, targets, extra), probe=probe, summarise=summarise,
        logger=kw.get("logger", lambda *a, **k: None),
        notifier=kw.get("notifier", lambda *a, **k: 0),
        ledger_append=kw.get("ledger_append", lambda root, out: None))
    return code, probe, summarise


def test_check_run_does_not_write_the_versioned_summary(tmp_path):
    """Прогон в НЕбоевой каталог снапшотов пишет сводку рядом с ним: иначе
    проверка перезаписывает docs/research/.../occupancy.md почти пустой
    историей — и это тот же класс потерь, что спам в чат, только по данным."""
    _, _, summarise = run_ok(tmp_path)
    argv = summary_argv_of(summarise)
    assert value_of(argv, "--out") == str(tmp_path / "occupancy.md")
    assert value_of(argv, "--out-html") == str(tmp_path / "occupancy.html")
    assert str(core.DEFAULT_OUT_MD) not in argv


def test_production_run_writes_the_versioned_summary(tmp_path, monkeypatch):
    """Боевой каталог снапшотов — боевые файлы сводки: ради них всё и есть."""
    monkeypatch.setattr(core, "DEFAULT_SNAPSHOT_ROOT", tmp_path / "snapshots")
    _, _, summarise = run_ok(tmp_path)
    argv = summary_argv_of(summarise)
    assert value_of(argv, "--out") == str(core.DEFAULT_OUT_MD)
    assert value_of(argv, "--out-html") == str(core.DEFAULT_OUT_HTML)


def test_explicit_out_wins_over_the_sandbox_default(tmp_path):
    _, _, summarise = run_ok(tmp_path, ["--out", "/tmp/своя.md",
                                        "--out-html", "/tmp/своя.html"])
    argv = summary_argv_of(summarise)
    assert value_of(argv, "--out") == "/tmp/своя.md"
    assert value_of(argv, "--out-html") == "/tmp/своя.html"


def test_log_run_reports_the_file_it_actually_wrote(tmp_path):
    """В Run Log идёт та сводка, которую прогон и написал: строка про боевой
    occupancy.md после прогона в песочницу — ложь по правилу №1."""
    logged = []
    run_ok(tmp_path, logger=lambda *a, **kw: logged.append(a))
    assert logged[0][2] == [str(tmp_path / "snapshots"),
                            str(tmp_path / "occupancy.md")]


def test_check_run_sends_nothing_to_telegram(tmp_path, monkeypatch, capsys):
    """Ключ --no-notify включается САМ при небоевом каталоге: проверочный
    прогон не имеет права писать владельцу (инцидент 04.09)."""
    sent = []
    monkeypatch.setattr(run_scheduled, "send_telegram",
                        lambda text: sent.append(text) or True)
    make_snapshot(tmp_path, {"x": ("ok", "")})
    run_scheduled.main(args_for(tmp_path, [{"username": "x"}]),
                       probe=Spy(), summarise=Spy(),
                       logger=lambda *a, **kw: None,
                       ledger_append=lambda root, out: None)
    out = capsys.readouterr().out
    assert sent == []
    # Текст не пропадает: оператор проверочного прогона видит его в консоли.
    assert "Загрузка глэмпингов" in out and "НЕ отправлено" in out


def test_no_notify_silences_even_the_production_run(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DEFAULT_SNAPSHOT_ROOT", tmp_path / "snapshots")
    sent = []
    monkeypatch.setattr(run_scheduled, "send_telegram",
                        lambda text: sent.append(text) or True)
    make_snapshot(tmp_path, {"x": ("ok", "")})
    run_scheduled.main(args_for(tmp_path, [{"username": "x"}], ["--no-notify"]),
                       probe=Spy(), summarise=Spy(),
                       logger=lambda *a, **kw: None,
                       ledger_append=lambda root, out: None)
    assert sent == []


def test_production_run_uses_the_telegram_transport(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DEFAULT_SNAPSHOT_ROOT", tmp_path / "snapshots")
    sent = []
    monkeypatch.setattr(run_scheduled, "send_telegram",
                        lambda text: sent.append(text) or True)
    make_snapshot(tmp_path, {"x": ("ok", "")})
    run_scheduled.main(args_for(tmp_path, [{"username": "x"}]),
                       probe=Spy(), summarise=Spy(),
                       logger=lambda *a, **kw: None,
                       ledger_append=lambda root, out: None)
    assert len(sent) == 1 and sent[0].startswith("Загрузка глэмпингов")


# ---------------------------------------------------------------------------
# Реестр рецептов и параллелизм доходят до пробника
# ---------------------------------------------------------------------------

def test_recipes_registry_reaches_the_probe(tmp_path):
    """Очередь строилась по одному реестру, а съём и пометки broken шли по
    боевому: прогон с тестовым --recipes писал status/broken_reason в живой
    файл рецептов."""
    _, probe, summarise = run_ok(tmp_path)
    recipes = str(tmp_path / "recipes.json")
    assert value_of(probe.calls[0], "--recipes") == recipes
    assert value_of(summary_argv_of(summarise), "--recipes") == recipes


def test_host_workers_is_forwarded(tmp_path):
    """Рычаг отката параллелизма обязан быть доступен из планового пути:
    иначе единственный способ вернуть последовательный прогон — правка кода."""
    _, probe, _ = run_ok(tmp_path, ["--host-workers", "1"])
    assert value_of(probe.calls[0], "--host-workers") == "1"


def test_host_workers_default_matches_the_probe(tmp_path):
    _, probe, _ = run_ok(tmp_path)
    assert (value_of(probe.calls[0], "--host-workers")
            == str(cli.DEFAULT_HOST_WORKERS))


def test_dry_run_shows_the_real_command_line(tmp_path, capsys):
    """«Что уйдёт в бой» — это ВСЯ строка вызова, включая горизонт фонда и
    дедлайн: их --dry-run умалчивал, а цена прогона стоит именно на них."""
    run_scheduled.main(args_for(tmp_path, [{"username": "x"}],
                                ["--dry-run", "--inventory-days", "7"]),
                       probe=Spy(), summarise=Spy(),
                       logger=lambda *a, **kw: None)
    out = capsys.readouterr().out
    assert "--inventory-days 7" in out
    assert "--deadline-min 150" in out and "--snapshot-root" in out


# ---------------------------------------------------------------------------
# Коды возврата: опоздание — не авария, пустой снапшот — авария
# ---------------------------------------------------------------------------

def test_late_targets_do_not_fail_the_unit(tmp_path):
    """Один медленный хост давал ежедневный OnFailure и один и тот же алерт
    сутки за сутками: опоздавшая цель — это деградация, а не отказ. День на
    диске есть, причина у цели записана, в Run Log стоит insufficient_data."""
    make_snapshot(tmp_path, {"x": ("ok", "")}, late=["y"])
    logged, notified = [], []
    code = run_scheduled.main(
        args_for(tmp_path, [{"username": "x"}, {"username": "y"}]),
        probe=Spy(code=1), summarise=Spy(),
        logger=lambda *a, **kw: logged.append(kw),
        notifier=lambda t, m, **kw: notified.append(kw["report"]),
        ledger_append=lambda root, out: None)
    assert code == 0
    assert logged[0]["status"] == "insufficient_data"
    assert notified[0]["late"] == ["y"]


def test_empty_snapshot_is_a_failure(tmp_path, capsys):
    """Самый тяжёлый исход — на диск не записано ни одной цели — давал зелёный
    Run Log и зелёный systemd: единственный сигнал уходил в чат."""
    logged, notified = [], []
    code = run_scheduled.main(
        args_for(tmp_path, [{"username": "x"}]),
        probe=Spy(), summarise=Spy(),
        logger=lambda *a, **kw: logged.append(kw),
        notifier=lambda t, m, **kw: notified.append(kw["report"]),
        ledger_append=lambda root, out: None)
    assert code == 1
    assert notified[0]["alert"] and notified[0]["total"] == 0
    assert logged[0]["status"] == "insufficient_data"


def test_denominator_is_the_plan_not_the_written_files(tmp_path):
    """Прогон, оборванный на 3-й цели из 21, рапортовал «без данных 0%»:
    знаменатель брался из числа ЗАПИСАННЫХ объектов, а план прогона лежит
    рядом в run.json."""
    snap = make_snapshot(tmp_path, {"a": ("ok", ""), "b": ("ok", "")})
    run = json.loads((snap / "run.json").read_text(encoding="utf-8"))
    run["targets"] = ["a", "b", "c", "d"]
    (snap / "run.json").write_text(json.dumps(run, ensure_ascii=False),
                                   encoding="utf-8")
    report = run_scheduled.run_report(
        core.read_snapshot(snap))
    assert report["total"] == 4 and report["empty"] == 2
    assert report["empty_share"] == 0.5 and report["alert"]
    assert ("c", "прогон оборван — объект не записан") in report["empty_rows"]


# ---------------------------------------------------------------------------
# Схлопывание повторов в чате (пункт 2 «постоянной починки» тикета 16)
# ---------------------------------------------------------------------------

def test_same_message_within_the_window_goes_once(tmp_path):
    """Инцидент 04.09 — это ОДИН И ТОТ ЖЕ текст подряд много раз за полчаса."""
    state = tmp_path / "notify-state.json"
    guard = run_scheduled.repeat_guard(state, window_hours=6,
                                       now=datetime(2026, 9, 4, 14, 0))
    assert guard.allow("итог прогона") is True
    guard.mark("итог прогона")
    assert guard.allow("итог прогона") is False
    assert guard.allow("другой текст") is True


def test_unsent_message_is_not_remembered(tmp_path):
    """Отметку ставит ОТПРАВКА, а не проверка. Иначе заглушённый транспорт
    (стоп-кран, нет сети, нет токена) съедал единственную новость прогона
    навсегда: sender вернул False, а сито уже помнило текст отправленным."""
    state = tmp_path / "notify-state.json"
    guard = run_scheduled.repeat_guard(state, window_hours=6,
                                       now=datetime(2026, 9, 4, 14, 0))
    assert guard.allow("итог прогона") is True  # не ушло — не помечаем
    later = run_scheduled.repeat_guard(state, window_hours=6,
                                       now=datetime(2026, 9, 4, 15, 0))
    assert later.allow("итог прогона") is True


def test_the_window_expires(tmp_path):
    """Плановый дайджест не имеет права заглохнуть: сутки > окна повтора."""
    state = tmp_path / "notify-state.json"
    run_scheduled.repeat_guard(state, window_hours=6,
                               now=datetime(2026, 9, 4, 14, 0)).mark("итог")
    later = run_scheduled.repeat_guard(state, window_hours=6,
                                       now=datetime(2026, 9, 5, 6, 30))
    assert later.allow("итог") is True


def test_repeat_guard_survives_an_unwritable_state(tmp_path, capsys):
    """Состояние — удобство, а не данные: его сбой не имеет права глушить
    единственную новость прогона."""
    занято = tmp_path / "занято"
    занято.write_text("это файл, а не каталог", encoding="utf-8")
    guard = run_scheduled.repeat_guard(занято / "состояние.json",
                                       window_hours=6)
    assert guard.allow("итог") is True
    guard.mark("итог")  # не пишется, но и не бросает
    assert "состояние повторов" in capsys.readouterr().err


class FakeGuard:
    """Сито с известным ответом: помнит, что помечали отправленным."""

    def __init__(self, allowed):
        self.allowed = allowed
        self.marked = []

    def allow(self, text):
        return text in self.allowed

    def mark(self, text):
        self.marked.append(text)


def test_notify_targets_respects_the_guard(monkeypatch):
    monkeypatch.setattr(run_scheduled.build_summary, "load_history",
                        lambda root, depth=None: history_of("x"))
    monkeypatch.setattr(run_scheduled.tg_digest, "digest",
                        lambda t, o, p, m, d, **kw: "про x")
    sent = []
    guard = FakeGuard({"про x"})
    n, queued, muffled = run_scheduled.notify_targets(
        [{"username": "x", "notify": True}], ["2026-09"],
        sender=lambda text: sent.append(text) or True, report=REPORT,
        sleeper=lambda s: None, guard=guard)
    assert sent == ["про x"] and n == 1 and queued == 2
    # Итог прогона сито не пропустило — это его работа, а не молчание чата.
    assert muffled == 1
    assert guard.marked == ["про x"]


def test_guard_remembers_only_what_actually_went(monkeypatch):
    """Транспорт молчит — сито не имеет права запомнить текст отправленным."""
    monkeypatch.setattr(run_scheduled.build_summary, "load_history",
                        lambda root, depth=None: {})
    # Сито пропускает ровно тот текст, который прогон и собирается слать:
    # молчит здесь ТРАНСПОРТ, а не сито (иначе тест проверял бы не то).
    guard = FakeGuard({run_scheduled.tg_digest.run_summary(REPORT,
                                                           date.today())})
    n, queued, muffled = run_scheduled.notify_targets(
        [], ["2026-09"], sender=lambda text: False, report=REPORT,
        sleeper=lambda s: None, guard=guard)
    assert n == 0 and queued == 1 and muffled == 0
    assert guard.marked == []


# ---------------------------------------------------------------------------
# Юнит systemd — не комментарий, а проверяемый инвариант
# ---------------------------------------------------------------------------

UNIT_DIR = Path(run_scheduled.REPO_ROOT) / "deploy" / "systemd-user"


def unit_value(name, key):
    for line in (UNIT_DIR / name).read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    return ""


def test_unit_timeout_is_well_above_the_run_deadline():
    """«Двигать парой» трижды написано комментарием и ни разу не проверено —
    а пара уже разъезжалась: дедлайн 90 мин под потолком 60 мин означает
    SIGTERM раньше штатного закрытия снапшота."""
    raw = unit_value("cf-occupancy.service", "TimeoutStartSec")
    assert raw.endswith("min"), raw
    assert (int(raw[:-3])
            >= run_scheduled.DEFAULT_DEADLINE_MIN * 1.2)


def test_timer_hour_matches_the_planned_time():
    """gap_days считается от планового момента: разъехавшийся час сделает
    догоняющий прогон «снятым вовремя»."""
    on_calendar = unit_value("cf-occupancy.timer", "OnCalendar")
    assert on_calendar.endswith(run_scheduled.DEFAULT_PLANNED_TIME + ":00")


def test_unit_execstart_points_at_this_script():
    # Витрина: юнит лежит с путями-плейсхолдерами (/home/<user>/...), поэтому
    # сверяем путь скрипта относительно корня репозитория, а не абсолютный.
    rel = Path(run_scheduled.__file__).resolve().relative_to(
        Path(run_scheduled.REPO_ROOT).resolve()).as_posix()
    assert rel in unit_value("cf-occupancy.service", "ExecStart")


# ---------------------------------------------------------------------------
# Бэкап и хранение (пункты 1 и 4 тикета 16)
# ---------------------------------------------------------------------------

def test_backup_copies_snapshots_and_ledger(tmp_path):
    """Каталог снапшотов и ledger — единственный экземпляр ряда, ради которого
    всё делается. Копия кладётся ПОЦЕЛЬНО: полный архив каждый день растёт на
    сорок мегабайт, а новых каталогов за сутки один."""
    make_snapshot(tmp_path, {"x": ("ok", "")}, run_id="2026-09-04-0630")
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    (ledger_dir / "index.json").write_text("{}", encoding="utf-8")
    stats = run_scheduled.backup_run(
        str(tmp_path / "snapshots"), str(ledger_dir), str(tmp_path / "backup"),
        now=datetime(2026, 9, 4, 7, 0))
    assert stats["snapshots"] == ["2026-09-04-0630"]
    assert (tmp_path / "backup" / "snapshots"
            / "2026-09-04-0630.tar.gz").is_file()
    assert stats["ledger"]


def test_backup_does_not_redo_what_is_already_copied(tmp_path):
    make_snapshot(tmp_path, {"x": ("ok", "")}, run_id="2026-09-04-0630")
    args = (str(tmp_path / "snapshots"), str(tmp_path / "ledger"),
            str(tmp_path / "backup"))
    run_scheduled.backup_run(*args, now=datetime(2026, 9, 4, 7, 0))
    again = run_scheduled.backup_run(*args, now=datetime(2026, 9, 5, 7, 0))
    assert again["snapshots"] == []


def test_backup_restores_into_a_working_snapshot(tmp_path):
    """Копия, которую нельзя развернуть, — не копия. Разворачиваем и читаем
    тем же ядром, что читает сводка."""
    make_snapshot(tmp_path, {"x": ("ok", "")}, run_id="2026-09-04-0630")
    run_scheduled.backup_run(str(tmp_path / "snapshots"),
                             str(tmp_path / "ledger"),
                             str(tmp_path / "backup"),
                             now=datetime(2026, 9, 4, 7, 0))
    dest = run_scheduled.restore_backup(
        tmp_path / "backup" / "snapshots" / "2026-09-04-0630.tar.gz",
        tmp_path / "restored")
    assert core.list_snapshots(dest) == ["2026-09-04-0630"]
    snap = core.read_snapshot(Path(dest) / "2026-09-04-0630")
    assert snap["objects"]["x"]["status"] == "ok"


def test_backup_failure_does_not_break_the_run(tmp_path, capsys):
    """Копия — эксплуатация, а не данные: её сбой не стоит дня цифр.

    Сбой берём НАСТОЯЩИЙ — архивация в недоступный каталог. Прежний тест
    подсовывал несуществующий каталог снапшотов и выходил по раннему return,
    то есть ветку архивации не проходил вовсе и был зелёным по ложной
    причине."""
    make_snapshot(tmp_path, {"x": ("ok", "")}, run_id="2026-09-04-0630")
    занято = tmp_path / "занято"
    занято.write_text("это файл, а не каталог", encoding="utf-8")
    stats = run_scheduled.backup_run(str(tmp_path / "snapshots"),
                                     str(tmp_path / "ledger"), str(занято))
    assert stats["snapshots"] == [] and not stats["ledger"]
    assert "копия снята не полностью" in capsys.readouterr().err


def test_backup_says_when_there_is_nothing_to_copy(tmp_path, capsys):
    stats = run_scheduled.backup_run(str(tmp_path / "нет-такого"), "нет",
                                     str(tmp_path / "backup"))
    assert stats["snapshots"] == []
    assert "копия не снята" in capsys.readouterr().err


def test_old_snapshots_are_gzipped_in_place(tmp_path):
    """Политика хранения: сырые снапшоты держим (правило №1), старше 90 суток —
    gzip на месте. Архив НЕ виден list_snapshots — и это верно: сводка читает
    свежее окно, а ряд живёт в ledger."""
    root = tmp_path / "snapshots"
    make_snapshot(tmp_path, {"x": ("ok", "")}, run_id="2026-01-01-0630")
    make_snapshot(tmp_path, {"x": ("ok", "")}, run_id="2026-09-01-0630")
    archived = run_scheduled.archive_old_snapshots(
        str(root), older_than_days=90, now=datetime(2026, 9, 4, 7, 0))
    assert archived == ["2026-01-01-0630"]
    assert (root / "2026-01-01-0630.tar.gz").is_file()
    assert core.list_snapshots(root) == ["2026-09-01-0630"]


def test_archived_snapshot_can_be_read_back(tmp_path):
    root = tmp_path / "snapshots"
    make_snapshot(tmp_path, {"x": ("ok", "")}, run_id="2026-01-01-0630")
    run_scheduled.archive_old_snapshots(str(root), older_than_days=90,
                                        now=datetime(2026, 9, 4, 7, 0))
    dest = run_scheduled.restore_backup(root / "2026-01-01-0630.tar.gz",
                                        tmp_path / "restored")
    assert core.list_snapshots(dest) == ["2026-01-01-0630"]


def test_archiving_is_off_by_zero(tmp_path):
    make_snapshot(tmp_path, {"x": ("ok", "")}, run_id="2026-01-01-0630")
    assert run_scheduled.archive_old_snapshots(
        str(tmp_path / "snapshots"), older_than_days=0,
        now=datetime(2026, 9, 4, 7, 0)) == []


def test_check_run_works_on_a_copy_of_the_registry(tmp_path, monkeypatch):
    """cli по ходу съёма ПИШЕТ в реестр (broken, счётчики отказов), а замок
    берёт по пути снапшота — то есть проверочный прогон в другой каталог
    правил живой файл рецептов параллельно плановому."""
    live = tmp_path / "живой-recipes.json"
    live.write_text(json.dumps({"x": MODULE}, ensure_ascii=False),
                    encoding="utf-8")
    monkeypatch.setattr(core, "DEFAULT_RECIPES", live)
    make_snapshot(tmp_path, {"x": ("ok", "")})
    probe = Spy()
    run_scheduled.main(
        ["--targets", write_targets(tmp_path, [{"username": "x"}]),
         "--snapshot-root", str(tmp_path / "snapshots"),
         "--ledger-dir", str(tmp_path / "ledger"), "--no-backup"],
        probe=probe, summarise=Spy(), logger=lambda *a, **kw: None,
        notifier=lambda *a, **kw: 0, ledger_append=lambda root, out: None)
    used = value_of(probe.calls[0], "--recipes")
    assert used == str(tmp_path / "recipes.json") and used != str(live)
    assert json.loads(Path(used).read_text(encoding="utf-8")) == {"x": MODULE}


def test_production_run_uses_the_live_registry(tmp_path, monkeypatch):
    live = tmp_path / "живой-recipes.json"
    live.write_text(json.dumps({"x": MODULE}, ensure_ascii=False),
                    encoding="utf-8")
    monkeypatch.setattr(core, "DEFAULT_RECIPES", live)
    monkeypatch.setattr(core, "DEFAULT_SNAPSHOT_ROOT", tmp_path / "snapshots")
    make_snapshot(tmp_path, {"x": ("ok", "")})
    probe = Spy()
    run_scheduled.main(
        ["--targets", write_targets(tmp_path, [{"username": "x"}]),
         "--ledger-dir", str(tmp_path / "ledger"), "--no-backup",
         "--no-notify"],
        probe=probe, summarise=Spy(), logger=lambda *a, **kw: None,
        notifier=lambda *a, **kw: 0, ledger_append=lambda root, out: None)
    assert value_of(probe.calls[0], "--recipes") == str(live)


def test_explicit_recipes_are_taken_as_given(tmp_path, monkeypatch):
    """Оператор сказал, каким реестром работать, — копию за него не делаем."""
    monkeypatch.setattr(core, "DEFAULT_RECIPES", tmp_path / "живой.json")
    (tmp_path / "живой.json").write_text(json.dumps({"x": MODULE}),
                                         encoding="utf-8")
    own_registry = tmp_path / "мой-реестр.json"
    own_registry.write_text(json.dumps({"x": MODULE}), encoding="utf-8")
    make_snapshot(tmp_path, {"x": ("ok", "")})
    probe = Spy()
    run_scheduled.main(
        ["--targets", write_targets(tmp_path, [{"username": "x"}]),
         "--recipes", str(own_registry),
         "--snapshot-root", str(tmp_path / "snapshots"),
         "--ledger-dir", str(tmp_path / "ledger"), "--no-backup"],
        probe=probe, summarise=Spy(), logger=lambda *a, **kw: None,
        notifier=lambda *a, **kw: 0, ledger_append=lambda root, out: None)
    assert value_of(probe.calls[0], "--recipes") == str(own_registry)
    assert not (tmp_path / "recipes.json").exists()


# ---------------------------------------------------------------------------
# Волна 4: один узел «боевой прогон или песочница» (блокер ревью волны 3)
# ---------------------------------------------------------------------------
#
# Волна 3 уводила в песочницу выход сводки, реестр рецептов и каталог копий
# тремя независимыми ключами, а четвёртый выход — слой сезонности — остался на
# боевом умолчании. Проверочный прогон в /tmp дописывал в живой ряд выдуманный
# прогон, и после отравления last_run_id следующий НАСТОЯЩИЙ прогон в слой уже
# не попадал вовсе. Поэтому решение принимается ОДИН раз (RunPaths), а список
# выходов один — и каждый из них проверяется на выход за пределы песочницы.

def test_sandbox_run_keeps_the_seasonality_layer_out_of_production(tmp_path,
                                                                   monkeypatch):
    """Умолчание --ledger-dir уходит за --snapshot-root, а не в боевой слой."""
    боевой = tmp_path / "боевой-слой"
    monkeypatch.setattr(run_scheduled.ledger, "DEFAULT_LEDGER_DIR", боевой)
    make_snapshot(tmp_path, {"x": ("ok", "")})
    seen = {}
    run_scheduled.main(args_for(tmp_path, [{"username": "x"}]),
                       probe=Spy(), summarise=Spy(),
                       logger=lambda *a, **kw: None,
                       notifier=lambda *a, **kw: (0, 0),
                       ledger_append=lambda root, out: seen.update(out=out))
    assert seen["out"] == str(tmp_path / "ledger")
    assert not боевой.exists()


def test_production_run_writes_the_production_layer(tmp_path, monkeypatch):
    """Боевой каталог снапшотов — боевой слой сезонности: ради него всё и есть."""
    боевой = tmp_path / "боевой-слой"
    monkeypatch.setattr(run_scheduled.ledger, "DEFAULT_LEDGER_DIR", боевой)
    monkeypatch.setattr(core, "DEFAULT_SNAPSHOT_ROOT", tmp_path / "snapshots")
    make_snapshot(tmp_path, {"x": ("ok", "")})
    seen = {}
    run_scheduled.main(args_for(tmp_path, [{"username": "x"}], ["--no-notify"]),
                       probe=Spy(), summarise=Spy(),
                       logger=lambda *a, **kw: None,
                       notifier=lambda *a, **kw: (0, 0),
                       ledger_append=lambda root, out: seen.update(out=out))
    assert seen["out"] == str(боевой)


def test_explicit_ledger_dir_is_taken_as_given(tmp_path):
    seen = {}
    make_snapshot(tmp_path, {"x": ("ok", "")})
    run_scheduled.main(
        args_for(tmp_path, [{"username": "x"}],
                 ["--ledger-dir", str(tmp_path / "мой-слой")]),
        probe=Spy(), summarise=Spy(), logger=lambda *a, **kw: None,
        notifier=lambda *a, **kw: (0, 0),
        ledger_append=lambda root, out: seen.update(out=out))
    assert seen["out"] == str(tmp_path / "мой-слой")


def test_every_output_of_a_sandbox_run_stays_in_the_sandbox(tmp_path, monkeypatch):
    """Инвариант, который не даст забыть следующий выход: ВСЕ пути прогона в
    песочницу лежат под каталогом песочницы."""
    # Витрина: боевого реестра рецептов в репозитории нет, а песочница берёт
    # его КОПИЮ — подставляем пустой реестр вместо боевого умолчания.
    live = tmp_path / "live" / "recipes.json"
    live.parent.mkdir()
    live.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(run_scheduled.core, "DEFAULT_RECIPES", live)
    paths = run_scheduled.RunPaths(str(tmp_path / "snapshots"))
    assert not paths.production and paths.stray() == []
    for name in run_scheduled.RunPaths.OUTPUTS:
        assert str(tmp_path) in getattr(paths, name), name


def test_a_forgotten_output_is_caught_by_the_check(tmp_path):
    """Ровно тот дефект, что дал блокер: новый выход оставили на боевом
    умолчании. Проверка обязана назвать его по имени."""
    paths = run_scheduled.RunPaths(str(tmp_path / "snapshots"))
    # Так выглядит забытое боевое умолчание: путь вне песочницы прогона.
    paths.ledger_dir = "/agent-runtime/боевой-слой"
    assert [name for name, _ in paths.stray()] == ["ledger_dir"]


def test_run_refuses_when_an_output_escapes_the_sandbox(tmp_path, monkeypatch,
                                                       capsys):
    """Прогон не начинается: писать выдуманные цифры в боевой ряд нельзя, а
    молча увести их некуда — это ошибка кода, и человек должен её увидеть."""
    class Забывчивый(run_scheduled.RunPaths):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.ledger_dir = "/боевой/слой"

    monkeypatch.setattr(run_scheduled, "RunPaths", Забывчивый)
    probe = Spy()
    code = run_scheduled.main(args_for(tmp_path, [{"username": "x"}]),
                              probe=probe, summarise=Spy(),
                              logger=lambda *a, **kw: None,
                              notifier=lambda *a, **kw: (0, 0))
    assert code == 2 and probe.calls == []
    assert "мимо песочницы" in capsys.readouterr().err


def test_production_run_is_not_second_guessed(tmp_path, monkeypatch):
    """У боевого прогона все выходы боевые по определению — проверка молчит."""
    monkeypatch.setattr(core, "DEFAULT_SNAPSHOT_ROOT", tmp_path / "snapshots")
    paths = run_scheduled.RunPaths(str(tmp_path / "snapshots"))
    assert paths.production and paths.stray() == []


# ---------------------------------------------------------------------------
# Волна 4: дайджест не читает всю историю и не врёт о дате снимка
# ---------------------------------------------------------------------------

def test_notify_reads_only_the_last_two_snapshots(monkeypatch):
    """2,55 с и 465 объекто-снимков ради двух последних снимков ОДНОЙ цели.
    Тикет 09 ввёл depth для сводки, а путь уведомления прошёл мимо него."""
    seen = {}
    monkeypatch.setattr(
        run_scheduled.build_summary, "load_history",
        lambda root, depth=None: seen.update(depth=depth) or history_of("x"))
    monkeypatch.setattr(run_scheduled.tg_digest, "digest",
                        lambda t, o, p, m, d, **kw: "текст")
    run_scheduled.notify_targets([{"username": "x", "notify": True}],
                                 ["2026-09"], sender=lambda text: True)
    assert seen["depth"] == 2


def test_digest_gets_the_day_of_the_snapshot_not_of_the_run(monkeypatch):
    """Цель, которую таймер ПРОПУСКАЕТ (агрегатор, неразведанный рецепт),
    сегодняшнего файла не получает: её трёхнедельная цифра уходила владельцу
    под сегодняшней датой со словами «за сутки не двигалось»."""
    monkeypatch.setattr(
        run_scheduled.build_summary, "load_history",
        lambda root, depth=None: {"x": [("2026-08-13-0630", {"units": {}}),
                                        ("2026-08-14-0630", {"units": {}})]})
    seen = {}
    monkeypatch.setattr(run_scheduled.tg_digest, "digest",
                        lambda t, o, p, m, d, **kw: seen.update(kw) or "текст")
    run_scheduled.notify_targets([{"username": "x", "notify": True}],
                                 ["2026-09"], sender=lambda text: True,
                                 today=date(2026, 9, 5))
    assert seen["taken"] == date(2026, 8, 14)
    assert seen["gap_days"] == 1


def test_gap_is_counted_between_the_two_snapshots(monkeypatch):
    monkeypatch.setattr(
        run_scheduled.build_summary, "load_history",
        lambda root, depth=None: {"x": [("2026-08-01-0630", {"units": {}}),
                                        ("2026-08-14-0630", {"units": {}})]})
    seen = {}
    monkeypatch.setattr(run_scheduled.tg_digest, "digest",
                        lambda t, o, p, m, d, **kw: seen.update(kw) or "текст")
    run_scheduled.notify_targets([{"username": "x", "notify": True}],
                                 ["2026-09"], sender=lambda text: True,
                                 today=date(2026, 9, 5))
    assert seen["gap_days"] == 13


def test_unparsable_run_id_does_not_invent_a_date(monkeypatch):
    """Каталог прогона не по формату — честное «не знаю», а не сегодняшняя дата."""
    monkeypatch.setattr(
        run_scheduled.build_summary, "load_history",
        lambda root, depth=None: {"x": [("странное-имя", {"units": {}})]})
    seen = {}
    monkeypatch.setattr(run_scheduled.tg_digest, "digest",
                        lambda t, o, p, m, d, **kw: seen.update(kw) or "текст")
    run_scheduled.notify_targets([{"username": "x", "notify": True}],
                                 ["2026-09"], sender=lambda text: True,
                                 today=date(2026, 9, 5))
    assert seen["taken"] is None and seen["gap_days"] is None


# ---------------------------------------------------------------------------
# Волна 4: хвост прогона не роняется чатом и говорит, что ушло
# ---------------------------------------------------------------------------

def test_notify_exception_does_not_kill_the_tail(tmp_path, capsys):
    """Сборка ТЕКСТА не защищена: битый каталог снапшота бросает
    core.SnapshotError, и прогон терял хвост целиком — вместе с Run Log."""
    make_snapshot(tmp_path, {"x": ("ok", "")})
    logged = []

    def boom(*a, **kw):
        raise RuntimeError("каталог снапшота оборван")

    code = run_scheduled.main(args_for(tmp_path, [{"username": "x"}]),
                              probe=Spy(), summarise=Spy(), notifier=boom,
                              logger=lambda *a, **kw: logged.append(kw),
                              ledger_append=lambda root, out: None)
    assert code == 0 and logged
    assert "уведомления не отправлены" in capsys.readouterr().err


def test_run_log_says_how_many_notifications_went(tmp_path):
    """Молчание чата неотличимо от исправной работы, пока прогон не сказал,
    сколько сообщений ушло из скольких."""
    make_snapshot(tmp_path, {"x": ("ok", "")})
    logged = []
    run_scheduled.main(args_for(tmp_path, [{"username": "x"}]),
                       probe=Spy(), summarise=Spy(),
                       notifier=lambda *a, **kw: (0, 1),
                       logger=lambda *a, **kw: logged.append((a, kw)),
                       ledger_append=lambda root, out: None)
    assert "уведомлений отправлено 0 из 1" in logged[0][1]["note"]
    assert any("не ушло" in e for e in logged[0][1]["errors"])
    # Молчащий чат — не про качество данных: статус остаётся success.
    assert logged[0][1]["status"] == "success"


def test_log_run_puts_the_note_into_the_input(monkeypatch):
    seen = {}
    monkeypatch.setattr(run_scheduled.subprocess, "run",
                        lambda cmd, **kw: seen.update(cmd=cmd))
    run_scheduled.log_run("2026-09-05T06:30:00+03:00", 21, ["снапшот"],
                          note="уведомлений отправлено 1 из 2")
    text = seen["cmd"][seen["cmd"].index("--input") + 1]
    assert "целей 21" in text and "уведомлений отправлено 1 из 2" in text


def test_dry_run_shows_where_every_output_goes(tmp_path, capsys):
    """Оператор приходит в --dry-run за ответом «куда всё ляжет». Слой
    сезонности молчал — и ровно он и оказался забыт на боевом умолчании."""
    run_scheduled.main(args_for(tmp_path, [{"username": "x"}], ["--dry-run"]),
                       probe=Spy(), summarise=Spy(),
                       logger=lambda *a, **kw: None)
    out = capsys.readouterr().out
    assert f"слой сезонности: {tmp_path / 'ledger'}" in out
    assert f"копия: {tmp_path / 'backup'}" in out


def test_silence_of_the_chat_names_its_reason(monkeypatch, capsys):
    """Стоп-кран в журнале юнита обязан назвать себя: иначе заглушённый
    прогон выглядит там ровно как исправный, а проверяющий бота идёт чинить
    исправное."""
    monkeypatch.setattr("cf.notify.notify_telegram", lambda *a, **kw: False)
    monkeypatch.setattr("cf.notify.mute_note",
                        lambda *a, **kw: "уведомления заглушены стоп-краном")
    assert run_scheduled.send_telegram("текст") is False
    assert "заглушены стоп-краном" in capsys.readouterr().err


def test_a_production_default_is_caught_even_inside_the_sandbox(tmp_path):
    """Снапшоты кладут и внутрь репозитория: тогда боевой agent-runtime
    формально лежит «в песочнице», а порча ряда никуда не девается. Поэтому
    равенство боевому умолчанию ловится отдельным признаком."""
    paths = run_scheduled.RunPaths(str(tmp_path / "snapshots"))
    paths.ledger_dir = str(run_scheduled.ledger.DEFAULT_LEDGER_DIR)
    assert str(tmp_path) in paths.ledger_dir  # умолчание лежит В песочнице
    assert [name for name, _ in paths.stray()] == ["ledger_dir"]


# ---------------------------------------------------------------------------
# Волна 5: Run Log — такой же выход прогона, как сводка (ревью волны 4)
# ---------------------------------------------------------------------------
#
# Волна 4 свела «боевой прогон или песочница» в один узел, но список выходов
# собрала только из ПУТЕЙ. Строка в Run Log путём не является — и осталась
# снаружи: проверочный прогон в /tmp писал в боевую вкладку «плановый прогон
# по расписанию», и в журнале завода появлялся день, которого не было.

def test_check_run_does_not_write_the_production_run_log(tmp_path, monkeypatch,
                                                         capsys):
    """Небоевой каталог снапшотов = проверочный прогон = журнала не трогаем.

    Умолчание логгера решает ТОТ ЖЕ узел, что и остальные выходы: иначе
    строку «плановый прогон, целей N» в боевом Run Log нельзя отличить от
    настоящего прогона, а по ней сверяют правило №6.
    """
    written = []
    monkeypatch.setattr(run_scheduled, "log_run",
                        lambda *a, **kw: written.append(a))
    make_snapshot(tmp_path, {"x": ("ok", "")})
    code = run_scheduled.main(args_for(tmp_path, [{"username": "x"}]),
                              probe=Spy(), summarise=Spy(),
                              ledger_append=lambda root, out: None)
    assert code == 0 and written == []
    out = capsys.readouterr().out
    # Текст строки не пропадает: оператор видит, что ушло бы в журнал.
    assert "Run Log" in out and "плановый прогон" in out


def test_production_run_writes_the_run_log(tmp_path, monkeypatch):
    """Боевой прогон пишет строку журнала — ради неё правило №6 и есть.

    И пишет её даже при --no-notify: молчание чата и молчание журнала — два
    разных решения, а раньше их не было ни одного.
    """
    monkeypatch.setattr(core, "DEFAULT_SNAPSHOT_ROOT", tmp_path / "snapshots")
    written = []
    monkeypatch.setattr(run_scheduled, "log_run",
                        lambda *a, **kw: written.append(kw))
    make_snapshot(tmp_path, {"x": ("ok", "")})
    run_scheduled.main(args_for(tmp_path, [{"username": "x"}], ["--no-notify"]),
                       probe=Spy(), summarise=Spy(),
                       ledger_append=lambda root, out: None)
    assert written and written[0]["status"] == "success"


def test_sandbox_turns_off_every_channel_not_just_the_chat(tmp_path):
    """Каналы наружу (чат, журнал) перечислены рядом с путями и гаснут вместе.

    Инвариант против следующей потери того же рода: новый канал добавляется в
    CHANNELS — и тем самым сам попадает под решение узла.
    """
    paths = run_scheduled.RunPaths(str(tmp_path / "snapshots"))
    assert not paths.production
    for name in run_scheduled.RunPaths.CHANNELS:
        assert getattr(paths, name) is False, name


def test_production_keeps_every_channel_on(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DEFAULT_SNAPSHOT_ROOT", tmp_path / "snapshots")
    paths = run_scheduled.RunPaths(str(tmp_path / "snapshots"))
    assert paths.production
    for name in run_scheduled.RunPaths.CHANNELS:
        assert getattr(paths, name) is True, name


def test_dry_run_says_whether_the_run_log_gets_a_row(tmp_path, capsys):
    """«Куда всё ляжет» — это и журнал тоже."""
    run_scheduled.main(args_for(tmp_path, [{"username": "x"}], ["--dry-run"]),
                       probe=Spy(), summarise=Spy(),
                       logger=lambda *a, **kw: None)
    out = capsys.readouterr().out
    assert "Run Log: нет (прогон не боевой)" in out


# ---------------------------------------------------------------------------
# Волна 5: «снято» считается по ЗНАЕМЫМ клеткам, а не по статусу
# ---------------------------------------------------------------------------
#
# Сетка, целиком переведённая в unknown (так делает правило окна продаж и так
# выглядит модуль, который ответил, но календарь не отдал), статус ok
# сохраняет. Итог в чат говорил «без данных 0%», а сводка по тем же целям в
# каждой клетке показывала «нет данных» — две цифры об одном дне.

ALL_UNKNOWN = {"Дом": {"2026-09-04": {"state": "unknown"},
                       "2026-09-05": {"state": "unknown"}}}
NO_SALES = {"Дом": {"2026-09-04": {"state": "sales_not_open"}}}


def report_of(tmp_path, objects, units_of=None):
    snap = make_snapshot(tmp_path, objects, units_of=units_of)
    return run_scheduled.run_report(core.read_snapshot(snap))


def test_a_grid_of_unknowns_is_not_a_taken_target(tmp_path):
    """Ни одной проверенной ночи — значит цель без данных, что бы ни стояло
    в статусе: сводка по такой цели печатает «нет данных» во всех клетках."""
    report = report_of(tmp_path, {"x": ("ok", "")}, units_of={"x": ALL_UNKNOWN})
    assert report["empty"] == 1 and report["empty_share"] == 1.0
    assert report["alert"]


def test_a_grid_without_open_sales_is_not_taken_either(tmp_path):
    """Продажи не открыты — цифры занятости нет, и знаменатель её не считает
    (occupancy_core: sales_not_open в known не входит)."""
    report = report_of(tmp_path, {"x": ("ok", "")}, units_of={"x": NO_SALES})
    assert report["empty"] == 1


def test_the_blank_grid_says_why_it_is_blank(tmp_path):
    """Причина у такой цели пустая (движок ответил, отказа не было), и
    дежурное «причина не записана» тут врёт: причина видна по самой сетке."""
    report = report_of(tmp_path, {"x": ("ok", "")}, units_of={"x": NO_SALES})
    assert "продажи не открыты" in dict(report["empty_rows"])["x"]


def test_one_known_night_is_enough_to_count_as_taken(tmp_path):
    """Полупустая сетка — это partial, а не «без данных»: цифра по ней есть."""
    mixed = {"Дом": {"2026-09-04": {"state": "unknown"},
                     "2026-09-05": {"state": "busy"}}}
    report = report_of(tmp_path, {"x": ("partial", "часть ночей не отдали")},
                       units_of={"x": mixed})
    assert report["empty"] == 0 and report["partial"] == 1


def test_the_chat_and_the_summary_agree_on_an_unknown_grid(tmp_path):
    """Та же цель тем же предикатом: цифра в чате обязана совпасть со сводкой."""
    obj = {"username": "x", "status": "ok", "units": ALL_UNKNOWN}
    assert build_summary._is_data_snapshot(obj) is False
    assert run_scheduled._has_data(obj) is False


# ---------------------------------------------------------------------------
# Волна 5: самая тяжёлая авария не молчит
# ---------------------------------------------------------------------------
#
# Битый реестр и пустая очередь выходили кодом 2 ДО того, как в прогоне
# появлялся аварийный выход: ноль строк в Run Log, ноль сообщений в чат.
# Единственным сигналом оставался OnFailure=cf-alert@ — тем же транспортом,
# который может быть заглушён стоп-краном.

def broken_registry_run(tmp_path, targets=({"username": "x"},)):
    recipes = tmp_path / "recipes.json"
    recipes.write_text("{это не json", encoding="utf-8")
    logged, notified = [], []
    probe = Spy()
    code = run_scheduled.main(
        ["--targets", write_targets(tmp_path, list(targets)),
         "--recipes", str(recipes),
         "--snapshot-root", str(tmp_path / "snapshots")],
        probe=probe, summarise=Spy(),
        logger=lambda *a, **kw: logged.append(kw),
        notifier=lambda t, m, **kw: notified.append(kw["report"]) or (1, 1, 0))
    return code, probe, logged, notified


def test_a_broken_registry_leaves_a_row_and_a_message(tmp_path):
    """Реестр не читается — прогон не состоялся ЦЕЛИКОМ. Это ровно тот случай,
    когда след нужнее всего, а его не было ни в журнале, ни в чате."""
    code, probe, logged, notified = broken_registry_run(tmp_path)
    assert code == 2 and probe.calls == []
    assert logged and logged[0]["status"] == "failed"
    assert logged[0]["errors"] and "реестр" in logged[0]["errors"][0].lower()
    assert notified and notified[0]["alert"]


def test_an_empty_queue_leaves_a_row_and_a_message(tmp_path):
    """Ни одной снимаемой цели — день слежки потерян весь. Молчать нельзя."""
    path = write_targets(tmp_path, [{"username": "art.glamp"}])
    recipes = tmp_path / "recipes.json"
    recipes.write_text('{"art.glamp": {"engine": "none"}}', encoding="utf-8")
    logged, notified = [], []
    code = run_scheduled.main(
        ["--targets", path, "--recipes", str(recipes),
         "--snapshot-root", str(tmp_path / "snapshots")],
        probe=Spy(), summarise=Spy(),
        logger=lambda *a, **kw: logged.append(kw),
        notifier=lambda t, m, **kw: notified.append(kw["report"]) or (1, 1, 0))
    assert code == 2
    assert logged and logged[0]["status"] == "failed"
    assert notified and "ни одной цели" in notified[0]["alert"]


def test_a_missing_targets_file_leaves_a_row_and_a_message(tmp_path):
    logged, notified = [], []
    code = run_scheduled.main(
        ["--targets", str(tmp_path / "нет.json"),
         "--snapshot-root", str(tmp_path / "snapshots")],
        probe=Spy(), summarise=Spy(),
        logger=lambda *a, **kw: logged.append(kw),
        notifier=lambda t, m, **kw: notified.append(kw["report"]) or (1, 1, 0))
    assert code == 2
    assert logged and logged[0]["status"] == "failed"
    assert notified and notified[0]["alert"]


def test_the_skipped_targets_are_named_in_the_broken_run(tmp_path):
    """Пустая очередь — это ещё и список «почему никого нет»: без него
    сообщение об аварии не даёт человеку следующего шага."""
    path = write_targets(tmp_path, [{"username": "a"}, {"username": "b"}])
    notified = []
    run_scheduled.main(
        ["--targets", path, "--snapshot-root", str(tmp_path / "snapshots"),
         "--recipes", str(tmp_path / "нет-реестра.json")],
        probe=Spy(), summarise=Spy(), logger=lambda *a, **kw: None,
        notifier=lambda t, m, **kw: notified.append(kw["report"]) or (1, 1, 0))
    assert len(notified[0]["skipped"]) == 2


def test_dry_run_stays_silent_on_a_broken_registry(tmp_path, capsys):
    """--dry-run не трогает ничего — в том числе журнал и чат: он показывает,
    что БУДЕТ, и сам по себе прогоном не является."""
    recipes = tmp_path / "recipes.json"
    recipes.write_text("{это не json", encoding="utf-8")
    logged, notified = [], []
    code = run_scheduled.main(
        ["--targets", write_targets(tmp_path, [{"username": "x"}]),
         "--recipes", str(recipes),
         "--snapshot-root", str(tmp_path / "snapshots"), "--dry-run"],
        probe=Spy(), summarise=Spy(),
        logger=lambda *a, **kw: logged.append(kw),
        notifier=lambda t, m, **kw: notified.append(kw) or (0, 0, 0))
    assert code == 2 and logged == [] and notified == []
    assert "реестр" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# Волна 5: строка ошибок не обвиняет чат за работу собственного сита
# ---------------------------------------------------------------------------

def test_sent_counts_reads_the_sieve_too():
    assert run_scheduled._sent_counts((1, 3, 2)) == (1, 3, 2)
    # Старые формы ответа уведомителя терпим: строка журнала не стоит падения.
    assert run_scheduled._sent_counts((1, 2)) == (1, 2, 0)
    assert run_scheduled._sent_counts(2) == (2, 2, 0)
    assert run_scheduled._sent_counts(None) == (0, 0, 0)


def test_notify_targets_counts_what_the_sieve_swallowed(monkeypatch):
    """Сито съело сообщение — это не отправка и не отказ транспорта, а третий
    исход, и назвать его обязан тот, кто его и устроил."""
    monkeypatch.setattr(run_scheduled.build_summary, "load_history",
                        lambda root, depth=None: {})
    sent_texts = []
    result = run_scheduled.notify_targets(
        [], ["2026-09"], sender=lambda text: sent_texts.append(text) or True,
        report=REPORT, sleeper=lambda s: None, guard=FakeGuard(set()))
    assert result == (0, 1, 1)
    assert sent_texts == []


def test_suppressed_repeat_does_not_blame_the_chat(tmp_path):
    """Строка ошибок обвиняла чат в молчании, которое устроило сито повторов:
    человек шёл чинить исправный транспорт."""
    make_snapshot(tmp_path, {"x": ("ok", "")})
    logged = []
    run_scheduled.main(args_for(tmp_path, [{"username": "x"}]),
                       probe=Spy(), summarise=Spy(),
                       notifier=lambda *a, **kw: (0, 1, 1),
                       logger=lambda *a, **kw: logged.append(kw),
                       ledger_append=lambda root, out: None)
    assert not any("не ушло" in e for e in logged[0]["errors"])
    assert "повтор" in logged[0]["note"]


def test_a_silent_transport_is_still_blamed(tmp_path):
    """Обратный случай не должен пострадать: сообщение дошло до транспорта и
    не ушло — это и есть заглушённый или недоступный чат."""
    make_snapshot(tmp_path, {"x": ("ok", "")})
    logged = []
    run_scheduled.main(args_for(tmp_path, [{"username": "x"}]),
                       probe=Spy(), summarise=Spy(),
                       notifier=lambda *a, **kw: (0, 2, 1),
                       logger=lambda *a, **kw: logged.append(kw),
                       ledger_append=lambda root, out: None)
    assert any("не ушло" in e for e in logged[0]["errors"])


def test_units_report_rebuilt_only_in_production(tmp_path, monkeypatch):
    """Поюнитный отчёт пересобирается в бою и молчит в песочнице (тикет 14).

    Отчёт пишет версионируемый md в docs/ — из проверочного прогона ему
    выходить нельзя, ровно как сводке. Падение рендера прогон не роняет.
    """
    import run_scheduled as rs

    calls = []
    class P:  # минимальный двойник RunPaths
        def __init__(self, root): self.snapshot_root = root
    rs.rebuild_units_report(P(tmp_path), report=lambda a: calls.append(("r", a)),
                            page=lambda a: calls.append(("p", a)))
    assert calls == []                      # песочница — ни одного вызова
    # Подмена core.DEFAULT_SNAPSHOT_ROOT (как делают соседние тесты) бой НЕ
    # включает — только настоящий каталог репозитория.
    monkeypatch.setattr(rs.core, "DEFAULT_SNAPSHOT_ROOT", tmp_path)
    rs.rebuild_units_report(P(tmp_path), report=lambda a: calls.append(("r", a)),
                            page=lambda a: calls.append(("p", a)))
    assert calls == []
    monkeypatch.setattr(rs, "is_real_repo_root", lambda root: True)
    rs.rebuild_units_report(P(tmp_path), today="2026-09-05",
                            report=lambda a: calls.append(("r", a)),
                            page=lambda a: calls.append(("p", a)))
    assert calls == [("r", ["--today", "2026-09-05"]), ("p", [])]
    def boom(a): raise RuntimeError("рендер упал")
    rs.rebuild_units_report(P(tmp_path), report=boom, page=lambda a: None)  # не бросает
