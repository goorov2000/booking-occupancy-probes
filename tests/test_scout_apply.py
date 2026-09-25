"""Запись когорты и рецептов требует проверки и настоящей блокировки."""
import json

import cli
import occupancy_core as core
import scout_apply


def setup_case(tmp_path, *, existing=False, verification=True):
    rec = {"engine": "bnovo", "site": "https://example.com", "status": "ok",
           "request": {"params": {"uid": "test"}},
           "source_urls": ["https://example.com/booking"], "notes": "draft"}
    draft = {"verdict": "high", "recipe_draft": rec, "scouted_at": "2026-09-08"}
    (tmp_path / "new.json").write_text(json.dumps(draft))
    manifest = [{"key": "old" if existing else "new", "scout_key": "new",
                 "region": "ru50", "site": "https://example.com",
                 "cohorts": ["ru50"], "cohort_details": {"ru50": {"rank": 1}}}]
    files = {"manifest.json": manifest,
             "recipes.json": {"old": {**rec, "notes": "working"}} if existing else {},
             "targets.json": [{"username": "old", "region": "samara"}] if existing else [],
             "verified.json": {"new": {"recipe_hash": core.recipe_hash(rec),
                                        "status": "ok", "known_cells": 2}}}
    for name, data in files.items():
        (tmp_path / name).write_text(json.dumps(data))
    args = ["--manifest", str(tmp_path / "manifest.json"), "--scout-out", str(tmp_path),
            "--recipes", str(tmp_path / "recipes.json"), "--targets", str(tmp_path / "targets.json"),
            "--snapshot-root", str(tmp_path / "snapshots"), "--with-honest"]
    if verification:
        args += ["--verification", str(tmp_path / "verified.json")]
    return args


def test_high_without_probe_receipt_cannot_become_working_recipe(tmp_path):
    assert scout_apply.main(setup_case(tmp_path, verification=False)) == 0
    rec = json.loads((tmp_path / "recipes.json").read_text(encoding="utf-8"))["new"]
    assert rec["status"] == "broken"
    assert "провер" in rec["broken_reason"]


def test_verified_recipe_and_existing_region_survive_repeated_cohort_update(tmp_path):
    args = setup_case(tmp_path, existing=True)
    assert scout_apply.main(args) == scout_apply.main(args) == 0
    targets = json.loads((tmp_path / "targets.json").read_text(encoding="utf-8"))
    assert targets == [{"username": "old", "region": "samara", "cohorts": ["ru50"],
                        "cohort_details": {"ru50": {"rank": 1}}}]
    assert json.loads((tmp_path / "recipes.json").read_text(encoding="utf-8"))["old"]["notes"] == "working"


def test_verified_high_is_applied(tmp_path):
    assert scout_apply.main(setup_case(tmp_path)) == 0
    assert json.loads((tmp_path / "recipes.json").read_text(encoding="utf-8"))["new"]["status"] == "ok"


def test_missing_draft_is_visible_as_unresolved_recipe(tmp_path):
    args = setup_case(tmp_path, verification=False)
    (tmp_path / "new.json").unlink()
    assert scout_apply.main(args) == 0
    assert json.loads((tmp_path / "recipes.json").read_text(encoding="utf-8"))["new"]["status"] == "broken"


def test_active_registry_lock_refuses_without_writing(tmp_path):
    args = setup_case(tmp_path)
    original = (tmp_path / "recipes.json").read_bytes()
    with core.run_lock(cli._registry_lock_path(tmp_path / "recipes.json")):
        assert scout_apply.main(args) == 2
    assert (tmp_path / "recipes.json").read_bytes() == original


def test_antibot_is_unknown_engine_not_absent_module():
    rec = scout_apply.honest_entry(
        {"verdict": "refuse", "access_refused": True,
         "refusal_reason": "captcha"}, "https://example.com")
    assert rec["status"] == "broken"


def test_messenger_landing_page_cannot_prove_object_has_no_module():
    rec = scout_apply.honest_entry(
        {"verdict": "refuse", "refusal_reason": "маркеров модуля бронирования нет",
         "evidence": [{"url": "https://telegram.im/", "what": "страница /",
                       "result": "HTTP 200, 1200 знаков"}]}, "https://telegram.im/")
    assert rec["status"] == "broken"
