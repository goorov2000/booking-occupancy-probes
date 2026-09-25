"""Реестр рецептов и targets: чтение/запись, битый JSON -> внятная ошибка."""
import json

import pytest

import occupancy_core as core


RECIPE = {
    "site": "https://example.com",
    "engine": "travelline",
    "status": "ok",
    "request": {
        "url_template": "https://ibe.tlintegration.ru/x?from={date_from}&to={date_to}",
        "method": "GET",
        "params": {},
        "headers": {},
        "date_substitution": "iso_range",
    },
    "discovered_at": "2026-08-14T10:00:00+03:00",
    "notes": "",
    "source_urls": ["https://example.com"],
}


def test_recipes_roundtrip(tmp_path):
    path = tmp_path / "recipes.json"
    core.save_recipes(path, {"obj_a": RECIPE})
    assert core.load_recipes(path) == {"obj_a": RECIPE}


def test_recipes_missing_file_is_empty_registry(tmp_path):
    assert core.load_recipes(tmp_path / "recipes.json") == {}


def test_recipes_broken_json_raises_registry_error(tmp_path):
    path = tmp_path / "recipes.json"
    path.write_text('{"obj_a": {', encoding="utf-8")
    with pytest.raises(core.RegistryError) as e:
        core.load_recipes(path)
    assert "recipes.json" in str(e.value)
    assert not isinstance(e.value, json.JSONDecodeError)


def test_targets_roundtrip(tmp_path):
    path = tmp_path / "targets.json"
    targets = [{"username": "obj_a", "site": "https://example.com", "priority": 1}]
    core.save_targets(path, targets)
    assert core.load_targets(path) == targets


def test_targets_missing_file_raises_registry_error(tmp_path):
    with pytest.raises(core.RegistryError) as e:
        core.load_targets(tmp_path / "targets.json")
    assert "targets.json" in str(e.value)


def test_targets_wrong_shape_raises_registry_error(tmp_path):
    path = tmp_path / "targets.json"
    path.write_text('{"username": "obj_a"}', encoding="utf-8")
    with pytest.raises(core.RegistryError):
        core.load_targets(path)


# ---------------------------------------------------------------------------
# Версия пробника (поле probe_version снапшота, тикет 10)
# ---------------------------------------------------------------------------

def test_every_engine_declares_its_probe_version():
    """Ряд сезонности живёт годами: «снято старым кодом» надо отличать от
    «снято новым». Общая версия транспорта для этого не годится — она не
    меняется, когда меняется РАЗБОР ответа конкретного движка."""
    import probes
    from probes import _common

    for engine, module in probes.ENGINES.items():
        version = getattr(module, "PROBE_VERSION", None)
        assert isinstance(version, int) and version >= 1, engine
        assert _common.probe_version_of(engine, module) == f"{engine}@{version}"


def test_bnovo_is_signed_with_the_version_of_the_code_that_took_it():
    """bnovo снимается кодом travelline — и подпись обязана это отражать."""
    from probes import bnovo, travelline

    assert bnovo.PROBE_VERSION == travelline.PROBE_VERSION


def test_parsing_changes_of_2026_09_04_raised_the_versions():
    """Правки 04.09 сменили смысл полей (фонд по своему горизонту, фонд
    номера из rooms_count) — версии подняты, иначе снимки до и после
    подписались бы одинаково."""
    from probes import bronirui, travelline, uhotels

    assert travelline.PROBE_VERSION >= 2
    assert bronirui.PROBE_VERSION >= 2
    assert uhotels.PROBE_VERSION >= 2


# ---------------------------------------------------------------------------
# Волна 5 (ревью wave4-core, пункт 1): битая ЗАПИСЬ не стоит всего прогона
# ---------------------------------------------------------------------------
#
# В реестре 86 рецептов, 61 из них записан машинно (разведка 04.09). Запись,
# которая не словарь (склейка двух файлов, полуготовый черновик, правка руки),
# роняла плановый прогон голым traceback ещё ДО открытия снапшота:
# run_scheduled.split_queue зовёт recipe.get("engine") вне своего
# try/except RegistryError, и AttributeError уносил день по ВСЕМ целям —
# ни строки в Run Log, ни алерта, ни единого снятого объекта.

def test_entry_that_is_not_a_dict_is_quarantined_not_fatal(tmp_path, capsys):
    path = tmp_path / "recipes.json"
    path.write_text(json.dumps({"good": RECIPE,
                                "bad": ["url_template", "https://x"]},
                               ensure_ascii=False), encoding="utf-8")

    recipes = core.load_recipes(path)

    assert recipes["good"] == RECIPE          # соседи не пострадали
    bad = recipes["bad"]
    assert isinstance(bad, dict)
    assert bad["status"] == "broken"
    assert "не словарь" in bad["broken_reason"]
    assert "list" in bad["broken_reason"]     # сказано, ЧТО там лежит
    err = capsys.readouterr().err
    assert "bad" in err and "recipes.json" in err


def test_quarantine_keeps_what_was_written_there(tmp_path):
    """Заготовку не выбрасываем: прогон переписывает реестр целиком, и
    молчаливое стирание чужой записи хуже битой записи."""
    path = tmp_path / "recipes.json"
    path.write_text(json.dumps({"bad": ["черновик", 17]}, ensure_ascii=False),
                    encoding="utf-8")

    recipes = core.load_recipes(path)
    assert recipes["bad"]["invalid_entry"] == ["черновик", 17]

    core.save_recipes(path, recipes)          # так его сохранит cli
    assert core.load_recipes(path)["bad"]["invalid_entry"] == ["черновик", 17]


def test_quarantined_entry_survives_the_scheduler_queue(tmp_path):
    """Тот самый вызов, на котором прогон падал: split_queue вне try."""
    import run_scheduled

    path = tmp_path / "recipes.json"
    path.write_text(json.dumps({"good": RECIPE, "bad": [1, 2]},
                               ensure_ascii=False), encoding="utf-8")
    targets = [{"username": "good"}, {"username": "bad"}]

    queue, skipped = run_scheduled.split_queue(targets,
                                              core.load_recipes(path))

    assert queue == ["good"]
    assert [name for name, _, _ in skipped] == ["bad"]
    assert [(name, group) for name, _, group in skipped] == [("bad", "scout")]
    assert "разведк" in dict((n, why) for n, why, _ in skipped)["bad"]
