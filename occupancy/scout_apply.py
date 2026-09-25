#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Перенос черновиков scout.py в реестр рецептов и список целей — одной командой.

scout.py по правилу тикета 11 в реестр не пишет никогда: он кладёт черновик
в scout-out/<ключ>.json с вердиктом high / low / refuse. 04.09 черновики
Самары переносились руками; для третьего трека (топ-50 глэмпингов России,
поручение заказчика 07.09.2026) перенос делает этот скрипт — воспроизводимо и с
теми же предохранителями, что были в голове у человека:

- рабочими в реестр попадают ТОЛЬКО черновики с verdict=high и полным
  recipe_draft (status ok), чей recipe_hash подтверждён smoke-прогоном
  (--verification: status ok/partial и известные клетки); low/refuse печатаются
  списком «на доразведку агентом» и в реестр
  идут честной записью no_module/broken, чтобы цель не пропала из сводки молча
  (флаг --with-honest);
- действующий рецепт ok НЕ затирается черновиком (--force, чтобы переписать);
- реестр не правится, пока идёт прогон (настоящие flock реестра/снапшотов и открытый
  снапшот без finished_at) — грабля 04.09 20:25, когда прогон затёр ручную
  правку;
- цели добавляются в targets.json с region/title/source, ключ навсегда.
  scout_key позволяет использовать готовый черновик под старым ключом цели;
  cohorts/cohort_details дописываются существующей цели без смены региона.

usage:
  scout_apply.py --manifest <json> [--scout-out DIR] [--recipes R] [--targets T]
                 [--verification JSON] [--with-honest] [--force] [--dry-run]
<json> — список [{key, site, title, region, priority?, source?}] (что разведано
и как звать), порядок строк = важность внутри региона.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import occupancy_core as core
import scout as scout_mod
import cli


def run_in_progress(snapshot_root: Path) -> str | None:
    # Файлы flock остаются после выхода: само наличие файла не означает
    # активный сбор. Настоящие блокировки берутся в main до чтения реестра.
    try:
        runs = sorted(p for p in snapshot_root.iterdir() if p.is_dir())
    except OSError:
        return None
    for p in runs[-2:]:
        try:
            run = json.load(open(p / "run.json", encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not run.get("finished_at"):
            return f"снапшот {p.name} не закрыт (finished_at пуст) — прогон, вероятно, идёт"
    return None


def honest_entry(draft: dict, site: str) -> dict:
    """Запись реестра для low/refuse: цель видна в сводке строкой «нет данных»."""
    engine = draft.get("engine")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    evidence = draft.get("evidence") or []
    reason = draft.get("refusal_reason") or draft.get("note") or ""
    fetched_site = any("HTTP 200" in str(e.get("result")) and
                       str(e.get("what", "")).startswith("страница") for e in evidence)
    host = (urlsplit(site).hostname or "").lower().removeprefix("www.")
    generic_landing = host in {"telegram.im", "t.me", "telegram.me", "wa.me",
                              "wa.clck.bar", "taplink.cc", "taplink.ru",
                              "instagram.com", "vk.com"}
    clean_absence = (draft.get("verdict") == "refuse" and not engine
                     and fetched_site and not generic_landing and not draft.get("access_refused")
                     and "маркеров" in reason and "не снялись" not in reason)
    if clean_absence:
        return {"site": site, "engine": "none", "status": "no_module",
                "request": {}, "discovered_at": now,
                "notes": "scout: движок не опознан — " + (draft.get("refusal_reason") or draft.get("note") or ""),
                "source_urls": [e.get("url") for e in draft.get("evidence") or [] if e.get("url")][:6]}
    rd = draft.get("recipe_draft") or {}
    return {"site": site, "engine": engine or rd.get("engine") or "unknown", "status": "broken",
            "request": rd.get("request") or {}, "discovered_at": now,
            "broken_reason": "scout: " + (reason or "нет проверенного рецепта; нужна проверка пробником"),
            "notes": "черновик scout, нужна доразведка агентом", "source_urls":
            [e.get("url") for e in draft.get("evidence") or [] if e.get("url")][:6]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--scout-out", default=str(scout_mod.DEFAULT_OUT_DIR))
    ap.add_argument("--recipes", default=str(core.DEFAULT_RECIPES))
    ap.add_argument("--targets", default=str(core.DEFAULT_TARGETS))
    ap.add_argument("--snapshot-root", default=str(core.DEFAULT_SNAPSHOT_ROOT))
    ap.add_argument("--with-honest", action="store_true", help="low/refuse — честной записью в реестр")
    ap.add_argument("--force", action="store_true", help="переписывать действующие ok-рецепты")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verification", help="JSON key → {recipe_hash,status,known_cells} после smoke-прогона")
    a = ap.parse_args(argv)
    try:
        with contextlib.ExitStack() as locks:
            locks.enter_context(core.run_lock(cli._registry_lock_path(a.recipes)))
            locks.enter_context(core.run_lock(Path(a.snapshot_root) / cli.LOCK_NAME))
            busy = run_in_progress(Path(a.snapshot_root))
            if busy and not a.dry_run:
                print(f"СТОП: {busy}", file=sys.stderr)
                return 2
            return _apply(a)
    except (core.RunLockError, OSError, ValueError) as exc:
        print(f"СТОП: {exc}", file=sys.stderr)
        return 2


def _apply(a) -> int:
    manifest = json.load(open(a.manifest, encoding="utf-8"))
    recipes = json.load(open(a.recipes, encoding="utf-8")) if Path(a.recipes).exists() else {}
    targets = json.load(open(a.targets, encoding="utf-8")) if Path(a.targets).exists() else []
    known = {t.get("username"): t for t in targets if isinstance(t, dict)}
    verification = json.load(open(a.verification, encoding="utf-8")) if a.verification else {}
    out_dir = Path(a.scout_out)
    added_r, added_t, kept, todo = [], [], [], []
    for row in manifest:
        key, site = row["key"], row.get("site") or ""
        scout_key = row.get("scout_key") or key
        draft_path = out_dir / f"{scout_key}.json"
        draft = json.load(open(draft_path, encoding="utf-8")) if draft_path.exists() else None
        if key not in known:
            t = {"username": key, "site": site, "priority": row.get("priority", 50),
                 "region": row.get("region"), "title": row.get("title") or key,
                 "source": row.get("source") or "scout_apply"}
            if row.get("note"):
                t["note"] = row["note"]
            targets.append(t)
            known[key] = t
            added_t.append(key)
        target = known[key]
        if row.get("cohorts"):
            target["cohorts"] = list(dict.fromkeys(target.get("cohorts", []) + row["cohorts"]))
        if row.get("cohort_details"):
            target.setdefault("cohort_details", {}).update(row["cohort_details"])
        existing = recipes.get(key)
        if existing and existing.get("status") == "ok" and not a.force:
            kept.append(key)
            continue
        draft = draft or {"note": "черновика scout нет; нужна проверка канала"}
        proposed = draft.get("recipe_draft") or {}
        receipt = verification.get(scout_key) or {}
        verified = (proposed.get("status") == "ok" and proposed.get("source_urls")
                    and receipt.get("recipe_hash") == core.recipe_hash(proposed)
                    and receipt.get("status") in ("ok", "partial")
                    and receipt.get("known_cells", 0) > 0)
        if draft.get("verdict") == "high" and verified:
            rec = dict(draft["recipe_draft"])
            rec.setdefault("discovered_at", draft.get("scouted_at"))
            rec["notes"] = (rec.get("notes") or "") + f" | scout {draft.get('scouted_at')}: проверен пробником ({receipt.get('status')}, известных клеток {receipt['known_cells']}); перенесён scout_apply"
            rec["verification"] = receipt
            rec.setdefault("source_urls", [e.get("url") for e in draft.get("evidence") or [] if e.get("url")][:6])
            recipes[key] = rec
            added_r.append(key)
        else:
            if draft.get("verdict") == "high":
                draft = dict(draft, note="нет успешной проверки пробником текущего recipe_hash")
            todo.append((key, f"verdict {draft.get('verdict')}, движок {draft.get('engine')}, "
                              f"не хватает {draft.get('missing_params')}; {draft.get('refusal_reason') or draft.get('note') or ''}"))
            if a.with_honest and not existing:
                recipes[key] = honest_entry(draft, site)
    print(f"рецептов добавлено {len(added_r)}: {added_r}")
    print(f"целей добавлено {len(added_t)}: {added_t}")
    print(f"оставлены действующие ok ({len(kept)}): {kept}")
    print(f"на доразведку агентом ({len(todo)}):")
    for k, why in todo:
        print(f"  - {k}: {why}")
    if a.dry_run:
        print("dry-run: файлы не тронуты")
        return 0
    core.save_recipes(a.recipes, recipes)
    core._dump_json(Path(a.targets), targets)
    print(f"записано: {a.recipes} ({len(recipes)} рецептов), {a.targets} ({len(targets)} целей)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
