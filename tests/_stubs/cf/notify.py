"""Заглушка `cf.notify`: ненастроенный транспорт телеграма.

Интерфейс повторяет то, что использует `occupancy/run_scheduled.py` и тесты:
`notify_telegram(text, config=None, client=None) -> bool`, `mute_note() -> str | None`,
`fit(text) -> str` (обрезка до предела Bot API), `TELEGRAM_LIMIT`.
"""
TELEGRAM_LIMIT = 4096
_CUT_NOTE = "\n\n…остальное — в журнале прогона."


def fit(text):
    """Обрезать до предела Bot API, сказав человеку, где лежит остальное."""
    text = str(text)
    if len(text) <= TELEGRAM_LIMIT:
        return text
    return text[:TELEGRAM_LIMIT - len(_CUT_NOTE)] + _CUT_NOTE


def mute_note():
    """Причина молчания транспорта; у заглушки её нет."""
    return None


def telegram_configured(config=None) -> bool:
    return False


def notify_telegram(text, config=None, client=None) -> bool:
    """Ничего не отправляет: транспорт не настроен."""
    return False
