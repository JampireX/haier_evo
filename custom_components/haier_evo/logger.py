
import logging


# websocket-client logs every reconnect and callback error at ERROR. Its errors reach the
# integration through on_error (Haier._on_error), which logs them once and at a fitting level.
logging.getLogger("websocket").setLevel(logging.CRITICAL)
_LOGGER = logging.getLogger("custom_components.haier_evo")
# INFO, unless a level is configured already (logger: logs: in configuration.yaml). Debug
# messages show with the integration's debug logging on. (The 0.3.26 betas logged them as
# INFO to diagnose the command delay after idle: some 900 lines an hour.)
if _LOGGER.level == logging.NOTSET:
    _LOGGER.setLevel(logging.INFO)
