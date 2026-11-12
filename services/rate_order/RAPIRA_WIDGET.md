# Native Telegram widget for /рапред

`/рапред` in a private chat with the bot sends the manager/admin a native HTML
widget, not an image. The candlestick chart has 5m / 15m / 1h switches, a last
close price, volume bars and pointer OHLC inspection. Full orderbooks from the
existing OrderbookService appear below it in the caption. Above the 1024 UTF-16
caption limit, complete text continues in linked plain-text messages; no levels
are silently dropped. This is a small chart, not an embedded TradingView.

Telegram production currently supports widgets in private chats only. Groups
keep the existing text-only `/рапред`; no image fallback is used. To test widgets
in a group, use a **separate Telegram test bot and separate database/environment**:

```
TELEGRAM_TEST_ENVIRONMENT=true
RAPIRA_WIDGET_GROUPS_ENABLED=true
```

The first flag explicitly routes the existing Bot session to Telegram's `/test/`
API. Both flags default to false. Do not set them on the production service.
Group widgets remain restricted to configured admin chats; private widgets and
their requests require manager/admin access. Old replaced widget requests are
rejected, and terminal wallets/payments are not part of this implementation.

The pinned aiogram lacks Bot API 10.4 widget classes. WidgetApi supplies small
TelegramMethod models using the same Bot/session/middleware. Update's supported
`extra=allow` preserves widget_query; an outer middleware handles it before the
old dispatcher tries to infer the update type. Polling explicitly requests that
update type. There is no second getUpdates consumer or global SDK monkey patch.

Data sources verified from the supplied HAR:
- Anonymous `https://api.rapira.net/market/history`: symbol=USDT/RUB,
  resolution=1/5/15/60, from/to in milliseconds, barsMaxSize=256.
- Existing market WS plus `spot@public.kline@USDT_RUB` (`kline`, period=1min).
- History OHLCV arrays: [time_ms, open, high, low, close, volume].

HTTP snapshots refresh every 30 seconds while there are active widgets; WS
minute candles replace same-minute updates rather than summing repeated volumes.
Partial first cached buckets retain authoritative aggregate history. Cache caps:
256 aggregate candles per interval, 180 minute candles, 32 active chat bindings,
256 KiB per REST response. No candle or image files are written. Float32 volume
is used only for visual bar heights; actual money/orderbooks continue using
Decimal. OHLC display ticks are 0.01 RUB. A dynamically shortened packed candle
window fits Telegram's 1024-byte state cap. HTML is checked against 32 KiB raw /
4 KiB zlib level 9; external scripts, network and iframes are absent.

One supervised publisher edits widget state and caption about every 10 seconds,
with a minimum 5-second interval for user-triggered updates and RetryAfter
backoff. Button queries are acknowledged before waiting for the publisher.
Missing/blocked messages are unsubscribed. Missing overflow messages are recreated.
Bindings, interval and overflow IDs reuse `live_messages` under `rapira_widget_v1:`
and restore on restart. No new database migration is required. Existing unrelated
live orderbook messages are not overwritten. The owned HTTP client closes on
shutdown/cancellation; Telegram's HTTP session stays owned by BotApp.

Freshness uses actual received market timestamps and UTC candle timestamps;
display times explicitly use Europe/Moscow, not the server's local timezone.
Widget HTML is immutable: new template versions require a new `/рапред` message.
Tests cover codec limits, aggregation, permissions, real dispatcher compatibility,
failure/backoff, cache/response bounds, persistence and shutdown. Browser preview
is not certification in the Telegram widget profile: validate once on a supported
Telegram client after deployment.

References: https://core.telegram.org/bots/widgets and
https://core.telegram.org/bots/api#sendwidget
