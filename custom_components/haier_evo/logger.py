
import logging


logging.getLogger("websocket").setLevel(logging.CRITICAL)
_LOGGER = logging.getLogger("custom_components.haier_evo")
_LOGGER.setLevel(logging.INFO)
# Временно (бета): все debug-сообщения выводятся как INFO, чтобы диагностировать
# задержку исполнения команды после простоя без включения режима отладки в HA.
_LOGGER.debug = _LOGGER.info
