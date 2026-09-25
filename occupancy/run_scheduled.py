# -*- coding: utf-8 -*-
"""Плановый прогон /glamping-occupancy без агента (systemd cf-occupancy.timer).

Зачем отдельная точка входа. Прогон скилла делает АГЕНТ: он разбирает очередь
целей на три ветки (рабочий рецепт -> пробник, битый или отсутствующий ->
разведка, агрегатор -> браузер), а в конце публикует артефакт. Из таймера
доступна ровно одна из этих веток — детерминированные пробники, — и её одной
хватает, чтобы цифры не протухали: рецепты уже разведаны, а календарь меняется
каждый день.

Что делает этот скрипт:
  1. читает targets.json и отправляет В ОДИН вызов cli.py ВСЕ цели подряд —
     один прогон, один каталог снапшота (иначе динамика между снимками поедет);
  2. пересобирает сводку build_summary.py (occupancy.md + html артефакта);
  3. дописывает производный слой сезонности (ledger.append) — отчёты по ряду
     читают его, а не всю историю снапшотов;
  4. шлёт в телеграм ОДИН итог прогона плюс отдельные сообщения по целям с
     полем "notify" в targets.json (текст — tg_digest.py, транспорт —
     cf.notify) — best-effort;
  5. пишет строку в CF Run Log (правило №6 завода) — best-effort.

Чего он НЕ делает и не должен: не разведывает объекты (у битого рецепта в
сводке честно встанет «нет данных» с причиной — это сигнал позвать агента),
не ходит браузером на агрегаторы и НЕ ПУБЛИКУЕТ артефакт: инструмент
публикации есть только у агента. То есть таймер держит свежими ДАННЫЕ и файлы
сводки, а страница по постоянной ссылке обновляется следующим прогоном агента.

Цели передаются пробнику ВСЕ, у кого есть рецепт знакомого движка, включая
помеченные broken: cli.py напишет им честную строку с причиной, и объект не
пропадёт из сводки молча.

Что изменилось 04.09.2026 (волна 2 спеки поюнитной сезонности)
--------------------------------------------------------------
- Общий контракт прогона доводится до cli: --deadline-min (150 мин при
  TimeoutStartSec=180min юнита; было 90/120 до 08.09.2026), --planned-at (плановый момент таймера, из
  него считается gap_days снимка) и --inventory-days (горизонт ФОНДА — главный
  рычаг цены прогона).
- КОД 1 БОЛЬШЕ НЕ ГАСИТ ХВОСТ. Раньше любой ненулевой код cli означал, что
  сводка не пересобрана, дайджест не отправлен и строки в Run Log нет: день
  данных на диске есть, а наружу он не вышел. Теперь 1 = «часть целей не
  успела к дедлайну», работа продолжается до конца и лишь код прогона остаётся
  ненулевым; 2 = настоящая ошибка (битый реестр, занятая блокировка), и только
  она останавливает конвейер.
- Алерт по ДОЛЕ целей без данных (--max-empty-share): раньше прогон рапортовал
  успех, даже когда insufficient_data отдали ВСЕ цели, и OnFailure=cf-alert@
  не срабатывал.
- Месяцы сводки скользят от сегодняшнего дня (core.summary_months), а не берутся
  из календарной константы DEFAULT_MONTHS: с 01.11.2026 она давала бы «нет
  данных» по всем объектам.

Что изменилось 04.09.2026 вечером (волна 3, ревью волны 2)
----------------------------------------------------------
- ПРОВЕРОЧНЫЙ ПРОГОН ИЗОЛИРОВАН ЦЕЛИКОМ. Раньше песочница была ложной: прогон
  в чужой --snapshot-root уводил туда снапшоты и ledger, но сводку писал в
  версионируемый docs/research/.../occupancy.md, а дайджест слал владельцу
  боевым транспортом (инцидент со спамом 04.09). Теперь небоевой каталог
  снапшотов сам уводит выход сводки рядом с собой и сам включает --no-notify:
  текст сообщения печатается в консоль вместо чата.
- Реестр рецептов (--recipes) и параллелизм (--host-workers) доходят до
  пробника: очередь строилась по одному реестру, а съём и пометки broken шли
  по боевому. Проверочный прогон вдобавок работает по КОПИИ реестра рядом со
  своей песочницей — cli пишет в реестр по ходу съёма, а замок берёт по пути
  снапшота, то есть параллельный прогон правил живой файл.
- КОД ВОЗВРАТА ЗНАЧИТ ОДНО. Ненулевой код = OnFailure=cf-alert@ = сообщение
  владельцу, поэтому его заслуживает только то, что человек обязан разобрать:
  прогон без данных. Опоздавшая цель — деградация (день на диске есть, причина
  у цели записана): она идёт в Run Log и в дайджест, но юнит не роняет, иначе
  один медленный хост шлёт один и тот же алерт каждые сутки.
- Повтор в чат схлопывается: тот же текст в окне --repeat-window-h не уходит
  второй раз (состояние — в agent-runtime/).
- Копия данных и хранение (тикет 16 п.1 и п.4): новые каталоги снапшотов и
  слой сезонности копируются во второе место, снапшоты старше 90 суток
  сжимаются в gzip на месте.

Что изменилось 04.09.2026 ночью (волна 4, ревью волны 3)
--------------------------------------------------------
- «БОЕВОЙ ИЛИ ПЕСОЧНИЦА» РЕШАЕТСЯ В ОДНОМ УЗЛЕ (RunPaths). Волна 3 уводила в
  песочницу сводку, реестр и копию тремя независимыми ключами, а четвёртый
  выход — слой сезонности — остался на боевом умолчании: проверочный прогон
  дописывал в живой ряд выдуманный прогон, и после отравления last_run_id
  следующий НАСТОЯЩИЙ прогон в слой уже не попадал (лечится только полной
  пересборкой). Теперь список выходов один (RunPaths.OUTPUTS), и прогон в
  песочницу отказывается стартовать, если хоть один из них смотрит наружу.
- Сито повторов помечает текст ТОЛЬКО после успешной отправки: заглушённый
  транспорт (стоп-кран, нет сети, нет токена) больше не съедает единственную
  новость прогона на шесть часов вперёд.
- Дайджест читает не всю историю снапшотов, а два последних снимка цели
  (depth), и датирует снимок ЕГО днём, а не днём прогона: у цели, которую
  таймер пропускает, «за сутки» превращается в честное «за N дней».
- Самые тяжёлые исходы (код пробников >= 2, несобранная сводка) больше не
  выходят молча: строка в Run Log со статусом failed и одно сообщение в чат.
- Уведомления обёрнуты в try, а число ушедших сообщений идёт в Run Log:
  молчание чата перестало быть неотличимым от исправной работы.

Что изменилось 04.09.2026 поздним вечером (волна 5, ревью волны 4)
------------------------------------------------------------------
- УЗЕЛ «БОЕВОЙ ИЛИ ПЕСОЧНИЦА» НАКРЫВАЕТ И RUN LOG. Волна 4 собрала список
  выходов из одних ПУТЕЙ, а строка журнала пути не имеет — и осталась
  снаружи: проверочный прогон в /tmp писал в боевую вкладку «плановый прогон
  по расписанию, целей N», то есть в журнале завода появлялся день, которого
  не было. Теперь у RunPaths два списка — OUTPUTS (файлы) и CHANNELS (чат и
  журнал), и умолчание логгера решает тот же узел.
- «СНЯТО» СЧИТАЕТСЯ ПО ЗНАЕМЫМ КЛЕТКАМ. Сетка, целиком переведённая в unknown
  (ветка «весь горизонт» правила окна продаж; модуль, который ответил, но
  календаря не отдал), статус ok сохраняет — и шла в итог как снятая цель:
  чат говорил «без данных 0%», а сводка по тем же целям в каждой клетке
  печатала «нет данных». Предикат теперь ОДИН со сводкой.
- САМАЯ ТЯЖЁЛАЯ АВАРИЯ БОЛЬШЕ НЕ МОЛЧИТ. Битый реестр и пустая очередь
  выходили кодом 2 ДО аварийного выхода прогона: ноль строк в Run Log, ноль
  сообщений в чат, а это потеря ВСЕГО дня по ВСЕМ целям. Единственным
  сигналом оставался OnFailure=cf-alert@ — тем же транспортом, который может
  быть заглушён стоп-краном.
- СТРОКА ОШИБОК НЕ ОБВИНЯЕТ ЧАТ за работу собственного сита повторов:
  уведомитель отдаёт третьим числом, сколько сообщений снято ситом, и
  «чат заглушён или недоступен» пишется только про то, что до транспорта
  дошло.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_summary  # noqa: E402
import build_units_page  # noqa: E402
import build_units_report  # noqa: E402
import cli  # noqa: E402
import ledger  # noqa: E402
import occupancy_core as core  # noqa: E402
import probes  # noqa: E402
import tg_digest  # noqa: E402
from probes._common import one_line  # noqa: E402

REPO_ROOT = SCRIPTS_DIR.parent   # витрина: <repo>/occupancy/ (было parents[3])
# Интерпретатор cf можно переопределить (CF_PYTHON) — на случай, если контур
# агентства когда-нибудь уедет в свой репозиторий со своим .venv (аудит 14.09.2026).
CF_PYTHON = Path(os.environ.get("CF_PYTHON") or REPO_ROOT / ".venv" / "bin" / "python")
# Витрина: пакет `cf` (Run Log, транспорт телеграма) — часть внешнего контура и в
# репозиторий не входит. Оба вызова ниже (log_run, send_telegram) best-effort:
# без пакета печатается предупреждение в stderr, прогон и файлы не страдают.
LOG_RUN_TIMEOUT_SEC = 120

# Дедлайн прогона: ~83% от TimeoutStartSec=180min юнита cf-occupancy.service.
# Запас нужен на хвост прогона — сводку, ledger и телеграм: их SIGTERM от
# systemd оборвал бы уже ПОСЛЕ того, как данные сняты.
#
# Редакция 08.09.2026: 90 -> 150 минут. Третий трек слежки (топ-50 глэмпингов
# России по Instagram, поручение заказчика 07.09) добавляет ~50 целей к 86, а
# узкое место прогона — не число хостов, а общие API-хосты движков: все Bnovo
# ходят в public-api.reservationsteps.ru, все TravelLine — в один хост, и
# пауза >= 1.2 с на хост выстраивает их в одну очередь. 85 целей шли 55 минут
# (07.09), линейная оценка на 135 целей — ~90 минут, то есть ровно старый
# дедлайн без запаса. Публикация страниц (cf-occupancy-publish.timer)
# сдвинута на 09:45 той же правкой — она ждёт закрытого прогона.
DEFAULT_DEADLINE_MIN = 150.0
# Плановый час = OnCalendar таймера. Меняется парой: здесь и в .timer.
DEFAULT_PLANNED_TIME = "06:30"
# Порог алерта по доле целей без данных (стартовый по тикету 16).
DEFAULT_MAX_EMPTY_SHARE = 0.2
# Пауза между сообщениями в телеграм: ретраев у транспорта нет, и пачка
# сообщений подряд теряется в Bot API молча.
TG_PAUSE_SEC = 1.5
# Окно схлопывания повторов в чате. Меньше суток нарочно: плановый дайджест
# уходит раз в день и обязан проходить всегда, а инцидент 04.09 — это один и
# тот же текст много раз за полчаса.
DEFAULT_REPEAT_WINDOW_H = 6.0
# Сколько снимков цели нужно дайджесту: последний и предыдущий, больше он не
# читает. Без ограничения путь уведомления разбирал ВСЮ историю снапшотов —
# 2,55 с и 465 объекто-снимков на 38 прогонах ради двух нужных, и это растёт
# линейно с каждым днём слежки (тот же счёт, что в build_summary.HISTORY_DEPTH).
NOTIFY_HISTORY_DEPTH = 2
NOTIFY_STATE_FILE = REPO_ROOT / "agent-runtime" / "occupancy-notify-state.json"
# Второе место для копии данных (тикет 16 п.1). Тот же диск, другое дерево:
# это защита от стирания и порчи каталога, а не от гибели машины — сказать
# честно важнее, чем обещать бэкап, которого нет.
DEFAULT_BACKUP_DIR = (REPO_ROOT / "agent-runtime" / "backup"
                      / "glamping-occupancy")
# Хранение (тикет 16 п.4): сырые снапшоты держим всегда (правило №1), но
# старше этого срока — сжатыми на месте.
DEFAULT_ARCHIVE_AFTER_DAYS = 90
# Сколько дневных копий слоя сезонности держим: слой производный и целиком
# пересобирается из снапшотов, глубокая история копий ему не нужна.
DEFAULT_LEDGER_COPIES = 14


#: Кому адресована строка пропуска. Ключ группы -> имя для чата и журнала.
#:
#: Зачем группы (09.09.2026). Раньше все пропущенные уходили в чат ОДНИМ
#: числом «Пропущено до пробника: 38 (работа агента: разведка и агрегаторы)»,
#: и владелец каждое утро читал 38 как невыполненную работу. На деле 18 из
#: них — объекты, у которых онлайн-канала НЕТ ВОВСЕ: это проверено, записано
#: в рецепт и агенту там делать нечего. Смешивать «мы ещё не разведали» с
#: «разведали и канала нет» — значит звать человека туда, где работы нет.
SKIP_LABELS = {
    "scout": "ждут разведки",
    "aggregator": "агрегаторы (ветка агента)",
    "no_module": "без онлайн-канала",
}
#: Порядок групп в сводке. «Ждут разведки» первой и ВСЕГДА, даже нулём:
#: единственная цифра здесь, которая требует работы, и её отсутствие —
#: новость («ждут разведки: 0»).
SKIP_ORDER = ("scout", "aggregator", "no_module")


def _checked_on(recipe: dict) -> str:
    """«, проверено 09.09.2026» по discovered_at рецепта; нет даты -> пусто.

    Дата важнее самой причины: «канала нет» без даты не отличить от
    «канала нет, но смотрели в прошлом году».
    """
    raw = str((recipe or {}).get("discovered_at") or "")
    try:
        return f", проверено {datetime.fromisoformat(raw):%d.%m.%Y}"
    except ValueError:
        return ""


def skip_reason(recipe: dict) -> tuple[str, str]:
    """Рецепт цели, которую пробник не снимает -> (группа, причина словами).

    Группа отвечает на один вопрос: ЖДЁТ ли эта строка человека. Причина —
    на второй: что именно про объект уже известно.
    """
    if not recipe:
        return "scout", "рецепта нет в реестре — ждёт разведки"
    status = recipe.get("status")
    engine = recipe.get("engine") or ""
    if status == "no_module":
        return "no_module", f"без онлайн-канала бронирования{_checked_on(recipe)}"
    if status == "aggregator" or engine.startswith("aggregator-"):
        return "aggregator", (f"канал {engine or 'агрегатор'} — снимает агент "
                              f"браузером, пробника v1 нет")
    # "unknown" — это НЕ имя движка, а отметка разведки «опознать не вышло»
    # (так scout_apply пишет честную запись low/refuse). Печатать её как
    # движок значит выдумывать движок с таким именем.
    reason = one_line(recipe.get("broken_reason") or "", limit=160)
    if not engine or engine == "unknown":
        head = ("движок не опознан — ждёт разведки" if engine
                else "в рецепте не записан движок — ждёт разведки")
        return "scout", f"{head}: {reason}" if reason else head
    if reason:
        return "scout", f"движок {engine!r} не поддержан пробником: {reason}"
    return "scout", f"движок {engine!r} не поддержан пробником — ждёт разведки"


def split_queue(targets: list, recipes: dict) -> tuple[list, list]:
    """targets + рецепты -> (кого снимает таймер, кого пропускает и почему).

    Пропущенные — тройки (цель, причина, группа): группа говорит, ждёт ли
    строка человека (см. SKIP_LABELS), причина — что про объект известно.

    Пропускаются цели, которые детерминированный пробник не умеет в
    принципе: агрегаторы (их ветка — браузер агента), чужие движки вне v1 и
    объекты без модуля. Раньше таймер гнал в прогон ВСЕХ, и cli писал им
    честное «движок не поддержан пробником» — но этот пустой снимок
    становился последним и ЗАТИРАЛ в сводке живые цифры, которые агент до
    того снял браузером (18.08: так пропали Островок-квоты glamping_pod_nebom
    и hobbitland.ru). Пропуск сохраняет их последний хороший снимок, а его
    дата в колонке «Снят» показывает возраст честнее пустоты.

    Цели с рецептом ЗНАКОМОГО движка идут в прогон всегда, даже помеченные
    broken: их строка «нужна переразведка» — это и есть сигнал позвать агента.
    Порядок строк targets = важность, поэтому не сортируем.

    Новая партия целей приходит в targets.json РАНЬШЕ своих рецептов (самарские
    объекты тикета 12): такие строки очередь не ломают — они уходят в skipped с
    честной причиной, попадают в итог прогона и НЕ считаются «целью без
    данных», потому что пробник их не снимал.
    """
    queue: list[str] = []
    skipped: list[tuple[str, str, str]] = []
    for target in targets:
        if not isinstance(target, dict):
            continue
        username = target.get("username")
        if not username:
            continue
        recipe = recipes.get(username) or {}
        engine = recipe.get("engine") or ""
        if engine in probes.ENGINES:
            queue.append(str(username))
            continue
        group, reason = skip_reason(recipe)
        skipped.append((str(username), reason, group))
    return queue, skipped


def probe_args(queue: list) -> list[str]:
    """Имена целей -> аргументы cli.py: --probe на каждую, в порядке очереди."""
    args: list[str] = []
    for username in queue:
        args += ["--probe", username]
    return args


def is_production_root(snapshot_root) -> bool:
    """Боевой ли это каталог снапшотов (тот, что читает опубликованная сводка).

    Единственный признак «прогон настоящий»: по нему решается, писать ли
    версионируемую сводку и звонить ли владельцу. Сравниваем разрешённые пути,
    а не строки: '.../snapshots' и 'agent-runtime/../snapshots' — один каталог.
    """
    try:
        return (Path(snapshot_root).expanduser().resolve()
                == Path(core.DEFAULT_SNAPSHOT_ROOT).expanduser().resolve())
    except OSError:
        return False


def _under(path, base) -> bool:
    """Лежит ли путь внутри каталога base (по разрешённым путям, не строкам)."""
    try:
        return (Path(path).expanduser().resolve()
                .is_relative_to(Path(base).expanduser().resolve()))
    except (OSError, ValueError):
        return False


class RunPaths:
    """Куда прогон пишет. ОДИН узел, решающий «боевой прогон или песочница».

    Раньше это решение принимали три независимые функции (выход сводки,
    реестр рецептов, каталог копий), а четвёртый выход — слой сезонности —
    остался на боевом умолчании ключа. Итог: проверочный прогон в /tmp
    дописывал в ЖИВОЙ ряд сезонности выдуманный прогон, а после отравления
    last_run_id следующий настоящий прогон в слой уже не попадал вовсе
    (ledger не трогает прогоны старше last_run_id) — лечится только полной
    пересборкой. Класс существует, чтобы такого разъезда больше не было:
    решение одно, список выходов один (OUTPUTS), и каждый из них проверяется
    на выход за пределы песочницы (stray).

    Правило: явный ключ оператора сильнее умолчания — он вправе сказать,
    куда писать, и проверка изоляции его не оспаривает. Забытым может быть
    только УМОЛЧАНИЕ, и ловим мы ровно его.
    """

    #: Всё, во что прогон ПИШЕТ ФАЙЛАМИ. Новый выход добавляется сюда — и тем
    #: самым сам попадает под проверку изоляции песочницы (stray).
    OUTPUTS = ("snapshot_root", "recipes", "out_md", "out_html",
               "ledger_dir", "backup_dir")
    #: Выходы прогона НАРУЖУ: у них нет пути, поэтому stray их не видит, но
    #: решает про них тот же узел. True — канал работает, False — прогон
    #: проверочный и в канал не пишет.
    #:
    #: Волна 4 собрала список выходов из одних путей, и Run Log остался
    #: снаружи: проверочный прогон в /tmp писал в БОЕВУЮ вкладку «плановый
    #: прогон по расписанию, целей N». В журнале завода появлялся день,
    #: которого не было, — а по этим строкам сверяют правило №6 и считают
    #: пропуски таймера. Список каналов существует ровно затем, чтобы
    #: следующий выход без пути тоже попал под решение узла.
    CHANNELS = ("notify", "run_log")

    def __init__(self, snapshot_root, recipes=None, out=None, out_html=None,
                 ledger_dir=None, backup_dir=None, no_notify=False):
        self.snapshot_root = str(snapshot_root)
        self.production = is_production_root(snapshot_root)
        # Песочница — каталог РЯДОМ с каталогом снапшотов: там же лежат её
        # сводка, реестр, слой и копия.
        self.sandbox_base = Path(snapshot_root).parent
        self.explicit = {name for name, value in (
            ("recipes", recipes), ("out_md", out), ("out_html", out_html),
            ("ledger_dir", ledger_dir), ("backup_dir", backup_dir)) if value}
        near = self.sandbox_base
        prod = self.production
        self.recipes = sandbox_recipes(snapshot_root, recipes)
        if not prod and self.recipes == str(core.DEFAULT_RECIPES):
            # Копия реестра не снялась — sandbox_recipes уже сказал об этом в
            # stderr и сознательно пошёл боевым файлом. Проверке изоляции
            # обсуждать нечего: выбор сделан и назван вслух.
            self.explicit.add("recipes")
        self.out_md = str(out or (core.DEFAULT_OUT_MD if prod
                                  else near / "occupancy.md"))
        self.out_html = str(out_html or (core.DEFAULT_OUT_HTML if prod
                                         else near / "occupancy.html"))
        self.ledger_dir = str(ledger_dir or (ledger.DEFAULT_LEDGER_DIR if prod
                                             else near / "ledger"))
        self.backup_dir = str(backup_dir or (DEFAULT_BACKUP_DIR if prod
                                             else near / "backup"))
        # Небоевой каталог снапшотов = проверочный прогон = чат не трогаем.
        # Инцидент 04.09: проверки агентов слали владельцу тот же дайджест,
        # что и плановый прогон, и он получил поток одинаковых сообщений.
        self.notify = bool(prod and not no_notify)
        # Run Log — тот же канал наружу, и решается он тем же признаком. А вот
        # --no-notify на него НЕ распространяется: молчание чата и молчание
        # журнала — два разных решения. Оператор, попросивший не будить
        # владельца ночью, не отказывался от следа прогона (правило №6).
        self.run_log = bool(prod)

    @staticmethod
    def production_defaults() -> list:
        """Пути, которые читает и пишет БОЕВОЙ прогон (в порядке OUTPUTS)."""
        return [core.DEFAULT_SNAPSHOT_ROOT, core.DEFAULT_RECIPES,
                core.DEFAULT_OUT_MD, core.DEFAULT_OUT_HTML,
                ledger.DEFAULT_LEDGER_DIR, DEFAULT_BACKUP_DIR]

    def stray(self) -> list:
        """Выходы песочницы, ушедшие наружу. -> [(имя поля, путь)].

        Это не аккуратность, а защита ряда: единственный способ, каким
        проверочный прогон может испортить боевые данные, — выход, забытый на
        боевом умолчании. Явно названные оператором пути не проверяются: он
        вправе сказать, куда писать.

        Признака два, и второй важнее. Путь вне каталога песочницы — общий
        случай. Путь, РАВНЫЙ боевому умолчанию, ловится отдельно: снапшоты
        кладут и внутрь репозитория (`--snapshot-root ./снапшоты`), и тогда
        боевой agent-runtime формально лежит «в песочнице», а порча ряда от
        этого никуда не девается.
        """
        if self.production:
            return []
        boevye = set()
        for path in self.production_defaults():
            try:
                boevye.add(Path(path).expanduser().resolve())
            except OSError:
                continue
        out = []
        for name in self.OUTPUTS:
            if name in self.explicit:
                continue
            value = getattr(self, name)
            try:
                resolved = Path(value).expanduser().resolve()
            except OSError:
                resolved = None
            if resolved in boevye or not _under(value, self.sandbox_base):
                out.append((name, value))
        return out


def sandbox_recipes(snapshot_root, recipes=None) -> str:
    """Каким реестром рецептов работать. -> путь.

    Проверочный прогон получает КОПИЮ живого реестра рядом со своим каталогом
    снапшотов. Причина не в аккуратности: cli по ходу съёма пишет в реестр
    (cli._account_recipe -> save_recipes) статус broken, причину и счётчики
    отказов, а замок он берёт по пути СНАПШОТА — то есть прогон в другой
    каталог идёт параллельно плановому и правит тот же боевой файл. Копия
    делает изоляцию песочницы полной: проверка не может испортить знание,
    добытое разведкой.

    Явный --recipes сильнее: оператор сказал, каким реестром работать.
    """
    if recipes:
        return str(recipes)
    live = str(core.DEFAULT_RECIPES)
    if is_production_root(snapshot_root):
        return live
    copy = Path(snapshot_root).parent / "recipes.json"
    try:
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(live, copy)
    except OSError as e:
        # Копию не сняли — работаем боевым реестром только на чтение мы не
        # умеем, поэтому честно предупреждаем: пометки уйдут в живой файл.
        print(f"предупреждение: копия реестра рецептов не снята ({e}) — "
              f"прогон пойдёт по боевому {live}", file=sys.stderr)
        return live
    print(f"реестр рецептов: копия для проверочного прогона {copy}")
    return str(copy)


def print_sender(text: str) -> bool:
    """Транспорт небоевого прогона: показать сообщение и НЕ отправлять.

    Не молчание: текст — половина смысла проверочного прогона, и оператор
    должен видеть, что именно ушло бы владельцу. Возвращает False честно —
    сообщение не отправлено.
    """
    print("--- сообщение НЕ отправлено (прогон не боевой) ---")
    print(text)
    return False


def _run_description(targets_count: int, note: str = "") -> str:
    """Описание прогона для Run Log. Одно на оба логгера — боевой и печатающий.

    Своя копия текста в каждом из них разъехалась бы ровно так же, как
    разъезжались выходы прогона до RunPaths: оператор проверочного прогона
    должен читать в консоли ТУ ЖЕ строку, что ушла бы в боевую вкладку.
    """
    described = f"плановый прогон по расписанию, целей {targets_count}"
    if note:
        described += f"; {note}"
    return described


def print_logger(started_at: str, targets_count: int, outputs: list,
                 status: str = "success", errors=(), note: str = "") -> None:
    """Журнал небоевого прогона: показать строку и НЕ писать её в Run Log.

    Двойник print_sender для второго канала наружу. Печатаем ровно то, что
    ушло бы в боевую вкладку: оператор проверочного прогона обязан видеть и
    статус, и ошибки — иначе изоляция песочницы отнимает у него половину
    смысла проверки.
    """
    print("--- строка в Run Log НЕ записана (прогон не боевой) ---")
    print(_run_description(targets_count, note))
    print(f"статус {status}, начат {started_at}, выходы: "
          + ", ".join(str(o) for o in outputs))
    for line in errors:
        print(f"ошибка прогона: {line}")


def _moment_from_iso(text):
    try:
        return datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None


class RepeatGuard:
    """Сито повторов в чате: allow(text) — можно ли слать, mark(text) — ушло.

    Пункт 2 «постоянной починки» тикета 16. Инцидент 04.09 — это ОДИН И ТОТ ЖЕ
    текст в чате много раз за полчаса; окно меньше суток, поэтому плановый
    дайджест (он и так несёт дату в первой строке) проходит всегда.

    ДВА шага, а не один, потому что отметку обязана ставить ОТПРАВКА. Раньше
    её ставила проверка, и сценарий выходил неизбежный: 06:30 прогон молчит
    из-за стоп-крана (или из-за упавшей сети), но текст уже помечен
    отправленным — владелец снимает флаг, просит переснять, и тот же итог того
    же дня уходит в сито как «уже отправляли». Чат остаётся пустым, а проверка
    «дайджест снова идёт» показывает поломанного бота там, где всё исправно.

    Состояние — удобство, а не данные: недоступный файл не имеет права глушить
    единственную новость прогона, поэтому все сбои чтения/записи молчаливо
    означают «пропускаем».
    """

    def __init__(self, state_path=None, window_hours=DEFAULT_REPEAT_WINDOW_H,
                 now: datetime = None):
        self.path = Path(state_path or NOTIFY_STATE_FILE)
        self.now = now or datetime.now()
        self.window_hours = window_hours
        self.window = timedelta(hours=window_hours)
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — нет файла, битый json, нет прав
            state = {}
        self.state = state if isinstance(state, dict) else {}

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha1(str(text).encode("utf-8")).hexdigest()

    def allow(self, text: str) -> bool:
        """Слать ли этот текст. Ничего не пишет: см. докстринг класса."""
        seen = _moment_from_iso(self.state.get(self._key(text)))
        if seen is not None and self.now - seen < self.window:
            print(f"повтор не отправлен: тот же текст ушёл "
                  f"{seen:%d.%m %H:%M} (окно {self.window_hours:g} ч)")
            return False
        return True

    def mark(self, text: str) -> None:
        """Запомнить, что текст ДЕЙСТВИТЕЛЬНО ушёл в чат."""
        self.state[self._key(text)] = self.now.isoformat(timespec="seconds")
        # Чистим протухшее: файл состояния не должен расти вечно.
        for old_key, stamp in list(self.state.items()):
            moment = _moment_from_iso(stamp)
            if moment is None or self.now - moment > self.window * 4:
                self.state.pop(old_key, None)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.state, ensure_ascii=False),
                                 encoding="utf-8")
        except OSError as e:
            print(f"предупреждение: состояние повторов не записано ({e})",
                  file=sys.stderr)


def repeat_guard(state_path=None, window_hours=DEFAULT_REPEAT_WINDOW_H,
                 now: datetime = None) -> RepeatGuard:
    """Сито повторов для этого прогона. -> RepeatGuard."""
    return RepeatGuard(state_path, window_hours, now)


def planned_moment(planned_time: str, now: datetime = None) -> datetime:
    """Последнее плановое срабатывание таймера не позже now.

    Плановый момент нужен снимку (core.gap_days): ночь, снятая с опозданием,
    в рыночную медиану не идёт. systemd этот момент процессу не передаёт, а
    старт процесса плановым временем НЕ является — при Persistent=true догон
    после простоя машины начинается когда угодно. Поэтому считаем его сами из
    того же часа, что стоит в OnCalendar: прогон в 06:47 — плановое сегодня,
    прогон в 02:05 — плановое вчерашнее, и gap_days честно выйдет суткой.
    """
    now = now or datetime.now().astimezone()
    hours, _, minutes = planned_time.partition(":")
    slot = now.replace(hour=int(hours), minute=int(minutes), second=0,
                       microsecond=0)
    return slot if slot <= now else slot - timedelta(days=1)


def read_last_snapshot(root):
    """Последний каталог прогона с диска -> read_snapshot | None.

    Читаем ФАКТ с диска, а не то, что вернул пробник: доля целей без данных —
    это про записанные объекты, и судить о ней по коду возврата нельзя.
    """
    try:
        runs = core.list_snapshots(root)
        if not runs:
            print(f"предупреждение: в {root} нет ни одного снапшота — долю "
                  f"целей без данных считать не по чему", file=sys.stderr)
            return None
        return core.read_snapshot(Path(root) / runs[-1])
    except (core.SnapshotError, OSError) as e:
        print(f"предупреждение: снапшот прогона не прочитан ({e}) — доля "
              f"целей без данных не проверена", file=sys.stderr)
        return None


def _has_data(obj: dict) -> bool:
    """Есть ли в объекте хоть одна ЗНАЕМАЯ ночь (free/busy).

    Не «сетка непустая» и не статус. Горизонт, целиком переведённый в unknown
    (так делает ветка «весь горизонт» правила окна продаж, так же выглядит
    модуль, который ответил, но календаря не отдал), статус ok сохраняет и
    сетку имеет — и считался снятой целью. Итог прогона в чат говорил «без
    данных 0%», а сводка по тем же целям в каждой клетке печатала «нет
    данных»: две цифры об одном дне, и та, по которой срабатывает алерт, —
    неверная.

    Предикат намеренно ОДИН со сводкой (build_summary._has_known_cells): это
    то же решение «есть ли что показать», и своя копия здесь разъедется с
    ней ровно так же, как разъехались три копии «готова ли тема» в заводе.
    Ночи sales_not_open в знаменатель занятости не входят (occupancy_core,
    «Семантика метрик»), поэтому и здесь они данными не считаются.
    """
    return build_summary._has_known_cells(obj)


def run_report(snapshot, skipped=(), max_empty_share=DEFAULT_MAX_EMPTY_SHARE
               ) -> dict:
    """Итог прогона по записанному снапшоту: счётчики, доля пустых, алерт.

    Знаменатель — ПЛАН прогона из run.json, а не число записанных файлов.
    Разница видна ровно там, где она дороже всего: прогон, убитый после 3 целей
    из 21, по числу файлов рапортовал «без данных 0%». Цели плана, для которых
    файла нет, идут в «без данных» с честной причиной.

    Пропущенные до пробника (нет рецепта, агрегатор) в знаменатель не идут:
    пробник их не трогал, и считать их «без данных» значит поднимать алерт на
    каждой новой партии целей, у которой рецепты ещё не разведаны. Считаются
    они по ГРУППАМ (skipped_groups): «ждут разведки» — это работа, а «без
    онлайн-канала» — уже сделанный вывод, и одно число на всех звало человека
    туда, где звать некого.

    Порог сравнивается строго: ровно 20% при пороге 20% — ещё не авария.
    Пустой (или непрочитанный) снапшот — всегда авария: это отказ прогона
    целиком, и он обязан быть слышен кодом возврата, а не только в чате.
    """
    objects = (snapshot or {}).get("objects") or {}
    run = ((snapshot or {}).get("run") or {})
    late = list(run.get("targets_late") or [])
    ok = partial = 0
    empty_rows: list[tuple[str, str]] = []
    for username, obj in objects.items():
        if obj.get("status") == "insufficient_data" or not _has_data(obj):
            # Причину берём ту же, что печатает сводка: у снимка со статусом
            # ok и пустым горизонтом поле reason обычно пустое (движок
            # ответил, отказа не было), и «причина не записана» тут врёт.
            empty_rows.append((username,
                               build_summary._blank_snapshot_reason(obj)))
        elif obj.get("status") == "partial":
            partial += 1
        else:
            ok += 1
    planned = [str(name) for name in (run.get("targets") or [])]
    for username in planned:
        if username not in objects:
            empty_rows.append((username, "прогон оборван — объект не записан"))
    total = max(len(objects), len(planned))
    share = (len(empty_rows) / total) if total else 0.0
    alert = ""
    if not total:
        alert = ("снапшот прогона не прочитан — снятых целей в нём нет, "
                 "нужен агент")
    elif share > max_empty_share:
        alert = (f"без данных {share * 100:.0f}% целей при пороге "
                 f"{max_empty_share * 100:.0f}% — прогон требует агента")
    skipped = list(skipped)
    groups = {name: 0 for name in SKIP_ORDER}
    for row in skipped:
        # Строка пропуска — тройка (цель, причина, группа); двойку из старого
        # вызова считаем неразведанной, а не роняем прогон из-за формы.
        group = row[2] if len(row) > 2 else "scout"
        groups[group] = groups.get(group, 0) + 1
    return {"total": total, "ok": ok, "partial": partial,
            "empty": len(empty_rows), "empty_rows": empty_rows,
            "skipped": skipped, "skipped_groups": groups, "late": late,
            "empty_share": share, "threshold": max_empty_share, "alert": alert}


# ---------------------------------------------------------------------------
# Копия данных и хранение (тикет 16, пункты 1 и 4)
# ---------------------------------------------------------------------------

def _tar_dir(src: Path, archive: Path) -> None:
    """Каталог -> tar.gz. Пишем через .part: оборванный архив не выглядит
    готовым."""
    archive.parent.mkdir(parents=True, exist_ok=True)
    part = archive.with_name(archive.name + ".part")
    with tarfile.open(part, "w:gz") as tar:
        tar.add(src, arcname=src.name)
    part.replace(archive)


def _keep_newest(directory: Path, keep: int) -> None:
    """Оставить в каталоге копий только keep самых свежих архивов."""
    if keep <= 0:
        return
    archives = sorted(p for p in directory.glob("*.tar.gz") if p.is_file())
    for path in archives[:-keep]:
        try:
            path.unlink()
        except OSError as e:
            print(f"предупреждение: старая копия {path.name} не удалена ({e})",
                  file=sys.stderr)


def backup_run(snapshot_root, ledger_dir, backup_dir, now: datetime = None,
               keep_ledger: int = DEFAULT_LEDGER_COPIES) -> dict:
    """Вторая копия снапшотов и слоя сезонности. -> что скопировано.

    Копия ПОЦЕЛЬНАЯ: архив на каталог прогона, и уже скопированное второй раз
    не жмётся. Полный архив каждый день стоил бы сорока мегабайт в сутки при
    одном новом каталоге — то есть копия съела бы диск раньше, чем пригодилась.
    Слой сезонности мал (сотни килобайт) и копируется целиком, дневными
    архивами с ротацией.

    Копия — эксплуатация, а не данные: любой её сбой это предупреждение в
    stderr, а не падение прогона. Цифры к этому моменту уже сняты.
    """
    now = now or datetime.now()
    stats = {"snapshots": [], "ledger": ""}
    root = Path(snapshot_root)
    if not root.is_dir():
        print(f"предупреждение: копия не снята — каталога снапшотов "
              f"{snapshot_root} нет", file=sys.stderr)
        return stats
    try:
        for name in core.list_snapshots(root):
            archive = Path(backup_dir) / "snapshots" / f"{name}.tar.gz"
            if archive.is_file():
                continue
            _tar_dir(root / name, archive)
            stats["snapshots"].append(name)
        ledger_path = Path(ledger_dir)
        if ledger_path.is_dir():
            archive = (Path(backup_dir) / "ledger"
                       / f"ledger-{now:%Y-%m-%d}.tar.gz")
            _tar_dir(ledger_path, archive)
            stats["ledger"] = str(archive)
            _keep_newest(archive.parent, keep_ledger)
    except (OSError, tarfile.TarError) as e:
        print(f"предупреждение: копия снята не полностью ({e})",
              file=sys.stderr)
    if stats["snapshots"] or stats["ledger"]:
        print(f"копия: прогонов +{len(stats['snapshots'])}"
              + (", слой сезонности скопирован" if stats["ledger"] else ""))
    return stats


def restore_backup(archive, dest) -> Path:
    """Развернуть архив копии в каталог. -> каталог, готовый для чтения ядром.

    Копия, которую нельзя развернуть, копией не является: приёмка тикета 16
    требует именно проверки восстановлением. filter="data" — не доверяем путям
    внутри архива (распаковка вне каталога назначения запрещена).
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        tar.extractall(dest, filter="data")
    return dest


def archive_old_snapshots(root, older_than_days=DEFAULT_ARCHIVE_AFTER_DAYS,
                          now: datetime = None) -> list[str]:
    """Снапшоты старше срока — в gzip НА МЕСТЕ. -> имена заархивированных.

    Политика тикета 16 п.4: сырые снимки держим всегда (правило №1), но не
    россыпью — 53 файла на прогон растут линейно. Архив лежит рядом с
    каталогами и НЕ виден core.list_snapshots (тот требует run.json в
    каталоге): сводка читает свежее окно, а длинный ряд живёт в ledger.
    Оригинал удаляется только после того, как архив открыт и прочитан.
    0 в older_than_days — политика выключена.

    ВАЖНО для пересборки слоя сезонности: ledger.rebuild читает КАТАЛОГИ, и
    сжатые прогоны для него невидимы. Перед полной пересборкой архивы надо
    развернуть обратно на место:
        run_scheduled.py --restore <root>/<run_id>.tar.gz --restore-into <root>
    (архив несёт внутри сам каталог прогона, поэтому разворачивается в корень
    снапшотов как был). Инкрементальной дозаписи это не мешает: она берёт
    только новые прогоны.
    """
    if not older_than_days:
        return []
    now = now or datetime.now()
    edge = (now - timedelta(days=older_than_days)).date()
    archived: list[str] = []
    for name in core.list_snapshots(root):
        try:
            when = date.fromisoformat(name[:10])
        except ValueError:
            continue  # имя не по формату YYYY-MM-DD-HHMM — не наше дело
        if when >= edge:
            continue
        source = Path(root) / name
        archive = Path(root) / f"{name}.tar.gz"
        try:
            _tar_dir(source, archive)
            with tarfile.open(archive) as tar:
                tar.getmembers()
            shutil.rmtree(source)
        except (OSError, tarfile.TarError) as e:
            print(f"предупреждение: снапшот {name} не заархивирован ({e})",
                  file=sys.stderr)
            continue
        archived.append(name)
    if archived:
        print(f"хранение: сжато прогонов {len(archived)} "
              f"(старше {older_than_days} суток)")
    return archived


def append_ledger(snapshot_root, out_dir, appender=ledger.append) -> None:
    """Дописать слой сезонности за этот прогон (тикет 13).

    Слой ПРОИЗВОДНЫЙ: источник истины — снапшоты, и любой его сбой лечится
    пересбором (ledger.rebuild). Поэтому он не стоит дня данных и роняет
    прогон не больше, чем недоступный чат.
    """
    try:
        stats = appender(snapshot_root, out_dir)
    except Exception as e:  # noqa: BLE001 — производный слой не роняет прогон
        print(f"предупреждение: слой сезонности не дописан ({e})",
              file=sys.stderr)
        return
    if isinstance(stats, dict):
        print(f"слой сезонности: новых прогонов "
              f"{len(stats.get('new_runs') or [])}, "
              f"ночей +{stats.get('nights_rows_added', 0)}, "
              f"темпа +{stats.get('pace_rows_added', 0)}")


# Настоящий боевой каталог снапшотов ЭТОГО репозитория — по положению файла,
# а не по константе ядра. Тесты планового прогона подменяют
# core.DEFAULT_SNAPSHOT_ROOT на tmp, и is_production_root честно считает tmp
# «боем»: 05.09 00:52 из-под pytest настоящий build_units_report собрал отчёт
# по пустому tmp-снапшоту и записал нули в docs/research/.../units-2026-09-05.md
# и в agent-runtime/.../out. Отчёт пишет ВЕРСИОНИРУЕМЫЕ файлы, поэтому его
# предохранитель обязан не зависеть от того, что тест сделал с константами.
REAL_SNAPSHOT_ROOT = (Path(__file__).resolve().parents[1] / "agent-runtime"
                      / "research" / "glamping" / "occupancy" / "snapshots")


def is_real_repo_root(snapshot_root) -> bool:
    """Тот ли это каталог снапшотов, что лежит в самом репозитории."""
    try:
        return Path(snapshot_root).expanduser().resolve() == REAL_SNAPSHOT_ROOT.resolve()
    except OSError:
        return False


def rebuild_units_report(paths, today=None,
                         report=build_units_report.main,
                         page=build_units_page.main) -> None:
    """Пересобрать поюнитный отчёт (xlsx + md) и html его страницы (тикет 14).

    Только в БОЕВОМ прогоне: отчёт пишет версионируемый md в docs/ и файлы
    в agent-runtime/.../out — из песочницы им выходить нельзя (тот же принцип,
    что у сводки в RunPaths). Шаг производный и best-effort: цифры уже сняты,
    и падение рендера не стоит дня данных. Публикует страницу не он — у
    таймера нет инструмента публикации, это делает cf-occupancy-publish
    (occupancy/publish_pages.sh) после прогона.
    """
    if not is_real_repo_root(paths.snapshot_root):
        print("поюнитный отчёт: песочница — не пересобираем", file=sys.stderr)
        return
    argv = ["--today", today] if today else []
    try:
        report(argv)
        page([])
    except Exception as e:  # noqa: BLE001 — производный слой не роняет прогон
        print(f"предупреждение: поюнитный отчёт не пересобран ({e})",
              file=sys.stderr)


def send_telegram(text: str) -> bool:
    """Отправить сообщение оператору через общий транспорт завода.

    Мягкая деградация по образцу самого cf.notify: телеграм не настроен или
    сеть легла — предупреждение в stderr, прогон продолжается. Цифры уже
    сняты, и терять их из-за чата нельзя.

    Причину молчания называем вслух в журнале юнита: решение «молчать» живёт
    в cf.notify (стоп-кран со сроком), и без этой строки заглушённый прогон
    выглядит в journald ровно как исправный.
    """
    try:
        from cf.config import load_config
        from cf.notify import mute_note, notify_telegram
        # Путь от корня репо, а не от текущего каталога (ревью 14.09.2026): запуск
        # не из корня молча глушил дайджест, а Run Log винил в этом чат. CF_CONFIG
        # по-прежнему главнее.
        config_path = os.environ.get("CF_CONFIG") or REPO_ROOT / "cf.config.json"
        ok = bool(notify_telegram(text, load_config(config_path)))
        if not ok:
            print("предупреждение: уведомление не ушло — "
                  + (mute_note()
                     or "см. журнал: токен, chat_id или сеть"),
                  file=sys.stderr)
        return ok
    except Exception as e:  # noqa: BLE001 — уведомление не роняет прогон
        print(f"предупреждение: уведомление не ушло ({e})", file=sys.stderr)
        return False


def _run_day(run_id):
    """Каталог прогона -> его день | None (имя не по формату — не выдумываем)."""
    try:
        return date.fromisoformat(str(run_id)[:10])
    except (TypeError, ValueError):
        return None


def notify_targets(targets: list, months: list, sender=send_telegram,
                   today=None, report=None, snapshot_root=None,
                   sleeper=time.sleep, pause_sec=TG_PAUSE_SEC,
                   guard=None) -> tuple:
    """Разослать итог прогона и сводки по целям с notify.

    -> (сколько ушло, сколько было в очереди, сколько снято ситом повторов).
    Второе число нужно журналу: без него молчание чата неотличимо от
    исправной работы. Третье отделяет от молчания чата работу СОБСТВЕННОГО
    сита: сообщение, снятое как повтор, до транспорта не доходило, и винить
    в его отсутствии чат — значит звать человека чинить исправное.

    Одна очередь отправки на всё сообщение прогона: сначала итог (он и есть
    новость при 53 целях), потом штучные сводки наблюдаемых объектов, между
    сообщениями — пауза. Раньше сообщение уходило на КАЖДУЮ цель без пауз, и
    при большой очереди целей это шторм, в котором тонет единственная строка,
    требующая человека.

    Снимки читаются с диска ПОСЛЕ прогона: берутся последний снимок цели и
    предыдущий её же — те самые два, по которым считается динамика в сводке,
    и ровно столько, сколько просит depth (иначе разбирается ВСЯ история: на
    38 прогонах это 2,55 с и 465 объекто-снимков ради двух нужных).

    Снимок датируется ДНЁМ СВОЕГО КАТАЛОГА, а не днём прогона: цель, которую
    таймер пропускает (агрегатор, неразведанный рецепт), сегодняшнего файла
    не получает, и её трёхнедельная цифра уходила владельцу под сегодняшней
    датой со словами «за сутки календарь не двигался».

    guard — сито повторов (RepeatGuard): не пропущенное сообщение молча
    выпадает из очереди, пауза считается по фактическим отправкам, а отметка
    «ушло» ставится ТОЛЬКО после успеха отправки.
    """
    today = today or date.today()
    queue: list[str] = []
    if report:
        head = tg_digest.run_summary(report, today)
        if head:
            queue.append(head)
    watched = [t for t in targets
               if isinstance(t, dict) and tg_digest.wants_notify(t)]
    if watched:
        history = build_summary.load_history(
            snapshot_root or core.DEFAULT_SNAPSHOT_ROOT,
            depth=NOTIFY_HISTORY_DEPTH)
        for target in watched:
            entries = history.get(target.get("username")) or []
            if not entries:
                continue
            run_id, obj = entries[-1]
            prev_id, prev = (entries[-2] if len(entries) > 1 else (None, None))
            taken, prev_taken = _run_day(run_id), _run_day(prev_id)
            gap = ((taken - prev_taken).days
                   if taken and prev_taken else None)
            text = tg_digest.digest(target, obj, prev, months, today,
                                    taken=taken, gap_days=gap)
            if text:
                queue.append(text)
    sent = attempts = muffled = 0
    for text in queue:
        if guard is not None and not guard.allow(text):
            muffled += 1
            continue
        if attempts:
            sleeper(pause_sec)
        attempts += 1
        if sender(text):
            sent += 1
            if guard is not None:
                guard.mark(text)
    if queue:
        print(_notify_note(sent, len(queue), muffled))
    return sent, len(queue), muffled


def _sent_counts(result) -> tuple:
    """Ответ уведомителя -> (ушло, было в очереди, схлопнуто ситом повторов).

    Третье число отделяет молчание ЧАТА от работы собственного сита: без него
    прогон, у которого сито сняло единственное сообщение как повтор, писал в
    Run Log «ни одно уведомление не ушло: чат заглушён или недоступен» — и
    отправлял человека чинить исправный транспорт.

    Уведомитель — подменяемая граница (проверочный прогон, тесты), поэтому
    терпим и прежние формы ответа (пара, число), и её отсутствие: строка
    журнала не стоит падения прогона.
    """
    if isinstance(result, tuple) and len(result) in (2, 3):
        try:
            values = [int(x or 0) for x in result]
        except (TypeError, ValueError):
            return 0, 0, 0
        return tuple(values) if len(values) == 3 else (*values, 0)
    try:
        count = int(result or 0)
    except (TypeError, ValueError):
        return 0, 0, 0
    return count, count, 0


def _notify_note(sent: int, queued: int, muffled: int = 0) -> str:
    """Приписка к описанию прогона в Run Log: что стало с сообщениями."""
    note = f"уведомлений отправлено {sent} из {queued}"
    if muffled:
        note += f", повторов схлопнуто {muffled}"
    return note


def log_run(started_at: str, targets_count: int, outputs: list[str],
            status: str = "success", errors=(), note: str = "") -> None:
    """Строка в CF Run Log. Сбой журнала прогон НЕ роняет: цифры уже сняты.

    Статус деградировавшего прогона — insufficient_data, а не failed: данные
    на диске есть, просто их меньше обещанного (правило №2 завода). failed
    остаётся за настоящим отказом: пробники оборваны, сводка не собрана.

    note — короткая приписка к описанию прогона (сейчас: сколько уведомлений
    ушло из скольких). Она идёт именно в описание, а не в статус: молчащий
    чат ничего не говорит о качестве СНЯТЫХ данных.
    """
    described = _run_description(targets_count, note)
    cmd = [str(CF_PYTHON), "-m", "cf", "log-run",
           "--agent", "glamping-occupancy",
           "--status", status,
           "--started-at", started_at,
           "--input", described,
           "--outputs", *outputs]
    if errors:
        cmd += ["--errors", *errors]
    try:
        subprocess.run(cmd, cwd=str(REPO_ROOT), check=True,
                       timeout=LOG_RUN_TIMEOUT_SEC,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"предупреждение: Run Log не записан ({e})", file=sys.stderr)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_scheduled",
        description="Плановый прогон загрузки: пробники по всем целям, "
                    "один снапшот, пересборка сводки, строка в Run Log.")
    parser.add_argument("--targets", default=str(core.DEFAULT_TARGETS))
    parser.add_argument("--recipes", default=None,
                        help="реестр рецептов (дефолт: боевой у боевого "
                             "--snapshot-root, иначе его копия рядом с "
                             "песочницей — cli пишет в реестр по ходу съёма)")
    parser.add_argument("--snapshot-root",
                        default=str(core.DEFAULT_SNAPSHOT_ROOT),
                        help="каталог снапшотов: сюда пишет пробник, отсюда "
                             "читают сводка и слой сезонности")
    parser.add_argument("--ledger-dir", default=None,
                        help="каталог производного слоя сезонности (дефолт: "
                             "боевой только у боевого --snapshot-root, иначе "
                             "рядом с ним — см. --out)")
    parser.add_argument("--no-inventory", action="store_true",
                        help="не снимать фонд типов (быстро, но занятость "
                             "там, где домиков в типе несколько, занижена)")
    parser.add_argument("--inventory-days", type=int,
                        default=core.DEFAULT_INVENTORY_DAYS,
                        help="на сколько ближних ночей спрашивать ФОНД типа: "
                             "главный рычаг цены прогона")
    parser.add_argument("--deadline-min", type=float,
                        default=DEFAULT_DEADLINE_MIN,
                        help="дедлайн прогона в минутах: цели, до которых "
                             "очередь не дошла, получают честную причину, а "
                             "прогон закрывается штатно")
    parser.add_argument("--planned-at", default=None,
                        help="плановый момент прогона ISO8601 (дефолт: "
                             "последнее срабатывание --planned-time)")
    parser.add_argument("--planned-time", default=DEFAULT_PLANNED_TIME,
                        help="час таймера HH:MM — тот же, что в OnCalendar")
    parser.add_argument("--max-empty-share", type=float,
                        default=DEFAULT_MAX_EMPTY_SHARE,
                        help="доля снятых целей без данных, выше которой "
                             "прогон считается аварийным (ненулевой код и "
                             "алерт в чат)")
    parser.add_argument("--months", default=None,
                        help="месяцы для сводки уведомления через запятую "
                             "(дефолт: скользящее окно от текущего месяца)")
    parser.add_argument("--host-workers", type=int,
                        default=cli.DEFAULT_HOST_WORKERS,
                        help="сколько хостов снимается одновременно; 1 — "
                             "прежний последовательный прогон (рычаг отката)")
    parser.add_argument("--out", default=None,
                        help="куда писать markdown-сводку (дефолт: боевой "
                             "файл только у боевого --snapshot-root, иначе "
                             "рядом с ним)")
    parser.add_argument("--out-html", default=None,
                        help="куда писать html сводки (см. --out)")
    parser.add_argument("--no-notify", action="store_true",
                        help="не писать в телеграм: текст печатается в "
                             "консоль. Включается САМ, когда --snapshot-root "
                             "не боевой — проверочный прогон не имеет права "
                             "писать владельцу")
    parser.add_argument("--repeat-window-h", type=float,
                        default=DEFAULT_REPEAT_WINDOW_H,
                        help="окно схлопывания повторов в чате, часы: тот же "
                             "текст в окне второй раз не уходит")
    parser.add_argument("--backup-dir", default=None,
                        help="второе место для копии снапшотов и слоя "
                             "сезонности")
    parser.add_argument("--no-backup", action="store_true",
                        help="не снимать копию данных этого прогона")
    parser.add_argument("--archive-after-days", type=int,
                        default=DEFAULT_ARCHIVE_AFTER_DAYS,
                        help="снапшоты старше стольких суток сжимаются в gzip "
                             "на месте; 0 — не сжимать")
    parser.add_argument("--restore", default=None, metavar="ARCHIVE",
                        help="развернуть архив копии в --restore-into и выйти "
                             "(проверка восстановления)")
    parser.add_argument("--restore-into", default=None, metavar="DIR",
                        help="каталог, куда разворачивать --restore")
    parser.add_argument("--dry-run", action="store_true",
                        help="только показать очередь и командную строку "
                             "прогона, ничего не запуская")
    return parser


def main(argv=None, *, probe=cli.main, summarise=build_summary.main,
         logger=None, notifier=notify_targets,
         ledger_append=append_ledger,
         units_report=rebuild_units_report) -> int:
    """Плановый прогон целиком. -> код возврата (0 | 1 | 2).

    logger=None значит «решает узел RunPaths»: боевой прогон пишет строку в
    CF Run Log, проверочный печатает её в консоль. Явный логгер (тесты,
    обёртки) сильнее — он и есть шов подмены.
    """
    args = _build_parser().parse_args(argv)

    if args.restore:
        if not args.restore_into:
            print("ошибка: --restore нужен --restore-into (куда "
                  "разворачивать)", file=sys.stderr)
            return 2
        try:
            dest = restore_backup(args.restore, args.restore_into)
        except (OSError, tarfile.TarError) as e:
            print(f"ошибка: копия не развёрнута ({e})", file=sys.stderr)
            return 2
        runs = core.list_snapshots(dest)
        print(f"копия развёрнута: {dest}, прогонов {len(runs)}"
              + (f" ({runs[0]}…{runs[-1]})" if runs else ""))
        return 0

    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        planned_at = (args.planned_at
                      or planned_moment(args.planned_time).isoformat(
                          timespec="seconds"))
    except ValueError as e:
        print(f"ошибка: --planned-time {args.planned_time!r} не HH:MM ({e})",
              file=sys.stderr)
        return 2
    months = ([m.strip() for m in args.months.split(",") if m.strip()]
              if args.months else core.summary_months(core.today()))
    paths = RunPaths(args.snapshot_root, recipes=args.recipes, out=args.out,
                     out_html=args.out_html, ledger_dir=args.ledger_dir,
                     backup_dir=args.backup_dir, no_notify=args.no_notify)
    stray = paths.stray()
    if stray:
        # Не предупреждение, а отказ стартовать: единственный способ, каким
        # проверочный прогон портит боевые данные, — выход, оставшийся на
        # боевом умолчании (так волна 3 дописала в живой слой сезонности
        # выдуманный прогон). Молча увести его некуда: это ошибка кода.
        for name, path in stray:
            print(f"ошибка: проверочный прогон писал бы мимо песочницы: "
                  f"{name} -> {path}", file=sys.stderr)
        return 2
    if logger is None:
        # Куда идёт строка журнала, решает ТОТ ЖЕ узел, что и файловые выходы.
        logger = log_run if paths.run_log else print_logger

    notify_off = not paths.notify
    sender = print_sender if notify_off else send_telegram
    # Сито повторов только на боевом пути: в консоли повтор не мешает никому,
    # а состояние отправок от проверочных прогонов врало бы. Заодно это и
    # ответ про боевой файл состояния (NOTIFY_STATE_FILE): у проверочного
    # прогона сита нет вовсе, поэтому трогать его нечему.
    guard = None if (notify_off or args.dry_run) else repeat_guard(
        window_hours=args.repeat_window_h)
    # Заполняются ниже; аварийный выход читает их АКТУАЛЬНЫМИ (замыкание
    # смотрит на переменные main в момент вызова, а не в момент объявления).
    skipped: list = []
    count = 0

    def tell(targets_to_digest, report) -> tuple:
        """Сообщения в чат. Своё исключение НЕ роняет прогон.

        Единственный шаг хвоста, который раньше вызывался голым: сборка
        ТЕКСТА (в отличие от транспорта) ничем не защищена, и битый каталог
        снапшота — core.SnapshotError из load_history — уносил вместе с чатом
        и строку в Run Log.
        """
        try:
            result = notifier(targets_to_digest, months, report=report,
                              snapshot_root=paths.snapshot_root,
                              sender=sender, guard=guard)
        except Exception as e:  # noqa: BLE001 — чат не стоит дня данных
            print(f"предупреждение: уведомления не отправлены ({e})",
                  file=sys.stderr)
            return 0, 0, 0
        return _sent_counts(result)

    def stop(reason: str) -> int:
        """Аварийный выход прогона: сказать человеку и записать в журнал.

        Правило №6 завода нарушалось ровно там, где след нужнее всего: при
        занятой блокировке реестра, битом recipes.json, пустой очереди и
        исключении в потоке пробника прогон выходил до логгера и до
        уведомителя, а единственным сигналом оставался systemd-алерт — тем же
        транспортом, который может быть заглушён стоп-краном. Битый реестр и
        пустая очередь — это потеря ВСЕГО дня по ВСЕМ целям, то есть самая
        тяжёлая авария, и молчала именно она.

        Штучные сводки целей не шлём: снапшот оборван, и читать по нему
        историю значит выдавать вчерашние цифры за сегодняшние.
        """
        print(f"ошибка: {reason}", file=sys.stderr)
        if args.dry_run:
            # --dry-run прогоном не является: он показывает, что БУДЕТ, и не
            # имеет права оставлять след ни в журнале, ни в чате.
            return 2
        broken = {"total": 0, "ok": 0, "partial": 0, "empty": 0,
                  "empty_rows": [], "skipped": list(skipped),
                  "skipped_groups": {name: sum(
                      1 for row in skipped
                      if (row[2] if len(row) > 2 else "scout") == name)
                      for name in SKIP_ORDER},
                  "late": [],
                  "empty_share": 0.0, "threshold": args.max_empty_share,
                  "alert": reason}
        sent, queued, muffled = tell([], broken)
        logger(started_at, count, [paths.snapshot_root], status="failed",
               errors=[reason], note=_notify_note(sent, queued, muffled))
        return 2

    try:
        targets = core.load_targets(args.targets)
        recipes = core.load_recipes(paths.recipes)
    except core.RegistryError as e:
        return stop(f"реестр прогона не прочитан: {e}")
    queue, skipped = split_queue(targets, recipes)
    if not queue:
        return stop(f"в {args.targets} нет ни одной цели, которую умеет снять "
                    f"пробник — снимать нечего, день слежки потерян весь")
    count = len(queue)
    for username, why, group in skipped:
        # Слова «работа агента» стоят только там, где работа и правда есть:
        # у строки «без онлайн-канала» вывод уже сделан и записан.
        role = ("работа агента" if group == "scout"
                else "ветка агента" if group == "aggregator"
                else "не работа агента")
        print(f"пропущено ({role}): {username} — {why}")

    probe_argv = probe_args(queue) + [
        "--recipes", paths.recipes,
        "--snapshot-root", paths.snapshot_root,
        "--planned-at", planned_at,
        "--deadline-min", f"{args.deadline_min:g}",
        "--inventory-days", str(args.inventory_days),
        "--host-workers", str(args.host_workers),
    ] + (["--no-inventory"] if args.no_inventory else [])
    summary_argv = ["--targets", args.targets,
                    "--recipes", paths.recipes,
                    "--snapshot-root", paths.snapshot_root,
                    "--out", paths.out_md, "--out-html", paths.out_html]
    if args.dry_run:
        # Показываем ВСЮ строку вызова: главный рычаг цены прогона
        # (--inventory-days) и дедлайн из неё раньше выпадали, а именно за ними
        # оператор сюда и приходит.
        print(f"целей в очереди: {count}")
        print("пробники: " + " ".join(probe_argv))
        print("сводка: " + " ".join(summary_argv))
        # Слой и копия печатаются наравне с остальным: это ВЫХОДЫ прогона, и
        # оператор пришёл сюда именно за ответом «куда всё ляжет».
        print(f"слой сезонности: {paths.ledger_dir}")
        print("копия: " + ("нет (--no-backup)" if args.no_backup
                           else paths.backup_dir))
        print("телеграм: " + ("нет (прогон не боевой)" if notify_off
                              else "боевой чат владельца"))
        # Строка журнала — такой же выход прогона: оператор пришёл сюда за
        # ответом «куда всё ляжет», и Run Log в этом ответе молчал.
        print("Run Log: " + ("боевая вкладка CF Run Log" if paths.run_log
                             else "нет (прогон не боевой)"))
        return 0

    code = probe(probe_argv)
    if code >= 2:
        return stop(f"прогон пробников вернул {code} — снятое лежит в "
                    f"снапшоте, но сводку по обрыву не пересобираем")
    # Код 1 — «часть целей не успела к дедлайну»: снапшот валиден, данные на
    # диске. Гасить на этом сводку, дайджест и Run Log значит терять день
    # НАРУЖУ ровно там, где он на диске сохранён.
    late_run = code != 0
    if late_run:
        print("предупреждение: часть целей не успела к дедлайну — прогон "
              "продолжается, сводка и журнал выйдут", file=sys.stderr)
    code = summarise(summary_argv)
    if code != 0:
        return stop(f"сборка сводки вернула {code} — данные прогона на диске "
                    f"есть, но наружу они не вышли")
    ledger_append(paths.snapshot_root, paths.ledger_dir)
    units_report(paths)
    if not args.no_backup:
        backup_run(paths.snapshot_root, paths.ledger_dir, paths.backup_dir)
        archive_old_snapshots(paths.snapshot_root, args.archive_after_days)

    report = run_report(read_last_snapshot(paths.snapshot_root), skipped,
                        args.max_empty_share)
    if report["alert"]:
        print(f"ошибка: {report['alert']}", file=sys.stderr)
    sent, queued, muffled = tell(targets, report)

    # Статус — только про ДАННЫЕ (правило №2), а молчание чата — отдельной
    # строкой ошибок: оно ничего не говорит о качестве снятого, но человек
    # обязан отличать «всё тихо, потому что тихо» от «всё тихо, потому что
    # заглушено». Кодом возврата этого не сказать: ненулевой код зовёт
    # OnFailure=cf-alert@, а тот идёт тем же самым молчащим транспортом.
    data_errors = []
    if late_run:
        data_errors.append("часть целей не успела к дедлайну прогона")
    if report["alert"]:
        data_errors.append(report["alert"])
    errors = list(data_errors)
    # Обвинять чат можно только в том, что он молчал. Сообщение, снятое
    # СОБСТВЕННЫМ ситом повторов, до транспорта не доходило вовсе, и строка
    # «чат заглушён или недоступен» отправляла человека чинить исправное.
    handed_over = queued - muffled
    if handed_over > 0 and not sent:
        errors.append(f"ни одно уведомление не ушло ({handed_over} "
                      f"в очереди): чат заглушён или недоступен")
    logger(started_at, count, [paths.snapshot_root, paths.out_md],
           status="insufficient_data" if data_errors else "success",
           errors=errors, note=_notify_note(sent, queued, muffled))
    # Ненулевой код = OnFailure=cf-alert@ = сообщение владельцу. Опоздавшая
    # цель его не стоит: день на диске есть, причина у цели записана, и в
    # дайджесте стоит строка «не дошла очередь». Иначе один медленный хост
    # роняет юнит каждые сутки и обесценивает сигнал, ради которого алерт и
    # заводился.
    return 1 if report["alert"] else 0


if __name__ == "__main__":
    sys.exit(main())
