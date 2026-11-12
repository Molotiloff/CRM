"""Bounded Rapira history and minute-candle aggregation; no Telegram dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

FRAMES = (5, 15, 60)


@dataclass(frozen=True, slots=True)
class Candle:
    time: int  # UTC seconds, not the millisecond time used by Rapira
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    @classmethod
    def parse(cls, row: list[Any]) -> Candle:
        if len(row) != 6:
            raise ValueError("Invalid Rapira candle length")
        try:
            timestamp = Decimal(str(row[0])) / 1000
            values = [Decimal(str(value)) for value in row[1:]]
            if not timestamp.is_finite() or timestamp <= 0 or timestamp != int(timestamp):
                raise ValueError("Invalid candle timestamp")
            if (
                any(not value.is_finite() for value in values)
                or min(values[:4]) <= 0
                or values[4] < 0
            ):
                raise ValueError("Invalid candle values")
            opened, high, low, close, volume = values
            if low > min(opened, close) or high < max(opened, close) or low > high:
                raise ValueError("Invalid candle OHLC")
            return cls(int(timestamp), opened, high, low, close, volume)
        except (InvalidOperation, TypeError, OverflowError) as exc:
            raise ValueError("Invalid Rapira candle") from exc


class CandleStore:
    LIMIT = 256
    MINUTE_LIMIT = 180

    def __init__(self) -> None:
        self.history: dict[int, dict[int, Candle]] = {frame: {} for frame in FRAMES}
        self.minutes: dict[int, Candle] = {}
        self.observed_at: dict[int, int] = {}
        self.last_tick = 0

    @staticmethod
    def _trim(rows: dict[int, Candle], limit: int) -> None:
        for timestamp in sorted(rows)[:-limit]:
            del rows[timestamp]

    def replace(self, frame: int, rows: list[Any], *, observed_at: int) -> None:
        if frame not in (*FRAMES, 1):
            raise ValueError("Unsupported interval")
        parsed = [Candle.parse(row) for row in rows]
        if not parsed:
            raise ValueError("Empty candle history")
        target = self.minutes if frame == 1 else self.history[frame]
        # A WS update can arrive while REST is in flight. Keep newer incomplete
        # candles, but let authoritative history repair older gaps/corrections.
        newest = max(item.time for item in parsed)
        for candle in parsed:
            previous = target.get(candle.time)
            if candle.time == newest and previous and previous.volume > candle.volume:
                continue
            target[candle.time] = candle
        self._trim(target, self.MINUTE_LIMIT if frame == 1 else self.LIMIT)
        self.observed_at[frame] = observed_at

    def on_minute(self, payload: dict) -> None:
        if payload.get("symbol") not in {"USDT_RUB", "USDT/RUB"} or payload.get("period") != "1min":
            return
        candle = Candle.parse(
            [
                payload.get(key)
                for key in (
                    "time",
                    "openPrice",
                    "highestPrice",
                    "lowestPrice",
                    "closePrice",
                    "volume",
                )
            ]
        )
        previous = self.minutes.get(candle.time)
        if previous is None or candle.volume >= previous.volume:
            self.minutes[candle.time] = candle
            self.last_tick = max(self.last_tick, candle.time)
        self._trim(self.minutes, self.MINUTE_LIMIT)

    def window(self, frame: int) -> list[Candle]:
        if frame not in FRAMES:
            raise ValueError("Unsupported interval")
        result = dict(self.history[frame])
        groups: dict[int, list[Candle]] = {}
        for candle in sorted(self.minutes.values(), key=lambda item: item.time):
            bucket = candle.time // (frame * 60) * (frame * 60)
            groups.setdefault(bucket, []).append(candle)
        first = min(self.minutes, default=0)
        for bucket, rows in groups.items():
            if bucket < first:
                continue  # first cached bucket is partial; use its REST history
            result[bucket] = Candle(
                bucket,
                rows[0].open,
                max(item.high for item in rows),
                min(item.low for item in rows),
                rows[-1].close,
                sum((item.volume for item in rows), Decimal(0)),
            )
        return [result[key] for key in sorted(result)[-self.LIMIT :]]
