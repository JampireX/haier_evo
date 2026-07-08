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
# Diagnostics: how long after sending a command an incoming status update is still
# correlated with it (to detect confirmations vs. cloud-side rollbacks/races).
WS_CMD_CORRELATION_WINDOW = 15.0
# "Zombie" WS session guard: ping/pong keeps the TCP connection alive, but the server can
# silently stop delivering application messages (observed: a 10.7h-old session with zero
# inbound messages — the first command sent into it was lost). If nothing has been received
# for this long, the session is torn down and re-established before sending a command.
WS_SESSION_STALE_TIMEOUT = 30 * 60
# Minimum interval (sec) between data refreshes triggered by GET /api/haier_evo requests.
# Protects against a flood of REST requests to Haier when the endpoint is polled often. 0 — refresh every time.
API_REFRESH_TTL = 5
API_PATH = "https://evo.haieronline.ru"
API_LOGIN = "v2/{region}/users/auth/sign-in"
API_TOKEN_REFRESH = "v2/{region}/users/auth/refresh"
API_DEVICES = "v2/{region}/pages/sduiRawPaginated/smartHome?part=1&partitionWeight=6"
API_STATUS = "https://iot-platform.evo.haieronline.ru/mobile-backend-service/api/v1/config/{mac}?type=DETAILED"
API_WS_PATH = "wss://iot-platform.evo.haieronline.ru/gateway-ws-service/ws/"
