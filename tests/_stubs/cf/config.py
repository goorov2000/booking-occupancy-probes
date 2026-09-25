"""Заглушка `cf.config`: читает JSON-конфиг, если он есть, иначе пустой словарь."""
import json
from pathlib import Path


def load_config(path=None) -> dict:
    if not path:
        return {}
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}
