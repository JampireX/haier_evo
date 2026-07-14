DOMAIN = "haier_evo"
COMMON_LIMIT_CALLS = 5
COMMON_LIMIT_PERIOD = 60
LOGIN_LIMIT_CALLS = 1
LOGIN_LIMIT_PERIOD = 15
LOGIN_LIMIT_MAX = 900
LOGIN_LIMIT_429 = 60
LOGIN_LIMIT_500 = 60
REFRESH_LIMIT_CALLS = 1
REFRESH_LIMIT_PERIOD = 15
REFRESH_LIMIT_MAX = 900
REFRESH_LIMIT_429 = 60
REFRESH_LIMIT_500 = 60
API_HTTP_ROUTE = True
API_TIMEOUT = 15
# WebSocket keepalive. A ping is sent every WS_PING_INTERVAL seconds; if no pong is
# received within WS_PING_TIMEOUT seconds the connection is treated as dead and torn
# down so the background thread can reconnect. Without a ping timeout a half-open
# (silently dead) connection is never detected, and the first command after a long
# idle is buffered into the dead socket and lost. WS_PING_TIMEOUT must be < WS_PING_INTERVAL.
WS_PING_INTERVAL = 10
WS_PING_TIMEOUT = 5
# After a successful send() we wait this long and re-check the socket: a half-open
# connection can close a few ms after send() returned (no exception raised), silently
# losing the command. If the socket is down by then we raise to trigger the retry/resend.
WS_POST_SEND_CHECK = 0.06
# After a device rejects a command (command_response with errNo != 0) we re-pull its
# real state over REST: set_* applied the value optimistically, so HA would otherwise
# keep showing a state the device refused. The delay lets the cloud settle first and
# coalesces a burst of rejections (e.g. a group command) into a single refresh.
COMMAND_REJECT_REFRESH_DELAY = 2.0
# How long after actually sending a command an incoming status update is still correlated
# with it — used both to log confirmations and to SUPPRESS a stale pre-command snapshot that
# would otherwise revert the optimistic value ("rolls back to off"). Must comfortably exceed
# the worst-case reconnect + cloud-attach + ack latency: observed up to ~15s (first inbound
# ~11s after a stale-session reconnect, ack ~15s), so 15s was dangerously tight. The window
# is measured from the REAL send time (see _touch_sent), not from the CMD OUT log line which
# precedes a possible multi-second reconnect.
WS_CMD_CORRELATION_WINDOW = 45.0
# "Zombie" WS session guard: ping/pong keeps the TCP connection alive, but the server can
# silently stop delivering application messages (observed: a 10.7h-old session with zero
# inbound messages — the first command sent into it was lost). If nothing has been received
# for this long, the session is torn down and re-established before sending a command.
WS_SESSION_STALE_TIMEOUT = 30 * 60
# How often the background heartbeat runs (see __init__.async_setup_entry): it reconnects a
# stale/zombie WS session and re-pulls device state over REST, so a device the user only
# observes (e.g. an idle washing machine) does not silently freeze on stale realtime data.
WS_HEARTBEAT_INTERVAL = 5 * 60
# On (re)connect the cloud pushes a full status snapshot on its own; if it has not arrived
# within this many seconds after the first connect, the device state is refreshed over REST
# instead. (Previously a target-temperature command was sent to force the snapshot — but any
# operation command makes an AC beep, and the snapshot arrives regardless of it.)
WS_INIT_SNAPSHOT_TIMEOUT = 10.0
# A status message carrying at least this many properties is treated as a full snapshot
# (incremental updates carry 1-2 properties, full snapshots ~30).
WS_FULL_SNAPSHOT_MIN_PROPS = 10
# Minimum interval (sec) between data refreshes triggered by GET /api/haier_evo requests.
# Protects against a flood of REST requests to Haier when the endpoint is polled often. 0 — refresh every time.
API_REFRESH_TTL = 5
API_PATH = "https://evo.haieronline.ru"
API_LOGIN = "v2/{region}/users/auth/sign-in"
API_TOKEN_REFRESH = "v2/{region}/users/auth/refresh"
API_DEVICES = "v2/{region}/pages/sduiRawPaginated/smartHome?part=1&partitionWeight=6"
API_STATUS = "https://iot-platform.evo.haieronline.ru/mobile-backend-service/api/v1/config/{mac}?type=DETAILED"
API_WS_PATH = "wss://iot-platform.evo.haieronline.ru/gateway-ws-service/ws/"
