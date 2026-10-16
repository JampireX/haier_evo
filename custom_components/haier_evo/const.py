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
# The cloud answers every command with a command_response (same trace, errNo) within ~1s — the
# Evo app's own traffic shows the same. No answer within this many seconds means the command was
# lost (typically in a session the server no longer serves, e.g. opened with a since-revoked
# token): the session is re-established and the command resent once. A session that has not
# delivered anything yet (the cloud is still attaching it) gets one more period first.
WS_CMD_ACK_TIMEOUT = 10.0
# Right after a session opens the cloud pushes a full status snapshot (observed within ~1-11s).
# A session nothing at all has arrived over for this long is accepted but not served — it is
# reported in the log (see Haier._check_silent_session).
WS_SILENT_SESSION_TIMEOUT = 60.0
# Reconnect backoff: a session that failed to open, or closed within WS_SESSION_OK_AFTER seconds
# without delivering anything, makes the next attempt wait 5 s, 10 s, 20 s ... up to this many
# seconds (on top of the 5 s connect rate limit). Sessions we end ourselves do not count.
WS_RECONNECT_BACKOFF_MAX = 300
WS_SESSION_OK_AFTER = 60
# A REST path that answers 401 even with a freshly refreshed token is not refused because of the
# token: for this many seconds its 401s no longer force a token refresh and a websocket restart.
NON_TOKEN_401_TTL = 3600
# "Zombie" WS session guard: ping/pong keeps the TCP connection alive, but the server can
# silently stop delivering application messages (observed: a 10.7h-old session with zero
# inbound messages — the first command sent into it was lost). If nothing has been received
# for this long, the session is torn down and re-established before sending a command.
WS_SESSION_STALE_TIMEOUT = 30 * 60
# How often the background heartbeat runs (see __init__.async_setup_entry): it reconnects a
# stale/zombie WS session and re-pulls device state over REST, so a device the user only
# observes (e.g. an idle washing machine) does not silently freeze on stale realtime data.
WS_HEARTBEAT_INTERVAL = 5 * 60
# A device whose REST config failed during setup is retried by the heartbeat: every tick at
# first, then with a doubling interval capped at this many seconds.
PENDING_DEVICE_RETRY_MAX = 60 * 60
# On (re)connect the cloud pushes a full status snapshot on its own; if it has not arrived
# within this many seconds after the first connect, the device state is refreshed over REST
# instead. (Previously a target-temperature command was sent to force the snapshot — but any
# operation command makes an AC beep, and the snapshot arrives regardless of it.)
WS_INIT_SNAPSHOT_TIMEOUT = 10.0
# A status message carrying at least this many properties is treated as a full snapshot
# (incremental updates carry 1-2 properties, full snapshots ~30).
WS_FULL_SNAPSHOT_MIN_PROPS = 10
# The WS gateway rejects an invalid/revoked access token by accepting the upgrade (101) and then
# closing with this code ("Auth token not valid!"). REST endpoints answer 401 instead.
WS_CLOSE_AUTH_FAILED = 1007
# A token the server rejects is expired locally to force a refresh (see Haier.invalidate_token),
# unless it was obtained less than this many seconds ago: a server that rejects even fresh tokens
# must not drive a tight refresh loop (at most one forced refresh per this interval).
TOKEN_REJECT_MIN_AGE = 60
# Washing machines report the running program as codes: attribute 90 tells apart the "evo"
# programs (they all share 0 == 45), attribute 0 the others. They are matched against the
# program definitions (businessAttributes), the more specific attribute first.
WM_PROGRAM_ID_ATTRS = ("90", "0")
WM_PROGRAM_ATTR = "0"
WM_SUBPROGRAM_ATTR = "90"
# Minimum interval (sec) between data refreshes triggered by GET /api/haier_evo requests.
# Protects against a flood of REST requests to Haier when the endpoint is polled often. 0 — refresh every time.
API_REFRESH_TTL = 5
API_PATH = "https://evo.haieronline.ru"
API_LOGIN = "v2/{region}/users/auth/sign-in"
API_TOKEN_REFRESH = "v2/{region}/users/auth/refresh"
API_DEVICES = "v2/{region}/pages/sduiRawPaginated/smartHome?part=1&partitionWeight=6"
# Component id of the device list on that page (preferred when several "deviceList" components exist).
DEVICE_LIST_COMPONENT_ID = "72a6d224-cb66-4e6d-b427-2e4609252684"
API_STATUS = "https://iot-platform.evo.haieronline.ru/mobile-backend-service/api/v1/config/{mac}?type=DETAILED"
API_WS_PATH = "wss://iot-platform.evo.haieronline.ru/gateway-ws-service/ws/"
