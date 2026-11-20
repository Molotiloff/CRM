# Telegram transport recovery

BotApp uses ResilientTelegramSession with one polling consumer. The usual
aiogram getUpdates backoff and offset handling remain unchanged. Only polling
TelegramNetworkError contributes to recovery: at least three failures, spanning
at least 120 seconds since the first failure. A successful poll resets that
window. Rotation is rate-limited to once per 120 seconds.

Rotation creates a fresh aiohttp connection pool and DNS cache. API URL (including
the test environment), JSON codecs and timeout are retained. Existing sends and
downloads drain on their original transport, which closes when its active count
reaches zero. At most three generations can coexist, avoiding unbounded resource
growth when an old request is slow. All generations close at bot shutdown.

This does not fix an external Telegram/network outage. It avoids indefinitely
reusing a potentially unhealthy connection pool; it never starts another poller,
clears offsets, drops pending updates or re-executes financial commands.

TelegramResponseRetryMiddleware retries explicit TelegramRetryAfter rejections
for sendMessage/editMessageText/editMessageCaption/editMessageReplyMarkup only.
There are at most two retries, with <=30 seconds per wait and <=45 seconds total
sleep. Uploads, callback answers, payments and widget methods keep their existing
policies. Network timeouts and server errors are never replayed by this layer:
the original request may already have succeeded. Cancellation interrupts waits.

Only the outgoing API request is repeated, not the command handler or database
transaction. Outbound archive middleware records the successful result once.
Long/exhausted flood limits propagate to the caller rather than blocking forever.
Limits exceeding this budget still require the caller's existing error handling.

Logs contain method names, chat IDs, counters and delays, not tokens or request
payloads. Search journalctl for "Recovering Telegram HTTP transport",
"Telegram polling recovered" or "Retrying Telegram response".

No migration or environment changes are required; restart the backend after
deploying. Unit tests use fake transports and do not contact Telegram.
