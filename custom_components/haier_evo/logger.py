
import logging
import logging.handlers


logging.getLogger("websocket").setLevel(logging.CRITICAL)
_LOGGER = logging.getLogger("custom_components.haier_evo")
_LOGGER.setLevel(logging.INFO)
# Временно (бета): все debug-сообщения выводятся как INFO, чтобы диагностировать
# задержку исполнения команды после простоя без включения режима отладки в HA.
_LOGGER.debug = _LOGGER.info


def setup_file_logging(path: str) -> None:
    """Собственный файл-лог интеграции (<config>/haier_evo.log).

    Нужен, потому что home-assistant.log у части инсталляций не пишется,
    а читать лог удобно напрямую по SMB без режима отладки.
    """
    if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in _LOGGER.handlers):
        return
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=10_000_000, backupCount=1, encoding="utf-8", delay=True,
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s (%(threadName)s) %(message)s"
    ))
    _LOGGER.addHandler(handler)
    _LOGGER.info(f"File logging enabled: {path}")
