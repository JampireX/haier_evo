
import logging


# websocket-client logs every reconnect and callback error at ERROR. Its errors reach the
# integration through on_error (Haier._on_error), which logs them once and at a fitting level.
logging.getLogger("websocket").setLevel(logging.CRITICAL)
_LOGGER = logging.getLogger("custom_components.haier_evo")
_LOGGER.setLevel(logging.INFO)
# Временно (бета): все debug-сообщения выводятся как INFO, чтобы диагностировать
# задержку исполнения команды после простоя без включения режима отладки в HA.
_LOGGER.debug = _LOGGER.info
