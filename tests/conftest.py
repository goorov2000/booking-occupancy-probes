"""Подключение каталога occupancy/ (код модуля) в sys.path для импорта ядра и CLI."""
import sys
import socket
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "occupancy"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Витрина: внешний пакет `cf` (конфиг, транспорт телеграма) в репозиторий не входит.
# Несколько тестов подменяют `cf.notify.notify_telegram`; для них подключается
# заглушка с тем же интерфейсом — только если настоящего пакета нет.
import importlib.util
if importlib.util.find_spec("cf") is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "_stubs"))


def _empty_config(factory):
    path = factory.getbasetemp() / "cf.config.json"
    if not path.exists():
        path.write_text("{}", encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def offline_only(monkeypatch, tmp_path_factory):
    """Тесты скилла всегда на фикстурах, включая отдельный запуск этой папки."""
    def blocked(*args, **kwargs):
        raise RuntimeError("occupancy tests are offline: supply a fake transport")

    monkeypatch.setenv("CF_NOTIFY_OFF", "1")
    # Настоящий cf.config.json (с путями к боевым секретам) тестам не нужен:
    # иначе результат зависел от каталога запуска (ревью 14.09.2026).
    monkeypatch.setenv("CF_CONFIG", str(_empty_config(tmp_path_factory)))
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
