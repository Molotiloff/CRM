"""Small, versioned display-only candle payload within Telegram's 1024-byte cap."""

from __future__ import annotations

import base64
import json
import struct
from pathlib import Path
from typing import Any

from .candles import Candle

HTML_PATH = Path(__file__).with_name("assets") / "rapira_widget.html"


def widget_state(
    candles: list[Candle], *, frame: int, revision: int, history_at: int, book_at: int
) -> str:
    # Exact 0.01 RUB OHLC ticks; volume is Float32 for visual bar heights only,
    # never for a wallet, a quote calculation, or the exact orderbook caption.
    rows = candles[-48:]
    while rows:
        base = min(int(item.low * 100) for item in rows)
        packed = bytearray()
        try:
            for item in rows:
                prices = [
                    int(value * 100) - base
                    for value in (item.open, item.high, item.low, item.close)
                ]
                packed.extend(struct.pack("<I4Hf", item.time, *prices, float(item.volume)))
        except (struct.error, OverflowError):
            rows = rows[1:]
            continue
        state: dict[str, Any] = {
            "v": 1,
            "r": revision,
            "f": frame,
            "p": base,
            "d": base64.b64encode(packed).decode("ascii"),
            "h": history_at,
            "u": book_at,
            "c": rows[-1].time,
        }
        text = json.dumps(state, separators=(",", ":"))
        if len(text.encode()) <= 1024:
            return text
        rows = rows[1:]
    return json.dumps(
        {"v": 1, "r": revision, "f": frame, "p": 0, "d": "", "h": history_at, "u": book_at},
        separators=(",", ":"),
    )
