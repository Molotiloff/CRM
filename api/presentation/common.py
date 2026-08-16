from __future__ import annotations

from decimal import Decimal
from typing import Any


def decimal_value(value: Any) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal(0)


def float_value(value: Any) -> float:
    return float(decimal_value(value))


def client_number(client_id: int) -> str:
    return f"CRM-{client_id:05d}"


def initials(name: str) -> str:
    parts = [part for part in name.replace("_", " ").split() if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return "".join(part[:1].upper() for part in parts[:2])


def tone(value: Decimal) -> str:
    if value > 0:
        return "green"
    if value < 0:
        return "red"
    return "neutral"
